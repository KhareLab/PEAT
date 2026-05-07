import shutil

import requests

from graph.state import PEATState
from graph.tools.bio import alphafold_fetch


def fetch_structure(state: PEATState) -> dict:
    """Download experimental PDB; fall back to AlphaFold if unavailable."""
    pdb_id     = state["pdb_id"].upper()
    uniprot_id = state.get("uniprot_id")

    try:
        r = requests.get(f"https://files.rcsb.org/download/{pdb_id}.pdb", timeout=30)
        r.raise_for_status()
        with open("temp.pdb", "wb") as f:
            f.write(r.content)
        return {"structure_source": "experimental", "af_result": None}

    except Exception:
        if not uniprot_id:
            raise RuntimeError(
                f"Could not download experimental structure for {pdb_id} "
                "and no UniProt ID available for AlphaFold fallback."
            )
        af_result = alphafold_fetch.invoke({"uniprot_id": uniprot_id})
        shutil.copy(af_result["pdb_path"], "temp.pdb")
        return {"structure_source": "alphafold", "af_result": af_result}
