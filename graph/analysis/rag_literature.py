import os

from structure_tools import build_3dmol_html
from ui import plot_domains

from graph.state import PEATState
from graph.chains import literature_qa_chain, annotation_qa_chain
from graph.analysis.oa_resolver import resolve_oa_pdf


# ── Main node ─────────────────────────────────────────────────────────────────

def rag_literature(state: PEATState) -> dict:
    pdb_id      = state["pdb_id"].upper()
    pdb_entry   = state.get("pdb_entry") or {}
    up_features = state.get("uniprot_features") or {}
    gpt_summary = state.get("gpt_summary") or {}
    af_result   = state.get("af_result")
    raw_prompt  = state.get("raw_prompt") or ""

    citation = pdb_entry.get("rcsb_primary_citation", {})
    doi      = citation.get("pdbx_database_id_DOI", "N/A")
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

    # Literature retrieval and Q&A — redundant OA-first cascade, never raises
    resolution        = resolve_oa_pdf(doi, email=os.getenv("UNPAYWALL_EMAIL", ""))
    paper_text        = resolution.text
    paper_source      = resolution.source
    retrieval_status  = resolution.status  # "found" | "not_found" | "no_doi"
    lit_answer            = None
    lit_answer_is_fallback = False

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
            "No associated paper DOI is available. "
            if retrieval_status == "no_doi" else
            "The full text of the associated paper could not be retrieved. "
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
        fig = plot_domains(up_features["features"], seq_len)
        tab2.append({"type": "plotly", "data": fig.to_json()})
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

    # ── Paper retrieval status callout — always visible, never buried in a tab ──
    if retrieval_status == "found":
        callout = {"type": "callout", "level": "success",
                   "text": f"Full-text paper retrieved via {paper_source} (DOI: {doi})."}
    elif retrieval_status == "not_found":
        callout = {"type": "callout", "level": "warning",
                   "text": ("A DOI was found but the full-text paper could not be retrieved from "
                            "Unpaywall, OpenAlex, or Crossref. Answering from PDB/UniProt annotations only.")}
    else:  # "no_doi"
        callout = {"type": "callout", "level": "warning",
                   "text": ("No associated publication DOI is available for this structure. "
                            "Answering from PDB/UniProt annotations only.")}

    artifacts = [
        callout,
        {"type": "tabs", "tabs": [
            {"label": "Literature & Catalysis", "content": tab1},
            {"label": "Sequence & Domains",      "content": tab2},
            {"label": "Mutations & Predictions", "content": tab3},
        ]},
        {"type": "markdown", "data": "### 3D Structure Viewer"},
        {"type": "html",     "data": build_3dmol_html(pdb_id)},
    ]

    return {
        "response_text": text_summary,
        "artifacts": artifacts,
        "paper_text": paper_text,
        "paper_retrieval_status": retrieval_status,
        "paper_source": paper_source,
    }
