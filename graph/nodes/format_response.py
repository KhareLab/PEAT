from langchain_core.messages import AIMessage

from graph.state import PEATState


def format_response(state: PEATState) -> dict:
    """Package response_text + artifacts into messages/chat_display; clear scratch fields."""
    text      = state.get("response_text") or ""
    artifacts = state.get("artifacts") or []

    # Update analyzed_pdb_ids cache when a new PDB was just processed
    analyzed = list(state.get("analyzed_pdb_ids") or [])
    if state.get("intent") in ("analyze", "sequence") and state.get("pdb_id"):
        pdb_id = state["pdb_id"]
        if pdb_id not in analyzed:
            analyzed.append(pdb_id)

    return {
        "messages":     [AIMessage(content=text)],
        "chat_display": [{"role": "assistant", "content": text, "artifacts": artifacts}],
        "analyzed_pdb_ids": analyzed,
        # Clear per-turn scratch
        "response_text":    None,
        "artifacts":        [],
        "pdb_entry":        None,
        "uniprot_features": None,
        "m_csa_sites":      None,
        "paper_text":       None,
        "gpt_summary":      None,
        "structure_source": None,
        "af_result":        None,
    }
