from typing import Literal

from langgraph.types import Command

from graph.state import PEATState
from graph.tools.data import get_pdb_id_from_sequence


def blast_search(state: PEATState) -> Command[Literal["llm_qa", "analysis", "format_response"]]:
    """Find PDB ID from sequence, then route to analysis or llm_qa."""
    found_id = get_pdb_id_from_sequence.invoke({"sequence": state["sequence"]})

    if not found_id:
        return Command(
            update={"response_text": "No matching PDB found for the provided sequence."},
            goto="format_response",
        )

    analyzed = state.get("analyzed_pdb_ids") or []
    if found_id in analyzed:
        return Command(update={"pdb_id": found_id}, goto="llm_qa")

    return Command(update={"pdb_id": found_id}, goto="analysis")
