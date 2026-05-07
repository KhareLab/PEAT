import uuid

import streamlit as st
from langchain_core.messages import HumanMessage

from predictors import predict_ddg_dynamut
from graph import build_graph
from graph.state import initial_state

# ── Graph singleton (module-level; one instance per Streamlit worker) ─────────
graph = build_graph()

# ── Node labels for status display ────────────────────────────────────────────
_NODE_LABELS = {
    "router":                "Routing…",
    "hpc_command":           "Running HPC command…",
    "hpc_minimize":          "Submitting energy minimization…",
    "hpc_check":             "Checking job status…",
    "hpc_download":          "Downloading results…",
    "alphafold":             "Fetching AlphaFold structure…",
    "foldseek":              "Running Foldseek search…",
    "blast_search":          "Searching RCSB by sequence…",
    "analysis":              "Analyzing protein…",
    "fetch_pdb_meta":        "Fetching PDB metadata…",
    "fetch_uniprot":         "Fetching UniProt annotations…",
    "fetch_structure":       "Downloading structure…",
    "fetch_active_sites":    "Fetching active site data…",
    "summarize_annotations": "Summarizing annotations…",
    "rag_literature":        "Retrieving literature…",
    "llm_qa":                "Thinking…",
    "format_response":       "Preparing response…",
}

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(layout="wide", page_title="PEAT – Protein Engineering Agent Toolkit")
st.title("🔬 PEAT – Protein Engineering Agent Toolkit")

# ── Session state ─────────────────────────────────────────────────────────────
if "thread_id" not in st.session_state:
    st.session_state.thread_id  = str(uuid.uuid4())
    st.session_state.chat_display = []
    # Seed the checkpointer with the initial state for this thread
    config = {"configurable": {"thread_id": st.session_state.thread_id}}
    graph.update_state(config, initial_state())


# ── Artifact renderer ─────────────────────────────────────────────────────────
def _render_artifact(artifact: dict) -> None:
    t = artifact["type"]
    if t == "html":
        st.components.v1.html(artifact["data"], height=550)
    elif t == "plotly":
        st.plotly_chart(artifact["data"], use_container_width=True)
    elif t == "code":
        st.code(artifact["data"], language=artifact.get("language", ""))
    elif t == "markdown":
        st.markdown(artifact["data"])
    elif t == "mutation_form":
        form_key = artifact.get("key", "mutate_form")
        with st.form(form_key):
            site      = st.text_input("Residue (e.g. A123)")
            mutation  = st.text_input("Mutation (e.g. A123C)")
            submitted = st.form_submit_button("Predict ΔΔG")
            if submitted:
                try:
                    chain  = site[0]
                    resnum = int(site[1:])
                    result = predict_ddg_dynamut("temp.pdb", chain, resnum, mutation)
                    st.success(f"Predicted ΔΔG: {result.get('ddg')} kCal/mol")
                except Exception as e:
                    st.error(f"Error: {e}")
    elif t == "expander":
        with st.expander(artifact["label"], expanded=artifact.get("expanded", False)):
            for sub in artifact["content"]:
                _render_artifact(sub)
    elif t == "tabs":
        tab_labels = [tab["label"] for tab in artifact["tabs"]]
        tab_objs   = st.tabs(tab_labels)
        for tab_obj, tab_def in zip(tab_objs, artifact["tabs"]):
            with tab_obj:
                for sub in tab_def["content"]:
                    _render_artifact(sub)


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.caption(
        "Type a PDB ID (e.g. `Analyze 6B5X`), a protein sequence (FASTA or plain AA), "
        "or an HPC command (e.g. `gmx mdrun -deffnm em`) directly in the chat below."
    )


# ── Render chat history ───────────────────────────────────────────────────────
for item in st.session_state.chat_display:
    with st.chat_message(item["role"]):
        if item.get("content"):
            st.markdown(item["content"])
        for artifact in item.get("artifacts", []):
            _render_artifact(artifact)


# ── Chat input loop ───────────────────────────────────────────────────────────
if prompt := st.chat_input("Ask about a protein (e.g. 'Analyze 6B5X') or run an HPC command…"):
    # Show user message immediately
    st.session_state.chat_display.append({"role": "user", "content": prompt, "artifacts": []})
    with st.chat_message("user"):
        st.markdown(prompt)

    config = {"configurable": {"thread_id": st.session_state.thread_id}}

    with st.chat_message("assistant"):
        with st.status("Working…", expanded=False) as status:
            for update in graph.stream(
                {"raw_prompt": prompt, "messages": [HumanMessage(content=prompt)]},
                config,
                stream_mode="updates",
            ):
                node = list(update.keys())[0]
                status.update(label=_NODE_LABELS.get(node, "Working…"))
            status.update(state="complete", expanded=False)

        # Read the assistant display item added by format_response
        final     = graph.get_state(config)
        all_items = final.values.get("chat_display", [])
        # The last item is the assistant response from this turn
        last = all_items[-1] if all_items else {}
        if last.get("content"):
            st.markdown(last["content"])
        for artifact in last.get("artifacts", []):
            _render_artifact(artifact)

    # Sync session state from graph (includes the assistant item added by format_response)
    st.session_state.chat_display = final.values.get("chat_display", [])
