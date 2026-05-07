from graph.state import PEATState
from graph.tools.data import get_uniprot_ids_from_sifts, fetch_uniprot_features


def fetch_uniprot(state: PEATState) -> dict:
    pdb_id      = state["pdb_id"].upper()
    uniprot_ids = get_uniprot_ids_from_sifts.invoke({"pdb_id": pdb_id})

    if uniprot_ids:
        uniprot_id   = uniprot_ids[0]
        up_features  = fetch_uniprot_features.invoke({"uniprot_id": uniprot_id})
    else:
        uniprot_id  = None
        up_features = {"features": [], "comments": [], "proteinDescription": {}, "genes": []}

    return {"uniprot_id": uniprot_id, "uniprot_features": up_features}
