from langchain_core.tools import tool

from data_fetch import get_pdb_data as _get_pdb_data
from data_fetch import get_uniprot_ids_from_sifts as _get_uniprot_ids_from_sifts
from data_fetch import fetch_uniprot_features as _fetch_uniprot_features
from data_fetch import get_m_csa_active_sites as _get_m_csa_active_sites
from data_fetch import get_pdb_id_from_sequence as _get_pdb_id_from_sequence
from data_fetch import get_unpaywall_data as _get_unpaywall_data
from data_fetch import fetch_pdf_text as _fetch_pdf_text


@tool
def get_pdb_data(pdb_id: str) -> dict:
    """Fetch PDB entry metadata including citation and structure details from RCSB."""
    return _get_pdb_data(pdb_id)


@tool
def get_uniprot_ids_from_sifts(pdb_id: str) -> list:
    """Map a PDB ID to its corresponding UniProt IDs using the SIFTS database."""
    return _get_uniprot_ids_from_sifts(pdb_id)


@tool
def fetch_uniprot_features(uniprot_id: str) -> dict:
    """Fetch UniProt features, functional annotations, and comments for a protein."""
    return _fetch_uniprot_features(uniprot_id)


@tool
def get_m_csa_active_sites(pdb_id: str) -> list:
    """Retrieve catalytic active site annotations from the M-CSA database."""
    return _get_m_csa_active_sites(pdb_id)


@tool
def get_pdb_id_from_sequence(sequence: str) -> str:
    """Find the best-matching PDB structure for a protein sequence via RCSB sequence search."""
    return _get_pdb_id_from_sequence(sequence)


@tool
def get_unpaywall_data(doi: str, email: str) -> dict:
    """Retrieve Unpaywall open-access metadata for a paper DOI."""
    return _get_unpaywall_data(doi, email)


@tool
def fetch_pdf_text(pdf_url: str) -> str:
    """Download a PDF and extract its text content (up to 10 000 characters)."""
    return _fetch_pdf_text(pdf_url)


SKILL_TESTS = [
    {
        "tool": "get_pdb_data",
        "input": {"pdb_id": "1TIM"},
        "assert_type": dict,
        "assert_key": "rcsb_id",
        "description": "returns RCSB metadata dict with rcsb_id key for 1TIM",
    },
    {
        "tool": "get_uniprot_ids_from_sifts",
        "input": {"pdb_id": "1TIM"},
        "assert_type": list,
        "assert_nonempty": True,
        "description": "returns non-empty list of UniProt IDs for 1TIM",
    },
    {
        "tool": "fetch_uniprot_features",
        "input": {"uniprot_id": "P00533"},
        "assert_type": dict,
        "description": "returns UniProt feature dict for EGFR (P00533)",
    },
    {
        "tool": "get_m_csa_active_sites",
        "input": {"pdb_id": "1TIM"},
        "assert_type": list,
        "description": "returns active site annotations for 1TIM",
    },
]
