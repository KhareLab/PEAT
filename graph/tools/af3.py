from langchain_core.tools import tool

from af3_tools import run_af3_monomer as _run_af3_monomer

@tool
def run_af3_monomer(target_name: str, mpnn_fasta_path: str, cfg: dict | None = None) -> dict:
    """Run AF3 monomer prediction on Amarel."""
    return _run_af3_monomer(target_name, mpnn_fasta_path, cfg)

    

