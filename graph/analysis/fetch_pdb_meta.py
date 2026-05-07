from graph.state import PEATState
from graph.tools.data import get_pdb_data


def fetch_pdb_meta(state: PEATState) -> dict:
    pdb_id = state["pdb_id"].upper()
    entry  = get_pdb_data.invoke({"pdb_id": pdb_id})
    return {"pdb_entry": entry}
