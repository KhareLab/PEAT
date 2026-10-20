# PEAT

**PEAT** — the Protein Engineering Agent Toolkit — is a chatbot for protein
redesign: a LangGraph agent loop with a web chat interface and an artifact pane
that renders session summaries and interactive molecular visualizations.

This is a from-scratch rewrite and shares no history with the Streamlit
application that previously carried the name; that work is preserved on the
`archive/*` branches. The Python package is still called `designagent`
(`plans/BACKLOG.md` **B12**).

Documentation: <https://kharelab.github.io/PEAT/> — built from `docs/`.

```
┌───────────────── frontend (Vite/React) ─────────────────┐
│  chat pane  ◄── SSE ──┐            │  artifact pane      │
│  (prompts, status,    │            │  Mol* · Markdown    │
│   task chips)         │            │  .docx · tables     │
└───────────────────────┼────────────┴─────────────────────┘
                        │ /api/chat, /api/artifacts
┌───────────────────────┴───────────── backend (FastAPI) ──┐
│  LangGraph loop                                          │
│    Coordinator → Initializer → Orchestrator              │
│        │             ↑              ↓                    │
│        │        Interpreter ←   Analyst                  │
│        └──→ Protocol — one cluster stage per turn        │
│                                                          │
│  Task Interfaces          Design History (tiered lake)   │
│   local  → flowgentic/asyncflow → rhapsody process pool  │
│   query  → REST APIs (RCSB, UniProt, Europe PMC)         │
│   hpc    → RADICAL Orbit  (or Globus Compute)            │
└──────────────────────────────────────────────────────────┘
```

## Quick start

```bash
./scripts/setup.sh              # builds .venv and verifies it
cp .env.example .env            # optional: add ANTHROPIC_API_KEY
cd frontend && npm install && cd ..
./scripts/dev.sh up             # backend :8000 + frontend :5173
```

Or start the two by hand — `.venv/bin/python -m designagent` and, in another
shell, `cd frontend && npm run dev`. `dev.sh` does the same thing and also waits
for the remote endpoint to finish registering, refuses to start a second backend
on a data dir Kuzu has locked, and knows that stopping the backend needs more
than a `SIGTERM`. `down`, `status`, `restart` and `logs` are the other
subcommands; `up --no-mpnn` starts it with no endpoint, which is the heuristic
path, `up --backend` skips Vite, and `--port N` / `--scratch` give a test run its
own port and its own data dir.

`scripts/setup.sh` is the only supported install path, and `--check` verifies an
existing environment without installing anything. **`uv sync` cannot work here**:
`radical.asyncflow`, `rhapsody`, `flowgentic` and `radical.orbit` are local
editable installs from `refcodes/`, and flowgentic pins two of its own
dependencies to git URLs that fight the local checkouts, so it is installed
`--no-deps`. `refcodes/` is gitignored, so a fresh clone cannot build at all —
see `plans/BACKLOG.md` B1. `--check` also imports `opentelemetry.sdk`, because
without it every rhapsody session inside Orbit fails to open and the whole
`live` tier skips itself with an import error rather than failing.

Two optional scripts: `./scripts/setup_mpnn.sh` installs real ProteinMPNN for a
CPU run in its own `.venv-mpnn` (a ~200 MB torch download, which is why
`setup.sh` does not), and `--check` prints the `DESIGNAGENT_MPNN_COMMAND` to
export. `./scripts/amarel_endpoint.sh` stands up and watches the Orbit endpoint
on the cluster — `install|check|start|run|stop|status|logs|selftest`.

Then ask for something, e.g. *"redesign 1UBQ to improve thermostability"*.

**It runs without an API key.** Every node has a deterministic rule-based path,
so the loop works end to end with no LLM; the key only improves intent
classification, planning and the written summary. `/api/health` reports which
mode you are in — in full to anyone while logins are off, and only to an admin
once they are on.

## The agent loop

| Node | Does | Writes to state |
| --- | --- | --- |
| **Coordinator** | Classifies the prompt, streams updates, answers from state | `intent`, `goal` |
| **Design Initializer** | PDB + UniProt + literature lookups, concurrently | `reference_design` |
| **Redesign Orchestrator** | Picks the success metric, builds and dispatches the worklist | `key_metric`, `worklist` |
| **Analyst** | Scores returns, writes the lake, ranks, rebuilds the view | `lead_design`, `ensemble`, `molecular_visualization` |
| **Interpreter** | Summarizes, flags related past designs, renders artifacts | `design_summary`, `artifacts` |
| **Protocol** | Runs one stage of the enzyme-redesign cluster pipeline per turn | `protocol` |

Routing is dynamic (`Command(goto=...)`): chat ends immediately, a design
request loops Orchestrator → Analyst until the key metric stops improving or
`max_rounds` is reached, then summarizes. One exception, and it is the subject of
the next section: while a protocol campaign is waiting on an answer, the
coordinator routes to Protocol *before* it classifies the message at all.

### The protocol node

The other five nodes serve a chat turn. `graph/nodes/protocol.py` serves a
multi-day cluster pipeline — the enzyme-redesign protocol — as **one stage per
turn**: nine stages in `STAGES`, from intake through conservation, ProteinMPNN
redesign, selection and AlphaFold3 to the report. It is the only node that
answers the user every turn and never routes onward.

The protocol has three points where the run must stop and wait for a person, and
this repo has no `interrupt()` anywhere. **The turn boundary is the checkpoint
mechanism** instead: a stage sets `protocol.awaiting`, returns, and the next
message answers it (four entries in `ANSWERS`). The consequence shows up in the
coordinator, which routes to `protocol` on `awaiting` *before* it classifies
intent at all — "liu", "310,364" and "go" all classify as `chat`, which would
answer from session state and leave the campaign waiting forever. `ESCAPE_WORDS`
(`graph/nodes/coordinator.py`) is the way out, so nobody is trapped in a
campaign they want to leave.

Progress is read from the filesystem rather than from a task handle: handles are
in-memory, so a restart cannot re-attach to an in-flight job, and the collect
stage instead lists the output tree. The data outlives the handle, so recovery is
a re-fetch rather than a re-attach. Everything a chat message contributes —
target name, UniProt id, domains, catalytic residues, a netid — ends up inside a
`bash -lc` body and in cluster paths, so `protocol/inputs.py` rejects rather than
sanitizes, and the `protocol_*` settings are environment-only for the same reason
`mpnn_command` is. **No stage has yet run on a cluster**, so every walltime, core
count and memory figure in `protocol/specs.py` is still an estimate.

## Task Interfaces

Task duration is indeterminate, so **every** interface submits asynchronously and
returns a handle carrying a future. Nothing in the graph blocks on submission.

| Interface | Backing | Cancel | Log stream | Push events |
| --- | --- | --- | --- | --- |
| `local` | flowgentic → asyncflow → rhapsody `ConcurrentExecutionBackend` (process pool) | yes | no | no |
| `query` | `httpx` on the main loop (REST retrieval) | yes | no | no |
| `hpc` (Orbit) | `EndpointRuntime` + rhapsody & PSI/J plugins | yes | yes (PSI/J, by byte offset) | yes |
| `hpc` (Globus) | `globus_compute_sdk.Executor` | only before start | no | no |

Capabilities are declared rather than assumed (`tasks/base.py: Capabilities`),
because the two HPC backends genuinely differ. With no HPC endpoint configured,
`hpc` tasks fall back to their app-local equivalents.

### Remote HPC

Orbit is implemented first, and it has run for real: on 2026-10-09 the `remote`
tier submitted PSI/J jobs through a broker on a public host to an endpoint
registered from a cluster login node across the internet, read their logs,
cancelled a queued one, and got the Slurm id back in `handle.meta["native_id"]`
(`plans/AMAREL_ENDPOINT.md` §6, rung 4). What that run found is three new backlog
entries rather than a broken path: two clients sharing an Orbit client name steal
each other's replies (A18), a `custom_attributes` flag that takes no value cannot
be expressed (A19), and — the sharp one — a `directory` that does not exist is
**silently replaced by `/tmp`** and the job still reports success (A20).

Against a real broker:

```bash
DESIGNAGENT_ORBIT_ENABLED=true \
RADICAL_ORBIT_BROKER_URL=https://broker.site:8443 \
RADICAL_ORBIT_BROKER_CERT=/path/broker_cert.pem \
RADICAL_ORBIT_BROKER_TOKEN=... \
DESIGNAGENT_ORBIT_PSIJ_EXECUTOR=slurm DESIGNAGENT_ORBIT_ACCOUNT=... \
.venv/bin/python -m designagent
```

For development, `DESIGNAGENT_ORBIT_LOCAL=true` has the server bring up a
localhost broker plus an endpoint itself (`tasks/hpc/local_orbit.py`: rhapsody on
`concurrent`, PSI/J on `local`), so the real client path is exercised with no
allocation. That broker runs `--no-auth`, which disables *ingress auth only* — it
still serves TLS and will not start without a cert and key, which the stack
generates.

The HPC path carries a real job spec: `_job_params` (`graph/nodes/orchestrator.py`)
fills in the executable from the tool module and the allocation from settings, and
`to_psij_spec` renames our resource vocabulary to PSI/J's. Without that, a
submission is `/bin/true` — or, with the wrong field names, an HTTP 500, which
rung 4 confirmed against a real site and not only against localhost.

The Globus adapter implements the same ABC and takes an injectable
`executor_factory`, so it is testable offline. It has never met a live endpoint,
and `globus-compute-sdk` is not installed.

## Design History (tiered data lake)

| Tier | Store | Holds |
| --- | --- | --- |
| 1 | Kuzu graph (embedded) | raw task outputs and provenance: `Design`, `Task`, `Output`, `Structure`, `DERIVED_FROM`, `PRODUCED` |
| 2 | SQLite | scores, per-round rankings, analyses |
| 3 | Parquet + manifest | curated golden sets staged for ML training |

Bulky payloads (coordinates, FASTA) go to a content-addressed blob directory and
are referenced by path from tier 1. A lake write failure is non-fatal: the round
survives in state, ranking falls back to in-memory, and the user is told in the
summary's caveats.

## Configuration

Settings come from env vars (prefix `DESIGNAGENT_`, see `.env.example`), and can
also be supplied **to a running server** — from the Settings panel in the UI, or
`PUT /api/settings`. Those are held in memory and are not written anywhere, so a
restart returns to `.env`; `GET /api/settings` says which source each value came
from. Precedence is: values set in the running app, then the environment, then
`.env` (resolved against the CWD), then the defaults in `config.py`.

```bash
python -m designagent --check-config            # what is in effect, secrets masked
python -m designagent --check-config --probe    # ...and does each credential work
```

The ones that change behaviour most:

- `ANTHROPIC_API_KEY` — enables LLM reasoning
- `DESIGNAGENT_FOLD_BACKEND` — `esmatlas` (public API, ≤400 aa), `local`, or `hpc`
- `DESIGNAGENT_POOL_WORKERS` — process-pool size
- `DESIGNAGENT_MAX_ROUNDS` — redesign rounds before summarizing
- `DESIGNAGENT_ORBIT_ENABLED` + `RADICAL_ORBIT_*` — remote HPC, with
  `DESIGNAGENT_ORBIT_PSIJ_EXECUTOR` / `_ACCOUNT` / `_QUEUE` for the site
- `DESIGNAGENT_ORBIT_LOCAL` — start a localhost broker + endpoint at startup
- `DESIGNAGENT_ADMIN_TOKEN` — required to change settings over HTTP when the
  server is not bound to loopback. With logins on it is not consulted at all:
  the admin role replaces it
- `DESIGNAGENT_AUTH_ENABLED` + `DESIGNAGENT_SECRETS_KEY` — per-user logins and
  the Fernet key their credentials are stored under, with
  `DESIGNAGENT_ORBIT_ALLOWED_BROKERS` naming the brokers a user may point at
- `DESIGNAGENT_KUZU_BUFFER_POOL_MB` — Kuzu's buffer pool. 0 keeps its default of
  ~80% of RAM, which on a small VM is most of the machine
- `DESIGNAGENT_WRAP_NODES` — see the note below

The `DESIGNAGENT_PROTOCOL_*` group — cluster roots, conda environments, tool
paths, module lines, the GPU queue — is environment-only, like `data_dir`,
`pool_workers` and `mpnn_command`: each one names a path or a shell word the
server will run on the endpoint under the site's allocation, so `/api/settings`
reports them and refuses to write them.

A credential is never rendered in full: responses carry presence, source and a
masked hint, and a log filter scrubs live secret values from records this code
did not write. Nothing refuses to start — a missing or rejected credential shows
up in `/api/health`, is badged in the UI, and makes the turn say in its reply
that it fell back.

### Logins and per-user credentials

Off unless `DESIGNAGENT_AUTH_ENABLED=true`, and off they change nothing: one
implicit user, the loopback rules above, and the whole offline suite behaves as
it did before they existed. On, every route but `POST /api/login`,
`POST /api/logout` and a bare `/api/health` needs the session cookie, a chat
session must be the caller's, session ids are minted by `POST /api/sessions`, and
the operator's settings routes need the admin role. `tests/test_auth.py` is the
contract.

Accounts are made from the CLI, with the server's environment, never over HTTP:

```bash
python -m designagent --gen-secrets-key          # a DESIGNAGENT_SECRETS_KEY
python -m designagent --add-user NAME [--admin]  # an account
python -m designagent --passwd NAME              # set a password
python -m designagent --list-users
```

A signed-in user then has their own credentials — an Anthropic key and model, and
an Orbit broker, token, cert, endpoint, account and queue
(`auth/credentials.py::USER_FIELDS`) — which replace the operator's for their
turns. They are stored as Fernet ciphertext in a fourth store, `data/auth.sqlite`,
with each value bound to its user and field, and they are validated by rejection.
A broker must be on `DESIGNAGENT_ORBIT_ALLOWED_BROKERS`, because it is a host this
server will dial and hand a token to. A plain user's baseline has every operator
credential *removed*, so nobody falls through to the operator's key or
allocation, and every path, shell and scheduler choice stays operator-only.

A turn carries its user in a `ContextVar`, not in LangGraph's `configurable` and
not in state, because checkpoint metadata would then hold a copy of the key every
turn; a pool task that calls the model is handed the key as a call argument rather
than through the params that get written to the lake.
`test_no_user_secret_is_persisted_by_a_turn` scans the checkpointer, the task
snapshots and the data dir for it.

### Deployment

Development is loopback: `:8000` and Vite on `:5173`. There is also a real
deployment, and `plans/LINODE_DEPLOY.md` is both its plan and its record — a
small VM that runs the Orbit broker under systemd, serves the UI over HTTPS
behind Caddy, and uses the app's own logins as the gate (Caddy's basic auth was
removed once they worked). Three things from it are worth knowing before copying
it: behind a same-host proxy every caller looks like loopback, so the
loopback-open settings routes are open to the internet unless logins or
`DESIGNAGENT_ADMIN_TOKEN` close them; a 4 GB VM needs
`DESIGNAGENT_KUZU_BUFFER_POOL_MB` set, because Kuzu's default is most of the
machine; and the secrets key belongs in the same backup as the broker's key,
since losing it makes every user re-enter their credentials.

`plans/AMAREL_ENDPOINT.md` is the matching cluster side — how the broker and the
endpoint are stood up, with an acceptance ladder whose rungs carry what they
actually returned. Rungs 0–4 are filled in; 5 and 6, a real protocol step and the
whole spine, are not.

### Optional extras

```bash
uv pip install -e '.[chem]'   # ChemGraph: cheminformatics / quantum chemistry
```

ChemGraph pins `langgraph`/`langchain`/`pydantic` exactly and pulls torch, so
install it deliberately and re-check the resolved set.

## Notes on the dependencies

Three things in the reference stack needed working around, all commented at the
call site:

1. **flowgentic's examples import `ConcurrentExecutionBackend` from
   `radical.asyncflow`**, where it no longer exists. It lives in
   `rhapsody.backends` now.
2. **flowgentic's `_default_retryable_exceptions()` does `except Exception:
   raise`** around its optional `httpx`/`aiohttp` imports, making both hard
   requirements. We install aiohttp *and* pass `retryable_exceptions` explicitly.
3. **flowgentic wraps everything with a 30 s timeout and 3 retries by default.**
   Fatal for a node awaiting an HPC job, so tasks are wrapped with
   `timeout_sec=None, max_attempts=1`.

**Why nodes are not flowgentic `EXECUTION_BLOCK`s by default:** that runs the
node body on asyncflow's own event loop, outside LangGraph's runnable context,
so `get_stream_writer()` raises and every status update and token is silently
dropped. Tasks still go through flowgentic to the process pool, which is where
the parallelism matters. Set `DESIGNAGENT_WRAP_NODES=true` to opt back in and
lose custom-event streaming.

## Observability

A turn says how it was made. Each node that authors text declares which function wrote it, so a reply
carries its author rather than just its content:

```
turn s-7fa2: coordinator(0ms) → initializer(2871ms) → orchestrator(967ms) → analyst(540ms)
             → orchestrator(932ms) → analyst(309ms) → interpreter(177ms)
             = 5796ms · reply by interpreter:_rule_based_summary
```

The same record reaches the browser on the `state` frames and is kept with the message, so the
collapsible **how this answer was made** under a reply still works after the next turn and after a
reload. `message` and `token` frames carry the node that produced them; `GET /api/sessions/{id}`
returns `reply_source` and `trace` for the last turn. `reply_source` values are grep-able —
`interpreter:_rule_based_summary` is a function name — and `CLAUDE.md` tabulates which function writes
which sentence.

The trace is counters and node names only, never payloads, and is reset every turn so the checkpoint
holds one turn's worth. Tasks record the node that submitted them, and a finished task's `elapsed`
is its duration rather than its age.

## Tests

```bash
.venv/bin/python -m pytest -q            # 417 tests, offline; stubs replace every tool
.venv/bin/python -m pytest -q -m live    # 12 tests; starts a real Orbit broker + endpoint
.venv/bin/python -m pytest -q -m remote  # 8 tests; submits to a real HPC endpoint
.venv/bin/python -m pytest -q -m llm     # 4 tests; calls Anthropic with a real key

cd frontend && npm test                  # 45 tests in jsdom; no servers needed
cd frontend && npm run test:e2e          # 2 tests in Chromium, canned stream
cd frontend && E2E_LIVE=1 npm run test:e2e   # ...plus one real round trip
```

The offline tier covers the lake tiers, task routing and capabilities,
shell-injection safety on the Globus path, the in-band staging protocol and its
refusal to decompress past a ceiling, the ProteinMPNN job spec and output
adapter, the classifier, the full design loop, artifact rendering, the SSE
framing contract, and resilience when storage or a fold service fails. The
enzyme-redesign protocol is its largest block — 203 of the 417 — and adds the
input validators that stand between a chat message and a shell script, the
signal-peptide offset arithmetic, the fixed-position set, every job spec both as
a dict and as shell run with `bash` against a `tmp_path`, the generated notebook
cell checked against the real notebook, and the stage machine walked turn by turn
against a fake endpoint. The 18 in `tests/test_auth.py` are the logins' contract:
that off they change nothing, that a session belongs to its owner, that an
operator credential is not inherited by a plain user, and that no user secret is
persisted by a turn. None of it needs network, a process pool or an endpoint,
because nodes reach the outside only through `Deps`.

The 12 tests marked `live` bring up a localhost Orbit broker and endpoint as
subprocesses and exercise the real client path — push states, incremental log
tailing by byte offset, a failing job, cancelling a running one, that a job's
stdout comes back whole and unduplicated, that declared inputs and outputs
actually travel, and one real ProteinMPNN run on CPU.

`remote` and `llm` are the two validation tiers, each excluded separately so that
`-m live` cannot drag in one that needs an allocation or spends money. `remote`
reads its broker and scheduler from the environment and submits trivial jobs —
plus three that prove the plumbing the protocol rests on and nothing offline can
reach: a working directory that persists between stages, a scheduler flag PSI/J
has no field for, and a file staged back out of a project directory. The same
assertions rehearse against `DESIGNAGENT_ORBIT_LOCAL=true` before a real
endpoint; `llm` replays the classifier table through the LLM path and reports
where it disagrees with the rules, and checks that the session summary really came
from the model. All three tiers are deselected by default (`addopts` in
`pyproject.toml`), and a marker is the only thing that selects them — naming the
file alone collects nothing.

### The browser

`npm run build` type-checks; it cannot tell you the page renders. Two layers do:

- **jsdom (`npm test`)** — the SSE reader against byte chunks split mid-JSON and mid-terminator, the
  frame reducers that turn a stream into chat state, the trace disclosure, the settings panel (its
  secret input must stay empty and an untouched field must not be sent back as its own masked hint),
  and Markdown sanitization, since assistant text is model-influenced.
- **Chromium (`npm run test:e2e`)** — a real page load, a prompt, the reply, the artifact pane and the
  "how this answer was made" disclosure. The default run replays a canned SSE body through
  `page.route`, so it needs no backend and no network; `E2E_LIVE=1` adds the real turn. Needs a
  one-time `npx playwright install chromium`.

Every e2e test fails on an uncaught page error. That is deliberate: the failure these were written
after was a blank tab, which type-checks and curls perfectly well.

- **By hand (`frontend/e2e/BROWSER_TESTS.md`)** — twelve user tests against a real backend with a
  real endpoint attached. Neither layer above can answer the question they exist for: *can someone
  sitting in front of this tell that a real model ran, and would they notice if it hadn't?* Each one
  names the capability it verifies and what its failure looks like, and the file is explicit about
  the four things a user currently cannot see — above all that a round with no endpoint attached
  never says in its reply that its designs came from a rule table.

## Slides

`slides/` holds a code-walk deck for a technical audience, in three acts —
**functionality** (what new tasks this makes possible), **performance** (what it
costs to run, and what was never measured) and **usability** (what it is usable
*as*: an application, a platform, a component). Its figures are drawn from a real
campaign rather than a mock-up: `run_model.py` mines `data/lake` into
`run.json`, which the builder reads. Re-run that one deliberately — it
aggregates the whole lake and takes whichever campaign comes first, so on a data
dir that has run anything since, it silently replaces the deck's worked example
(`plans/BACKLOG.md` M2).

```bash
.venv/bin/python slides/check_anchors.py   # re-derive the 46 cited line numbers
.venv/bin/python slides/bench_staging.py   # re-measure the staging channel → staging.json
.venv/bin/python slides/run_model.py       # regenerate run.json from data/lake
.venv/bin/python slides/make_script.py     # regenerate DECK_SCRIPT.md from the deck's notes
NODE_PATH=<dir with pptxgenjs> node slides/build_deck.js
```

`bench_staging.py` is the deck's one measurement rather than a mined figure: it
drives the real `wrap()`/`collect()` from `tasks/hpc/artifacts.py` through a
local shell and reports what a file costs to cross the broker. It is where
backlog **A22** came from.

Run `check_anchors.py` after changing backend code: a slide that cites
`tasks/base.py:135` while showing something else is worse than one with no
citation.

## Backlog

Open issues live in [`plans/BACKLOG.md`](plans/BACKLOG.md), each with the
evidence and how to reproduce it. The ones that most change how you should read
this README: the Globus adapter has never met a live endpoint; `proteinmpnn`
falls back to a labelled heuristic proposer when no endpoint is attached, and the
cheapest way to tell the two apart in a finished round is the mutation count,
because the heuristic only ever emits single substitutions; and **no stage of the
enzyme-redesign protocol has run on a cluster**, so its walltimes, core counts and
memory figures are estimates.

What *has* run on a real endpoint is the `hpc` path itself — submit, poll, log,
cancel, with the Slurm id coming back — on 2026-10-09 (`plans/AMAREL_ENDPOINT.md`
rung 4). The three things that run found are A18, A19 and A20, the last of which
says a job whose working directory does not exist runs in `/tmp` and reports
success. The Mol* artifact pane was confirmed rendering in a browser on
2026-10-01.
