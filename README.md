# PEAT – Protein Engineering Agent Toolkit

> RAG-powered structural biology assistant for the KhareLab at Rutgers University.

**Live app**: http://149.165.172.252

## What it does

PEAT is a Streamlit chatbot that combines four capabilities:

1. **Structure analysis** — PDB lookup, interactive 3D viewer (3Dmol.js), UniProt domain maps, M-CSA active sites, AlphaFold structure fetching
2. **Literature RAG** — retrieves open-access papers via Unpaywall, runs LLM Q&A against the full text
3. **Structural similarity** — Foldseek search against PDB, AlphaFold DB, and SwissProt
4. **HPC execution** — submits GROMACS energy minimization jobs to Anvil (NAIRR allocation) via Globus Compute, monitors job status, and downloads results — all from the chat box

## Usage

Everything goes through the chat box:

| Input | What happens |
|-------|-------------|
| `Analyze 6B5X` or just `6B5X` | Full PDB + UniProt + literature analysis |
| FASTA block or raw AA sequence | BLAST → top PDB match → analysis |
| `alphafold Q9WYE2` | Fetch AlphaFold predicted structure |
| `foldseek 6B5X` | Structural similarity search |
| `minimize 6B5X` | Submit GROMACS energy minimization on Anvil |
| `check job 1234567` | Poll SLURM job status |
| `download results 1234567` | Pull output files from Anvil |
| `gmx ...` / `sbatch ...` / `squeue` | Run arbitrary HPC commands |
| Anything else | LLM answer using full conversation context |

## Quick start

```bash
git clone https://github.com/ArnavAK74/PEAT.git
cd PEAT
pip install -r requirements.txt
```

Create a `.env` file:

```env
# LLM (defaults to Jetstream2 Llama 4 Scout — no key needed)
LLM_BASE_URL=https://llm.jetstream-cloud.org/llama-4-scout/v1/
LLM_MODEL=llama-4-scout

# For harder reasoning tasks, use DeepSeek R1:
# LLM_BASE_URL=https://llm.jetstream-cloud.org/sglang/v1/
# LLM_MODEL=DeepSeek-R1

# RAG
UNPAYWALL_EMAIL=your@email.edu
SCIHUB_ENABLED=false

# HPC — Anvil (NAIRR allocation) via Globus Compute
GLOBUS_COMPUTE_ENDPOINT_ID=your-endpoint-uuid
GLOBUS_CLIENT_ID=your-client-id
GLOBUS_CLIENT_SECRET=your-client-secret
HPC_WORKDIR=/anvil/scratch/yournetid/peat_runs
HPC_PARTITION=gpu
HPC_QOS=gpu
HPC_ACCOUNT=your_allocation

# Optional: direct SSH for HPC commands (squeue, sacct, etc.)
HPC_HOST=anvil.rcac.purdue.edu
HPC_USER=yournetid
HPC_PRIVATE_KEY=<raw private key content>
```

```bash
streamlit run app.py
```

## Agents and Graph

PEAT's agentic loop is implemented as a [LangGraph](https://github.com/langchain-ai/langgraph) `StateGraph`. The Streamlit UI calls `graph.stream()` each turn and renders whatever the graph produces; all conversational logic lives in the graph.

### State

A single `PEATState` TypedDict is threaded through every node. Key fields:

| Field | Type | Description |
|-------|------|-------------|
| `messages` | `list[BaseMessage]` | LLM conversation history (LangChain message types, `add_messages` reducer) |
| `chat_display` | `list[dict]` | Rendered chat items including rich artifacts; accumulates across turns |
| `intent` | `str` | Routing token set by the router node each turn |
| `analyzed_pdb_ids` | `list[str]` | PDB IDs already analyzed; persisted across sessions via checkpointer |
| `hpc_jobs` | `dict` | Job ID → `{pdb_id, remote_dir}`; persisted across sessions via checkpointer |
| `response_text` | `str \| None` | Text output for the current turn; cleared after each turn |
| `artifacts` | `list[dict]` | Rich UI artifacts (tabs, 3D viewer, plots) for the current turn; cleared after each turn |

Conversation state is persisted with `SqliteSaver` (`peat_state.db`), so HPC job records survive app restarts.

### Routing

The router node runs before every LLM call. It applies a priority-ordered sequence of deterministic regex parsers — one per intent — and writes the matched intent and any extracted parameters to state. No LLM is involved in routing.

```
user input
  │
  ▼
router node (regex parsers, priority order)
  │
  ├── HPC command prefix (gmx, sbatch, …)  →  hpc_command
  ├── "minimize <PDB>"                      →  hpc_minimize
  ├── "check job <ID>"                      →  hpc_check
  ├── "download results <ID>"               →  hpc_download
  ├── "alphafold <UniProt>"                 →  alphafold
  ├── "foldseek <PDB>"                      →  foldseek
  ├── bare PDB ID (not yet analyzed)        →  analysis subgraph
  ├── protein sequence / FASTA              →  blast_search → analysis or llm_qa
  └── everything else                       →  llm_qa
```

If a PDB ID is recognized but was already analyzed this session, the router routes to `llm_qa` so the LLM can answer from conversation context instead of re-running the pipeline.

Each parser is an independently-testable function in `graph/router.py`. Adding a new intent means adding a parser function and a new conditional branch — no changes to existing parsers.

### Analysis subgraph

PDB analysis is a 6-node linear subgraph (`graph/analysis/`). Each step writes its results to state fields that downstream nodes read:

```
fetch_pdb_meta → fetch_uniprot → fetch_structure
               → fetch_active_sites → summarize_annotations → rag_literature
```

| Node | Does |
|------|------|
| `fetch_pdb_meta` | RCSB REST → `pdb_entry` |
| `fetch_uniprot` | SIFTS mapping + UniProt REST → `uniprot_id`, `uniprot_features` |
| `fetch_structure` | RCSB PDB download; AlphaFold fallback via `alphafold_fetch` tool → `structure_source`, `af_result` |
| `fetch_active_sites` | M-CSA REST → `m_csa_sites` |
| `summarize_annotations` | UniProt comments → LLM (`annotation_chain`) → `gpt_summary` JSON |
| `rag_literature` | Unpaywall cascade → LLM Q&A against paper text (`literature_qa_chain`); annotation fallback if no paper → `response_text`, `artifacts` |

HTTP-only nodes (`fetch_pdb_meta`, `fetch_uniprot`, `fetch_active_sites`) have `RetryPolicy(max_attempts=3)`.

### Tools

All protein tool functions are exposed as LangChain `@tool`-decorated wrappers in `graph/tools/`. The underlying modules (`bio_tools.py`, `hpc_tools.py`, `data_fetch.py`, etc.) are unchanged; the `@tool` layer provides standard LangChain tool metadata and a clean interface for future LLM tool-calling integration.

### LLM chains

All LLM calls go through LCEL chains defined in `graph/chains.py`. A single `ChatOpenAI` instance (configured from env vars) is shared across all chains:

| Chain | Used by |
|-------|---------|
| `qa_chain` | `llm_qa` node — general conversational Q&A with full message history |
| `annotation_chain` | `summarize_annotations` — UniProt text → structured JSON |
| `literature_qa_chain` | `rag_literature` — answer against paper excerpt |
| `annotation_qa_chain` | `rag_literature` — fallback answer when no paper is available |

### Code layout

```
graph/
├── state.py          # PEATState TypedDict, HpcJobInfo, initial_state()
├── router.py         # deterministic intent parsers + router node
├── chains.py         # ChatOpenAI instance + LCEL chain definitions
├── graph.py          # build_graph() — assembles nodes, edges, checkpointer
├── tools/            # @tool wrappers: bio.py, hpc.py, data.py
├── nodes/            # action nodes: hpc.py, bio.py, sequence.py, llm_qa.py, format_response.py
└── analysis/         # 6-node analysis subgraph
```

`app.py` contains only the Streamlit UI — page config, artifact renderer, history rendering loop, and the `graph.stream()` call.

## LLM backends

| Endpoint | Model | Use case |
|----------|-------|----------|
| `https://llm.jetstream-cloud.org/llama-4-scout/v1/` | `llama-4-scout` | Default — general Q&A, annotation summaries |
| `https://llm.jetstream-cloud.org/sglang/v1/` | `DeepSeek-R1` | Harder reasoning tasks |

Both are Jetstream2-hosted, OpenAI-compatible, and require no API key.

## RAG pipeline

Paper retrieval is attempted in this order:
1. **Unpaywall** — open-access PDF (always on; requires `UNPAYWALL_EMAIL`)
2. **Library cookie** — set `LIBRARY_COOKIE` env var for institutional access
3. **Sci-Hub** — set `SCIHUB_ENABLED=true` (dev only)

If no paper is retrieved, the LLM answers from PDB metadata and UniProt annotations instead.

## HPC

Job submission uses [Globus Compute](https://www.globus.org/compute) with confidential client authentication (`GLOBUS_CLIENT_ID` + `GLOBUS_CLIENT_SECRET`). The GROMACS minimization function runs remotely on Anvil; results are pulled back via Globus Compute file reads.

Direct SSH (`HPC_HOST`, `HPC_USER`, `HPC_PRIVATE_KEY`) is optional and used only for interactive HPC commands typed in the chat box.

Allowed command prefixes: `gmx`, `python`, `python3`, `bash`, `sh`, `squeue`, `sacct`, `sbatch`, `scancel`, `echo`, `ls`, `cat`, `head`, `tail`

Timeout: 300 s (override with `HPC_TIMEOUT_SECONDS`).

## License

MIT
