"""Opt-in tests against live external APIs — skipped by default.

Run explicitly with:

    python3 -m pytest -m integration tests/test_integration_live.py

These validate the two real-world motivating cases end-to-end (real RCSB
metadata, real Unpaywall/OpenAlex/Crossref cascade) rather than fixtures.
"""
import os

import pytest

from data_fetch import get_pdb_data
from graph.analysis.oa_resolver import resolve_oa_pdf

pytestmark = pytest.mark.integration


def _doi_for(pdb_id: str) -> str:
    entry = get_pdb_data(pdb_id)
    citation = entry.get("rcsb_primary_citation", {})
    return citation.get("pdbx_database_id_DOI", "N/A")


def test_1oil_live_has_doi_and_resolves():
    doi = _doi_for("1OIL")
    assert doi != "N/A", "1OIL is expected to have a citation DOI"

    result = resolve_oa_pdf(doi, email=os.getenv("UNPAYWALL_EMAIL", ""))

    assert result.status in ("found", "not_found")
    assert result.status != "no_doi"


def test_7r2x_live_has_no_doi():
    doi = _doi_for("7R2X")
    assert doi == "N/A", "7R2X's citation is expected to have no DOI (status: To be published)"

    result = resolve_oa_pdf(doi, email=os.getenv("UNPAYWALL_EMAIL", ""))

    assert result.status == "no_doi"
    assert result.text is None
