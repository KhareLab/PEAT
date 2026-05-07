from langgraph.graph import StateGraph, START, END
from langgraph.types import RetryPolicy

from graph.state import PEATState
from graph.analysis.fetch_pdb_meta import fetch_pdb_meta
from graph.analysis.fetch_uniprot import fetch_uniprot
from graph.analysis.fetch_structure import fetch_structure
from graph.analysis.fetch_active_sites import fetch_active_sites
from graph.analysis.summarize_annotations import summarize_annotations
from graph.analysis.rag_literature import rag_literature

_http_retry = RetryPolicy(max_attempts=3, initial_interval=1.0)


def build_analysis_subgraph():
    builder = StateGraph(PEATState)

    builder.add_node("fetch_pdb_meta",         fetch_pdb_meta,         retry_policy=_http_retry)
    builder.add_node("fetch_uniprot",           fetch_uniprot,          retry_policy=_http_retry)
    builder.add_node("fetch_structure",         fetch_structure)
    builder.add_node("fetch_active_sites",      fetch_active_sites,     retry_policy=_http_retry)
    builder.add_node("summarize_annotations",   summarize_annotations)
    builder.add_node("rag_literature",          rag_literature)

    builder.add_edge(START,                 "fetch_pdb_meta")
    builder.add_edge("fetch_pdb_meta",      "fetch_uniprot")
    builder.add_edge("fetch_uniprot",       "fetch_structure")
    builder.add_edge("fetch_structure",     "fetch_active_sites")
    builder.add_edge("fetch_active_sites",  "summarize_annotations")
    builder.add_edge("summarize_annotations", "rag_literature")
    builder.add_edge("rag_literature",      END)

    return builder.compile(checkpointer=False)
