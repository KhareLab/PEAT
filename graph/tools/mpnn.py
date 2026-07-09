from langchain_core.tools import tool

from mpnn_tools import run_proteinmpnn as _run_proteinmpnn


@tool
def run_proteinmpnn(input_file: str, loc: str, cfg: dict = None, wait: bool = True) -> str:
    """Run ProteinMPNN on a PDB file and return the SLURM job ID."""
    if cfg is None:
        cfg = {}
    return _run_proteinmpnn(input_file, loc, cfg, wait)


