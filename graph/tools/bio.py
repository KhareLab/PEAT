from langchain_core.tools import tool

from bio_tools import foldseek_search as _foldseek_search
from bio_tools import alphafold_fetch as _alphafold_fetch


@tool
def foldseek_search(pdb_id: str) -> list:
    """Search for structurally similar proteins using Foldseek 3D structural alignment."""
    return _foldseek_search(pdb_id)


@tool
def alphafold_fetch(uniprot_id: str) -> dict:
    """Fetch an AlphaFold predicted structure for a protein by UniProt ID."""
    return _alphafold_fetch(uniprot_id)
