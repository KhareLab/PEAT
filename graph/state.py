from typing import Annotated, Optional
from typing_extensions import TypedDict
import operator

from langchain_core.messages import BaseMessage, SystemMessage
from langgraph.graph.message import add_messages

_SYSTEM_PROMPT = (
    "You are PEAT, a protein engineering assistant for the KhareLab at Rutgers University. "
    "You help researchers analyze protein structures, interpret UniProt annotations, "
    "understand literature, and run GROMACS simulations on Amarel HPC. "
    "When referring to previously analyzed proteins, use the information from the conversation history."
)


class HpcJobInfo(TypedDict):
    pdb_id: str
    remote_dir: str


class PEATState(TypedDict):
    # Conversation
    messages: Annotated[list[BaseMessage], add_messages]
    chat_display: Annotated[list[dict], operator.add]

    # Routing — overwritten each turn by router node
    intent: str
    raw_prompt: str

    # Parsed routing params — only one set populated per turn
    pdb_id: Optional[str]
    uniprot_id: Optional[str]
    hpc_command: Optional[str]
    job_id: Optional[str]
    sequence: Optional[str]

    # Analysis pipeline scratch fields
    pdb_entry: Optional[dict]
    uniprot_features: Optional[dict]
    m_csa_sites: Optional[list]
    paper_text: Optional[str]
    gpt_summary: Optional[dict]
    structure_source: Optional[str]
    af_result: Optional[dict]

    # Cross-turn caches — overwritten in place; persisted by checkpointer
    analyzed_pdb_ids: list
    hpc_jobs: dict

    # Response accumulation — cleared by format_response after each turn
    response_text: Optional[str]
    artifacts: list


def initial_state() -> dict:
    return {
        "messages": [SystemMessage(content=_SYSTEM_PROMPT)],
        "chat_display": [],
        "intent": "",
        "raw_prompt": "",
        "pdb_id": None,
        "uniprot_id": None,
        "hpc_command": None,
        "job_id": None,
        "sequence": None,
        "pdb_entry": None,
        "uniprot_features": None,
        "m_csa_sites": None,
        "paper_text": None,
        "gpt_summary": None,
        "structure_source": None,
        "af_result": None,
        "analyzed_pdb_ids": [],
        "hpc_jobs": {},
        "response_text": None,
        "artifacts": [],
    }
