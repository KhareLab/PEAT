# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bash
streamlit run app.py
```

Import / syntax check without starting Streamlit:

```bash
python3 -c "from graph import build_graph; print('OK')"
```

There is no test suite yet. For now, verify routing logic in `graph/router.py` directly:

```bash
python3 -c "
from graph.router import router, parse_analyze_request, parse_foldseek_request
assert parse_analyze_request('6B5X') == '6B5X'
assert parse_foldseek_request('foldseek 6B5X') == '6B5X'
print('router OK')
"
```

## Architecture

PEAT is a Streamlit chatbot backed by a LangGraph `StateGraph`. `app.py` is UI-only (~130 lines); all agent logic lives in `graph/`.

### Data flow per turn

```
st.chat_input → graph.stream() → router node → action node → format_response node → st.session_state.chat_display
```

`graph.stream(stream_mode="updates")` is called with `{"raw_prompt": prompt, "messages": [HumanMessage(...)]}`. The graph checkpointer (`SqliteSaver`, file `peat_state.db`) merges this with the prior state for the thread, so `analyzed_pdb_ids` and `hpc_jobs` persist across app restarts. `thread_id` is a UUID stored in `st.session_state`, scoped to the browser session.

### Graph structure

```
START → router → [conditional]
                    ├── hpc / minimize / check_job / download / alphafold / foldseek / analyze / llm_qa
                    │       └── → format_response → END
                    └── sequence → blast_search → [Command] → analysis | llm_qa | format_response
```

`format_response` always runs last. It appends an `AIMessage` to `messages`, appends a display item (with `artifacts`) to `chat_display`, updates `analyzed_pdb_ids`, and clears per-turn scratch fields.

### State schema (`graph/state.py`)

`PEATState` is a TypedDict. Key reducers:
- `messages` — `add_messages` (LangGraph, merges by ID)
- `chat_display` — `operator.add` (accumulates across turns)
- All other fields — overwrite (no reducer)

`artifacts` and `response_text` are scratch fields; they are set by the action node and cleared by `format_response` at the end of each turn.

### Router (`graph/router.py`)

Deterministic regex routing — no LLM involved. Seven `parse_*` functions plus `is_hpc_command` are called in priority order inside the `router` node. The `route_by_intent` function is a pure state read used as the conditional edge.

Priority order:
1. `is_hpc_command` → `"hpc"` (command prefix: `gmx`, `sbatch`, `squeue`, etc.)
2. `parse_minimize_request` → `"minimize"`
3. `parse_check_job` → `"check_job"`
4. `parse_download_results` → `"download"`
5. `parse_alphafold_request` → `"alphafold"`
6. `parse_foldseek_request` → `"foldseek"`
7. `parse_analyze_request` → `"analyze"` (bare PDB ID; routes to `"llm_qa"` if already in `analyzed_pdb_ids`)
8. `parse_sequence_input` → `"sequence"` (FASTA block or raw AA ≥ 20 chars, ≥ 80% valid AA chars)
9. default → `"llm_qa"`

### Analysis subgraph (`graph/analysis/`)

Six linear nodes compiled with `checkpointer=False`. They read/write to the shared `PEATState` scratch fields:

`fetch_pdb_meta` → `fetch_uniprot` → `fetch_structure` → `fetch_active_sites` → `summarize_annotations` → `rag_literature`

`rag_literature` is the only node that writes `response_text` and `artifacts`. It calls the redundant OA-first paper retrieval cascade (`graph/analysis/oa_resolver.resolve_oa_pdf` — Unpaywall → OpenAlex → Crossref, each source independently timeout/exception-hardened so one failing never blocks the others) and builds the full tab artifact structure. It also writes `paper_retrieval_status` (`"found"` / `"not_found"` / `"no_doi"`) and `paper_source` so retrieval outcome is machine-readable, and prepends a top-level `"callout"` artifact (success/warning) so the outcome is always visible, not just folded into fallback prose.

`fetch_structure` writes to `temp.pdb` on disk — a side effect outside state, used immediately by downstream nodes and the mutation form.

### Tool modules

The root-level modules (`bio_tools.py`, `hpc_tools.py`, `data_fetch.py`, `sequence_tools.py`, `structure_tools.py`, `predictors.py`, `ui.py`) are **not** imported by `app.py` directly. They are wrapped by `@tool`-decorated functions in `graph/tools/` and called via `.invoke({...})` from graph nodes. Do not add graph-level logic to these modules.

### LLM chains (`graph/chains.py`)

Single `ChatOpenAI` instance shared across four LCEL chains. All LLM calls go through this file — no direct `ChatOpenAI` instantiation elsewhere. The model and base URL are read from `LLM_BASE_URL` and `LLM_MODEL` env vars; the default is the Jetstream2 Llama 4 Scout endpoint (no API key required).

### Artifacts

The `artifacts` field is `list[dict]`. Each dict has a `"type"` key. The full set of types handled by `_render_artifact()` in `app.py`:

| type | rendered as |
|------|-------------|
| `"markdown"` | `st.markdown` |
| `"html"` | `st.components.v1.html` (height 550 — 3Dmol.js viewer) |
| `"plotly"` | `st.plotly_chart` (`data` is a JSON string via `fig.to_json()`; parsed back with `pio.from_json()` — required for msgpack checkpointer compatibility) |
| `"code"` | `st.code` |
| `"tabs"` | `st.tabs` with nested `content` lists |
| `"expander"` | `st.expander` with nested `content` list |
| `"mutation_form"` | inline Streamlit form calling `predict_ddg_dynamut` against `temp.pdb` |
| `"callout"` | `st.success` / `st.warning` / `st.error` (keyed by `"level"`, default `st.info`) — used by `rag_literature` to flag paper retrieval status |

## Adding a new intent

1. Add a `parse_<intent>(text: str) -> str | None` function to `graph/router.py`.
2. Add a branch in the `router` node that calls it in the correct priority position.
3. Add the intent string to `route_by_intent`'s return type and to the `add_conditional_edges` mapping in `graph/graph.py`.
4. Create the action node (in `graph/nodes/` or as a new subgraph) — it must write to `response_text` and optionally `artifacts`.
5. Wire the node with `add_edge(node_name, "format_response")` in `build_graph()`.
6. Add a `@tool` wrapper in `graph/tools/` if the node calls an external API or module.

## Predictors (`predictors.py`)

`predict_ddg_dynamut` and `predict_mcsmp_pi` POST to placeholder URLs (`dynamut-api.example.org`, `mcsmp-api.example.org`). These are stubs — the mutation form in the UI will fail at runtime until real endpoints are wired in.

## HPC / Globus Compute

`hpc_tools.py` uses a lazy singleton `_gc_client` initialized on first call. `submit_minimization`, `check_job_status`, and `download_job_results` all require `GLOBUS_COMPUTE_ENDPOINT_ID`, `GLOBUS_CLIENT_ID`, and `GLOBUS_CLIENT_SECRET`. The `_run_minimization_on_hpc` function is serialized and executed remotely on Anvil — it cannot import from the local project.
