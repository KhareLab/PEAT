# Dependencies

The stack is in four parts: the Python packages the server declares, four middleware packages it
deliberately does **not** declare, the frontend's own small set, and the external services the
agent calls at runtime.

## Python runtime dependencies

Declared in `pyproject.toml`, grouped as that file groups them. The package requires Python 3.11
or newer, and `scripts/setup.sh` builds the environment with 3.12.

| Group | Packages |
| --- | --- |
| Agent loop | `langgraph>=1.2,<2`, `langgraph-checkpoint-sqlite`, `langchain-core>=1.6,<2`, `langchain>=1.4,<2`, `langchain-anthropic`, `python-json-logger`, `pyyaml`, `python-dotenv` |
| Web | `fastapi`, `uvicorn[standard]`, `httpx`, `pydantic>=2`, `pydantic-settings` |
| Data lake and artifacts | `kuzu`, `pyarrow`, `python-docx`, `biopython` |

Three further dependencies are there only to satisfy upstream code, and are commented as such at
their declaration:

- **`langchain-openai` and `langchain-ollama`** are not used by this project. `import flowgentic`
  pulls in a provider module that imports both at module scope, so they must be installed.
- **`aiohttp`** is required because flowgentic's retry machinery wraps its optional `httpx` and
  `aiohttp` imports in `try: ... except Exception: raise`, which makes both hard requirements.

Optional groups:

```bash
uv pip install -e '.[dev]'    # pytest, pytest-asyncio, respx, ruff
uv pip install -e '.[chem]'   # ChemGraph: cheminformatics / quantum chemistry
```

ChemGraph is heavy and pins `langgraph`, `langchain` and `pydantic` exactly while pulling Torch
through `mace-torch`, so install it deliberately and re-check the resolved set afterwards.

## The four middleware packages

| Package | Role |
| --- | --- |
| `radical.asyncflow` | The workflow engine tasks are submitted through |
| `rhapsody` | Execution backends, including the `ConcurrentExecutionBackend` process pool the `local` interface uses |
| `flowgentic` | The LangGraph ↔ asyncflow integration: tasks are wrapped as flowgentic function tasks |
| `radical.orbit` | The broker, the endpoint runtime, and the rhapsody and PSI/J plugins behind the `hpc` interface |

They are **deliberately absent from `pyproject.toml`**, and there is no `[tool.uv.sources]`
block. All four are local editable installs from a `refcodes/` directory, applied by
`scripts/setup.sh`.

The reason is recorded in `pyproject.toml` itself. Declaring them makes `uv sync` the obvious
command, and it cannot work: flowgentic pins `radical-asyncflow` and `academy-py` to git URLs, so
resolving its dependencies fetches from git and fights the local checkout. It is therefore
installed `--no-deps`, and the three packages it then lacks (`academy`, `langchain-community`,
`langchain-mcp-adapters`) are on no path this project uses. A previous `[tool.uv.sources]` block
listed three of them while none was a declared dependency, so `uv sync` resolved cleanly and
silently produced an environment that died at `import flowgentic`.

`radical.orbit` is installed `--no-deps` for a related reason — its requirements name the
published `rhapsody-py`, which would displace the editable checkout — with its real needs
installed by name alongside, including `psij-python`, `globus-sdk`, `msgpack`, `cloudpickle`,
`websockets` and `authlib`. Two more are added by hand: **`opentelemetry-sdk`**, without which
every rhapsody session inside Orbit fails to open, and **`nvidia-ml-py`**.

Because `refcodes/` is gitignored, the revisions this project was last built and checked against
are recorded in `scripts/setup.sh` as `REFCODES_PINS`, and `--check` warns on a mismatch rather
than failing — a newer checkout may well be fine, but the repository's claims about these
packages were made against those commits.

### Known rough edges, worked around at the call site

1. flowgentic's examples import `ConcurrentExecutionBackend` from `radical.asyncflow`, where it
   no longer exists; it lives in `rhapsody.backends`.
2. `_default_retryable_exceptions()` re-raises on a failed optional import, making `httpx` and
   `aiohttp` hard requirements. Both are installed, and retryable exceptions are passed
   explicitly.
3. flowgentic's default retry configuration is a 30-second timeout with three attempts, which
   would cancel and silently re-run a fold or an HPC job. Tasks are wrapped with no timeout and a
   single attempt.
4. Orbit's clients are synchronous and its push callbacks arrive on a listener thread, so calls
   are run in a thread and events are hopped back onto the event loop. Its terminal task event
   omits stdout, so the result is re-fetched.

## Frontend

`frontend/package.json` declares four runtime dependencies:

| Package | Version | Role |
| --- | --- | --- |
| `react` / `react-dom` | ^19.0.0 | The UI |
| `marked` | ^15.0.6 | Renders assistant Markdown |
| `dompurify` | ^3.2.3 | Sanitizes it, because assistant text is model-influenced |

The build and test set is `vite` ^6, `typescript` ~5.7, `@vitejs/plugin-react`, `vitest` with
`jsdom` and Testing Library for the unit tier, and `@playwright/test` for the Chromium tier.

**The Mol\* viewer is not an npm dependency.** `frontend/src/artifacts/MolstarView.tsx` loads
`@rcsb/rcsb-molstar` (pinned to a specific version in that file) as a UMD bundle from jsDelivr at
runtime and caches the loader on `window`. The structure viewer therefore needs network access
from the *browser*, not from the server.

## External services

None of these is required for the application to start; each one that is unavailable produces a
degraded path that says so.

| Service | Used for | Credential |
| --- | --- | --- |
| [RCSB PDB](https://www.rcsb.org/) — data, file download and search APIs | Reference structures, metadata, text search | none |
| [UniProt](https://www.uniprot.org/) REST | Sequence and functional annotation | none |
| [Europe PMC](https://europepmc.org/) | Literature lookup for the target | none |
| [AlphaFold DB](https://alphafold.ebi.ac.uk/) | Predicted structures where there is no experimental one | none |
| [ESM Atlas](https://esmatlas.com/) `foldSequence` | The default folding backend, for sequences up to ~400 aa | none |
| [Anthropic](https://www.anthropic.com/) | Intent classification, round planning, the written summary | an API key, per user |
| ProteinMPNN | Sequence design | a local install, or an HPC endpoint |
| A RADICAL Orbit broker and endpoint | Real scheduler jobs: submit, poll, tail, cancel | broker URL, token and certificate, plus an account and queue |
| [Globus Compute](https://www.globus.org/compute) | An alternative `hpc` substrate | a Globus identity. `globus-compute-sdk` is not installed by default, and this adapter has never met a live endpoint |
