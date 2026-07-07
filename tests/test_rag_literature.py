import sys
from unittest.mock import MagicMock

from graph.analysis.oa_resolver import PaperResolution
from graph.analysis.rag_literature import rag_literature

# graph/analysis/__init__.py does
# `from graph.analysis.rag_literature import rag_literature`, which rebinds the
# `rag_literature` attribute on the `graph.analysis` package to the *function*
# of the same name, shadowing the submodule. Both `import graph.analysis.rag_literature`
# and dotted-string monkeypatch targets resolve through that shadowed attribute
# chain, so pull the real module straight out of sys.modules instead.
rag_literature_module = sys.modules["graph.analysis.rag_literature"]

# ── Real RCSB fixtures (data.rcsb.org/rest/v1/core/entry/{id}) ───────────────

PDB_ENTRY_1OIL = {
    "struct": {"title": "STRUCTURE OF LIPASE"},
    "rcsb_primary_citation": {
        "title": (
            "The crystal structure of a triacylglycerol lipase from Pseudomonas "
            "cepacia reveals a highly open conformation in the absence of a "
            "bound inhibitor."
        ),
        "pdbx_database_id_DOI": "10.1016/S0969-2126(97)00177-9",
        "rcsb_journal_abbrev": "Structure",
        "rcsb_authors": ["Kim, K.K.", "Song, H.K.", "Shin, D.H.", "Hwang, K.Y.", "Suh, S.W."],
    },
}

# 7R2X: citation is present (title/authors) but has no DOI — "To be published" —
# distinct from a PDB entry with no citation at all.
PDB_ENTRY_7R2X = {
    "struct": {"title": "Paradendryphiella salina PL8 mannuronate-specific alginate lyase"},
    "rcsb_primary_citation": {
        "title": "Paradendryphiella salina PL8 mannuronate-specific alginate lyase",
        "rcsb_journal_abbrev": "To be published",
        "rcsb_authors": ["Fredslung, F.", "Welner, D.H.", "Wilkens, C."],
        # no "pdbx_database_id_DOI" key
    },
}


def _base_state(pdb_id: str, pdb_entry: dict) -> dict:
    return {
        "pdb_id": pdb_id,
        "pdb_entry": pdb_entry,
        "uniprot_features": {},
        "uniprot_id": None,
        "gpt_summary": {},
        "af_result": None,
        "structure_source": "experimental",
        "raw_prompt": "",
    }


def _tabs_artifact(artifacts):
    return next(a for a in artifacts if a["type"] == "tabs")


def _callouts(artifacts):
    return [a for a in artifacts if a["type"] == "callout"]


def test_1oil_has_doi_found(monkeypatch):
    monkeypatch.setattr(
        rag_literature_module,
        "resolve_oa_pdf",
        lambda doi, email: PaperResolution(text="full paper text " * 30, source="unpaywall", status="found"),
    )
    lit_chain = MagicMock(return_value="This lipase adopts an open conformation upon activation.")
    annotation_chain = MagicMock()
    monkeypatch.setattr(rag_literature_module, "literature_qa_chain", MagicMock(invoke=lit_chain))
    monkeypatch.setattr(rag_literature_module, "annotation_qa_chain", MagicMock(invoke=annotation_chain))

    result = rag_literature(_base_state("1OIL", PDB_ENTRY_1OIL))

    assert result["paper_retrieval_status"] == "found"
    assert result["paper_source"] == "unpaywall"

    callouts = _callouts(result["artifacts"])
    assert len(callouts) == 1
    assert callouts[0]["level"] == "success"
    assert "unpaywall" in callouts[0]["text"]
    assert "10.1016/S0969-2126(97)00177-9" in callouts[0]["text"]

    lit_chain.assert_called_once()
    annotation_chain.assert_not_called()


def test_7r2x_no_doi_triggers_fallback(monkeypatch):
    # Deliberately do NOT mock resolve_oa_pdf here: the 7R2X fixture's citation has
    # no `pdbx_database_id_DOI` key, so `doi` resolves to "N/A" and the *real*
    # resolve_oa_pdf short-circuits to status="no_doi" with zero network calls
    # (already covered by test_oa_resolver.py::test_no_doi_short_circuits_...).
    # This exercises the real integration between the node and the resolver's
    # short-circuit, rather than re-asserting the resolver's own internals.
    lit_chain = MagicMock()
    annotation_chain = MagicMock(return_value="Based on available annotations, this is an alginate lyase.")
    monkeypatch.setattr(rag_literature_module, "literature_qa_chain", MagicMock(invoke=lit_chain))
    monkeypatch.setattr(rag_literature_module, "annotation_qa_chain", MagicMock(invoke=annotation_chain))

    result = rag_literature(_base_state("7R2X", PDB_ENTRY_7R2X))

    assert result["paper_retrieval_status"] == "no_doi"
    assert result["paper_source"] is None

    callouts = _callouts(result["artifacts"])
    assert len(callouts) == 1
    assert callouts[0]["type"] == "callout"
    assert callouts[0]["level"] == "warning"
    assert "No associated publication DOI" in callouts[0]["text"]

    lit_chain.assert_not_called()
    annotation_chain.assert_called_once()

    # The warning must be a distinct structured artifact, not just markdown prose.
    tabs = _tabs_artifact(result["artifacts"])
    lit_tab = next(t for t in tabs["tabs"] if t["label"] == "Literature & Catalysis")
    assert not any(item["type"] == "callout" for item in lit_tab["content"])


def test_1oil_doi_present_all_sources_fail(monkeypatch):
    monkeypatch.setattr(
        rag_literature_module,
        "resolve_oa_pdf",
        lambda doi, email: PaperResolution(text=None, source=None, status="not_found"),
    )
    lit_chain = MagicMock()
    annotation_chain = MagicMock(return_value="Based on available annotations, this is a lipase.")
    monkeypatch.setattr(rag_literature_module, "literature_qa_chain", MagicMock(invoke=lit_chain))
    monkeypatch.setattr(rag_literature_module, "annotation_qa_chain", MagicMock(invoke=annotation_chain))

    result = rag_literature(_base_state("1OIL", PDB_ENTRY_1OIL))

    assert result["paper_retrieval_status"] == "not_found"
    assert result["paper_source"] is None

    callouts = _callouts(result["artifacts"])
    assert callouts[0]["level"] == "warning"
    assert "could not be retrieved" in callouts[0]["text"]

    lit_chain.assert_not_called()
    annotation_chain.assert_called_once()

    # This is the exact ambiguity the redundancy/flagging work resolves: "not_found"
    # (DOI exists, fetch failed) must be distinguishable from "no_doi" (no DOI at all).
    assert result["paper_retrieval_status"] != "no_doi"
