from graph.state import PEATState
from graph.tools.data import get_m_csa_active_sites


def fetch_active_sites(state: PEATState) -> dict:
    sites = get_m_csa_active_sites.invoke({"pdb_id": state["pdb_id"].upper()})
    return {"m_csa_sites": sites}
