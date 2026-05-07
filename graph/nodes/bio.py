from structure_tools import build_3dmol_html

from graph.state import PEATState
from graph.tools.bio import foldseek_search, alphafold_fetch


def alphafold(state: PEATState) -> dict:
    uniprot_id = state["uniprot_id"]
    try:
        af = alphafold_fetch.invoke({"uniprot_id": uniprot_id})
        response = (
            f"### AlphaFold Structure — {uniprot_id}\n"
            f"- **Entry:** {af['entry_id']}\n"
            f"- **Gene:** {af['gene']} | **Organism:** {af['organism']}\n"
            f"- **Description:** {af['description']}\n"
            f"- **Mean pLDDT:** {af['plddt_mean']} "
            f"(min {af['plddt_min']}, max {af['plddt_max']})\n\n"
            "_pLDDT > 90: very high · 70–90: high · 50–70: low · < 50: very low_"
        )
        artifacts = [
            {"type": "markdown", "data": "### 3D Structure Viewer"},
            {"type": "html",     "data": build_3dmol_html(uniprot_id, pdb_path=af["pdb_path"])},
        ]
        return {"response_text": response, "artifacts": artifacts}
    except Exception as e:
        return {"response_text": f"AlphaFold fetch failed: {e}", "artifacts": []}


def foldseek(state: PEATState) -> dict:
    pdb_id = state["pdb_id"]
    try:
        hits = foldseek_search.invoke({"pdb_id": pdb_id})
        if hits:
            rows = [
                "| # | PDB/AF ID | Probability | E-value | Seq. Identity | Description |",
                "|---|-----------|-------------|---------|---------------|-------------|",
            ]
            for i, h in enumerate(hits, 1):
                prob = f"{h['prob']:.3f}"              if h.get("prob")              is not None else "—"
                ev   = f"{h['evalue']:.2e}"            if h.get("evalue")            is not None else "—"
                sid  = f"{h['sequence_identity']:.1%}" if h.get("sequence_identity") is not None else "—"
                rows.append(f"| {i} | `{h['pdb_id']}` | {prob} | {ev} | {sid} | {h['description']} |")
            response = f"### Foldseek — Top hits for {pdb_id}\n\n" + "\n".join(rows)
        else:
            response = f"Foldseek returned no hits for {pdb_id}."
        return {"response_text": response, "artifacts": [{"type": "markdown", "data": response}]}
    except Exception as e:
        return {"response_text": f"Foldseek search failed: {e}", "artifacts": []}
