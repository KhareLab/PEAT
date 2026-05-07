from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver

from graph.state import PEATState
from graph.router import router, route_by_intent
from graph.analysis import build_analysis_subgraph
from graph.nodes.hpc import hpc_command, hpc_minimize, hpc_check, hpc_download
from graph.nodes.bio import alphafold, foldseek
from graph.nodes.sequence import blast_search
from graph.nodes.llm_qa import llm_qa
from graph.nodes.format_response import format_response


def build_graph(db_path: str = "peat_state.db"):
    checkpointer = SqliteSaver.from_conn_string(db_path)
    checkpointer.setup()

    analysis = build_analysis_subgraph()

    builder = StateGraph(PEATState)

    builder.add_node("router",          router)
    builder.add_node("hpc_command",     hpc_command)
    builder.add_node("hpc_minimize",    hpc_minimize)
    builder.add_node("hpc_check",       hpc_check)
    builder.add_node("hpc_download",    hpc_download)
    builder.add_node("alphafold",       alphafold)
    builder.add_node("foldseek",        foldseek)
    builder.add_node("blast_search",    blast_search)
    builder.add_node("analysis",        analysis)
    builder.add_node("llm_qa",          llm_qa)
    builder.add_node("format_response", format_response)

    builder.add_edge(START, "router")
    builder.add_conditional_edges(
        "router",
        route_by_intent,
        {
            "hpc":       "hpc_command",
            "minimize":  "hpc_minimize",
            "check_job": "hpc_check",
            "download":  "hpc_download",
            "alphafold": "alphafold",
            "foldseek":  "foldseek",
            "analyze":   "analysis",
            "sequence":  "blast_search",
            "llm_qa":    "llm_qa",
        },
    )

    for node in ["hpc_command", "hpc_minimize", "hpc_check", "hpc_download",
                 "alphafold", "foldseek", "analysis", "llm_qa"]:
        builder.add_edge(node, "format_response")

    # blast_search uses Command for routing — no static edge needed
    builder.add_edge("format_response", END)

    return builder.compile(checkpointer=checkpointer)
