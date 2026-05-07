import os
import re

import requests

from data_fetch import get_unpaywall_data, fetch_pdf_text
from structure_tools import build_3dmol_html
from ui import plot_domains

from graph.state import PEATState
from graph.chains import literature_qa_chain, annotation_qa_chain


# ── Paper retrieval cascade ───────────────────────────────────────────────────

def _fetch_paper_text(doi: str) -> str | None:
    """Priority: Unpaywall → library cookie → Sci-Hub (dev only)."""
    email = os.getenv("UNPAYWALL_EMAIL", "")

    if email:
        ua_data = get_unpaywall_data(doi, email)
        if ua_data:
            best    = ua_data.get("best_oa_location") or {}
            pdf_url = best.get("url_for_pdf") or ua_data.get("doi_url")
            if pdf_url:
                text = fetch_pdf_text(pdf_url)
                if text and len(text) > 200:
                    return text

    lib_cookie = os.getenv("LIBRARY_COOKIE", "")
    if lib_cookie:
        try:
            import fitz, tempfile
            r = requests.get(
                f"https://doi.org/{doi}",
                headers={"Cookie": lib_cookie},
                allow_redirects=True, timeout=15,
            )
            if r.ok and "pdf" in r.headers.get("Content-Type", "").lower():
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
                    tf.write(r.content)
                doc  = fitz.open(tf.name)
                text = "".join(p.get_text() for p in doc)[:10000]
                if text and len(text) > 200:
                    return text
        except Exception:
            pass

    if os.getenv("SCIHUB_ENABLED", "false").lower() == "true":
        scihub_base = os.getenv("SCIHUB_URL", "https://sci-hub.se")
        try:
            r = requests.get(f"{scihub_base}/{doi}", timeout=20)
            if r.ok:
                match = re.search(r'src=["\']([^"\']*\.pdf[^"\']*)["\']', r.text)
                if match:
                    pdf_url = match.group(1)
                    if pdf_url.startswith("//"):
                        pdf_url = "https:" + pdf_url
                    text = fetch_pdf_text(pdf_url)
                    if text and len(text) > 200:
                        return text
        except Exception:
            pass

    return None


# ── Main node ─────────────────────────────────────────────────────────────────

def rag_literature(state: PEATState) -> dict:
    pdb_id      = state["pdb_id"].upper()
    pdb_entry   = state.get("pdb_entry") or {}
    up_features = state.get("uniprot_features") or {}
    gpt_summary = state.get("gpt_summary") or {}
    af_result   = state.get("af_result")
    raw_prompt  = state.get("raw_prompt") or ""

    citation = pdb_entry.get("rcsb_primary_citation", {})
    doi      = citation.get("pdbx_database_id_doi", "N/A")
    title    = citation.get("title", "N/A")
    authors  = citation.get("rcsb_authors", [])
    journal  = citation.get("rcsb_journal_abbrev", "N/A")

    # Rebuild annotation texts for fallback context (same logic as summarize_annotations)
    all_texts = []
    for comment in up_features.get("comments", []):
        if comment.get("texts"):
            for t in comment["texts"]:
                all_texts.append(t.get("value", ""))
    if not all_texts:
        struct_title = pdb_entry.get("struct", {}).get("title", "")
        if struct_title:
            all_texts.append(f"PDB structure title: {struct_title}")
        if title != "N/A":
            all_texts.append(f"Associated paper: {title}")

    # Protein identity
    prot_desc = up_features.get("proteinDescription", {})
    rec_name  = prot_desc.get("recommendedName", {})
    name = rec_name.get("fullName", {}).get("value", "")
    if not name:
        sub_names = prot_desc.get("submissionNames", [])
        name = (sub_names[0].get("fullName", {}).get("value", "") if sub_names else "") or "N/A"
    ec   = (rec_name.get("ecNumbers", [{}]) or [{}])[0].get("value", "N/A")
    gene = (
        up_features.get("genes", [{}])[0].get("geneName", {}).get("value", "N/A")
        if up_features.get("genes") else "N/A"
    )

    # Literature retrieval and Q&A
    paper_text            = None
    lit_answer            = None
    lit_answer_is_fallback = False

    if doi != "N/A":
        paper_text = _fetch_paper_text(doi)

    user_question = raw_prompt or f"What is the function and mechanism of {pdb_id}?"

    if paper_text:
        try:
            lit_answer = literature_qa_chain.invoke({
                "doi":        doi,
                "question":   user_question,
                "paper_text": paper_text,
            })
        except Exception:
            lit_answer = None

    if not lit_answer:
        lit_answer_is_fallback = True
        fallback_context_parts = [
            f"PDB ID: {pdb_id}",
            f"Paper title: {title}" if title != "N/A" else "",
            f"Journal: {journal}"   if journal != "N/A" else "",
            f"DOI: {doi}"           if doi != "N/A" else "",
        ] + all_texts
        fallback_context = "\n".join(p for p in fallback_context_parts if p)
        paper_note = (
            "The full text of the associated paper could not be retrieved. "
            if doi != "N/A" else
            "No associated paper DOI is available. "
        )
        try:
            lit_answer = annotation_qa_chain.invoke({
                "paper_note": paper_note,
                "question":   user_question,
                "context":    fallback_context,
            })
        except Exception:
            lit_answer = None

    # ── Text summary (goes into LLM message context) ─────────────────────────
    text_summary = (
        f"Analysis complete for **{pdb_id}**.\n"
        f"- **Protein:** {name} | **Gene:** {gene} | **EC:** {ec}\n"
        f"- **Paper:** _{title}_ ({journal}) — DOI: {doi}\n"
    )
    if state.get("structure_source") == "alphafold" and af_result:
        text_summary += (
            f"- **Structure:** AlphaFold model ({af_result['entry_id']}) — "
            f"no experimental PDB available. "
            f"Mean pLDDT: {af_result['plddt_mean']} "
            f"(min {af_result['plddt_min']}, max {af_result['plddt_max']})\n"
        )
    if gpt_summary.get("Function"):
        text_summary += "\n**Functional notes:**\n" + "\n".join(f"- {b}" for b in gpt_summary["Function"]) + "\n"
    if lit_answer:
        label = "Annotation-based answer" if lit_answer_is_fallback else "Literature answer"
        text_summary += f"\n**{label}:**\n{lit_answer[:400]}…\n"

    # ── Tab 1: Literature & Catalysis ────────────────────────────────────────
    tab1 = [{"type": "markdown", "data": (
        "### Paper Metadata\n"
        f"- **DOI:** {doi}\n"
        f"- **Title:** {title}\n"
        f"- **Authors:** {', '.join(authors)}\n"
        f"- **Journal:** {journal}"
    )}]
    if name != "N/A":
        tab1.append({"type": "markdown", "data": (
            "### UniProt Annotations\n"
            f"- **Protein:** {name}\n"
            f"- **EC Number:** {ec}\n"
            f"- **Gene:** {gene}"
        )})
    if gpt_summary.get("Function"):
        tab1.append({"type": "markdown", "data":
            "### Functional Roles\n" + "\n".join(f"- {b}" for b in gpt_summary["Function"])
        })
    if lit_answer:
        header = (
            "### Analysis _(paper unavailable — based on available annotations)_"
            if lit_answer_is_fallback else
            "### Literature Answer"
        )
        tab1.append({"type": "markdown", "data": f"{header}\n{lit_answer}"})
    else:
        tab1.append({"type": "markdown", "data":
            "_No answer could be generated — paper inaccessible and annotation context insufficient._"
        })

    # ── Tab 2: Sequence & Domains ─────────────────────────────────────────────
    uniprot_id = state.get("uniprot_id")
    tab2 = []
    if uniprot_id:
        tab2.append({"type": "markdown", "data":
            f"**UniProt:** [{uniprot_id}](https://www.uniprot.org/uniprotkb/{uniprot_id})"
        })
    if up_features.get("features"):
        seq_len = pdb_entry.get("rcsb_entry_info", {}).get("polymer_monomer_count_maximum", 500)
        tab2.append({"type": "plotly", "data": plot_domains(up_features["features"], seq_len)})
    if gpt_summary.get("Sequence"):
        tab2.append({"type": "markdown", "data":
            "### Sequence Features\n" + "\n".join(f"- {b}" for b in gpt_summary["Sequence"])
        })
    if af_result:
        tab2.append({"type": "markdown", "data": (
            "### AlphaFold Confidence (pLDDT)\n"
            f"- **Entry:** {af_result['entry_id']}\n"
            f"- **Mean pLDDT:** {af_result['plddt_mean']} "
            f"(min {af_result['plddt_min']}, max {af_result['plddt_max']})\n"
            "_pLDDT > 90: very high confidence · 70–90: high · 50–70: low · < 50: very low_"
        )})
    if not tab2:
        tab2.append({"type": "markdown", "data": "_No UniProt domain annotations found._"})

    # ── Tab 3: Mutations & Predictions ────────────────────────────────────────
    tab3 = [
        {"type": "markdown",      "data": "### Mutation ΔΔG Predictions"},
        {"type": "mutation_form", "data": None, "key": f"mutate_{pdb_id}"},
    ]

    artifacts = [
        {"type": "tabs", "tabs": [
            {"label": "Literature & Catalysis", "content": tab1},
            {"label": "Sequence & Domains",      "content": tab2},
            {"label": "Mutations & Predictions", "content": tab3},
        ]},
        {"type": "markdown", "data": "### 3D Structure Viewer"},
        {"type": "html",     "data": build_3dmol_html(pdb_id)},
    ]

    return {"response_text": text_summary, "artifacts": artifacts, "paper_text": paper_text}
