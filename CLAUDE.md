# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

```bash
./scripts/setup.sh           # build .venv and verify it
./scripts/setup.sh --check   # verify an existing one, install nothing
```

That script is the only supported path and **`uv sync` is not an alternative**. The four middleware
packages — `radical.asyncflow`, `rhapsody`, `flowgentic` and `radical.orbit` — are local editable
installs from `refcodes/`, which is gitignored and absent from a fresh clone; flowgentic pins
`radical-asyncflow` and `academy-py` to git URLs that fight the local checkouts, so it goes in
`--no-deps` and the three deps it then lacks are unused here. `pyproject.toml` carries the full
reasoning where a `[tool.uv.sources]` block used to be.

Orbit goes in `--no-deps` too, with `opentelemetry-sdk` and `nvidia-ml-py` added by hand: its
rhapsody plugin calls `start_telemetry`, and without the sdk every session fails to open and the
whole `live` tier *skips* itself on an import error rather than failing (`plans/AMAREL_ENDPOINT.md`
rung 1). `setup.sh --check` imports both for that reason.

Run `--check` after a pull: it fails with the missing piece named, rather than letting the app die
deep inside an import.

```bash
./scripts/setup_mpnn.sh          # real ProteinMPNN (CPU) in .venv-mpnn + refcodes/ProteinMPNN
./scripts/setup_mpnn.sh --check  # verify, and print the command to export
```

Optional and deliberately **not** part of `setup.sh`: torch is a ~200 MB download and no offline test
needs it, so a fresh clone should not pay for it. It pins ProteinMPNN's revision, because
"whatever main is today" is how `refcodes/` ended up with no recorded revisions. `--check` prints the
`DESIGNAGENT_MPNN_COMMAND` to export; with `DESIGNAGENT_ORBIT_LOCAL=true` and
`DESIGNAGENT_ORBIT_JOB_GPUS=0` the `hpc` path then runs the real model on this host through a real
broker. A 76-residue target takes a few seconds on CPU. It proves the model, not a scheduler, a
queue, an allocation or a GPU — the local PSI/J executor forks a process. The real thing is
`plans/AMAREL_ENDPOINT.md` §6, rung 4.

A child process that gets a path must get an absolute one. `LocalOrbitStack` resolves its work dir
in `__init__` because `_spawn` runs the broker and endpoint with `cwd=work_dir`, and
`orbit_work_dir` is the relative `data/orbit-local` by default — so a relative `--cert` resolved
against the work dir twice and the broker exited "TLS cert not found". Every test passed an absolute
`tmp_path`, so the suite could not see it; `DESIGNAGENT_ORBIT_LOCAL=true` from the repo root was
broken the whole time.

`./scripts/dev.sh` is the shortest path to a running stack and encodes four traps worth knowing even
if you start things by hand. Kuzu's lock means a second backend on the same data dir **dies** rather
than degrading, so `up` refuses and names the holder and `down` waits for the lock to clear.
**SIGTERM does not stop the backend** — asyncflow installs its own handler, reports a completed
shutdown, and leaves the process running, so every stop escalates (backlog C8). The local Orbit
endpoint registers a few seconds *after* the port opens, so `up` waits for `hpc: true`: a round
planned before then silently takes the heuristic branch. And `pgrep -f "port 8000"` matches the shell
running it and kills itself, which is why processes are found by reading `/proc`.

`config.yml` in the **current working directory** is read at `import flowgentic` time. Both
`agent_execution` and `logger` keys must be present — flowgentic does `APP_SETTINGS["logger"]["level"]`
with no fallback once it finds a file. Run everything from the repo root.

## Commands

```bash
./scripts/dev.sh up                             # backend + frontend, endpoint if installed
./scripts/dev.sh up --no-mpnn                   # no endpoint: the heuristic path
./scripts/dev.sh down | status | restart | logs
./scripts/amarel_endpoint.sh selftest           # Orbit endpoint on Amarel; the rest
#   is install | check | start | run | stop | status | logs
.venv/bin/python -m designagent --reload        # backend on :8000
.venv/bin/python -m designagent --check-config  # effective config, secrets masked
.venv/bin/python -m designagent --check-config --probe   # ...and try each credential
.venv/bin/python -m designagent --add-user NAME [--admin] # an account (logins on); --passwd, --list-users
.venv/bin/python -m designagent --gen-secrets-key       # a DESIGNAGENT_SECRETS_KEY
cd frontend && npm run dev                      # Vite on :5173, proxies /api
cd frontend && npm run build                    # tsc -b && vite build
cd frontend && npm test                         # 45 vitest/jsdom tests, no servers
cd frontend && npm run test:e2e                 # 2 Playwright tests in a real browser
cd frontend && E2E_LIVE=1 npm run test:e2e      # ...plus one real round trip

.venv/bin/python -m pytest -q                   # 417 offline tests, no network
.venv/bin/python -m pytest -q -m live           # 12 live tests; starts a real broker
.venv/bin/python -m pytest -q -m remote         # 8 tests against a real HPC endpoint
.venv/bin/python -m pytest -q -m llm            # 4 tests against a real API key
.venv/bin/python -m pytest tests/test_graph.py::test_design_loop_produces_lead_ensemble_and_artifacts -q
.venv/bin/ruff check backend tests              # E/F/I, line-length 100
```

`frontend/e2e/BROWSER_TESTS.md` is the by-hand tier: twelve user tests against a real endpoint,
written functionally so they survive refactors, covering what neither jsdom nor Chromium can — that a
real model round is distinguishable from a heuristic one by reading the screen. Run it after
anything that changes what the UI attributes.

`npm test` is jsdom only and needs nothing running: it covers the SSE reader's partial-frame buffer,
`handleFrame`'s reducers, the trace disclosure, the task chips (including that a remote task names its
interface and a local one does not), the settings panel's refusal to render a secret, and Markdown
sanitization. `npm run test:e2e` drives Chromium (one-time `npx playwright install
chromium`) and replays a canned stream through `page.route`, so it is deterministic and needs no
backend; `E2E_LIVE=1` adds the networked turn. **Every e2e test fails on an uncaught page error** —
that assertion is the one that catches a dead tab, which `tsc` cannot.

`remote` and `llm` are the validation tiers: each is excluded separately in
`addopts`, so `-m live` cannot pull in one that needs an allocation or spends
money. `remote` rehearses against `DESIGNAGENT_ORBIT_LOCAL=true` and otherwise
reads a real broker from the environment; `llm` needs `ANTHROPIC_API_KEY` and is
the only thing that measures the LLM classifier and the LLM summary.

Three of the `remote` tests exist for the protocol and cover plumbing no offline
test can reach: that a job's `directory` is honoured, that `custom_attributes`
is accepted rather than answered with an HTTP 500, and that a file stages back
out of a project directory. All three pass against the development stack, and all
three passed against Amarel on 2026-10-21 (rung 4b) — but only because that run
was made **from the endpoint host** with `--basetemp` on `/scratch`. The two
directory tests build their paths with `tmp_path`, so the job has to be able to
see them: `/scratch` is shared with the compute nodes, `/tmp` is node-local, and
from the VM both still fail (backlog A20). Run them with a
`DESIGNAGENT_ORBIT_CLIENT_NAME` of their own while the backend is up, or the two
clients steal each other's replies (A18) — and with
`DESIGNAGENT_ORBIT_JOB_GPUS=0` on a CPU queue, or every spec built through
`job_params` is rejected for asking for a GPU (A25).

The tier's one function-task test **skips** against a login-node endpoint rather
than failing. That is not a gap: Orbit gates rhapsody to the
`compute` and `standalone` host roles and the veto cannot be lifted by asking for
the plugin, while psij loads anywhere — a login-node endpoint serving psij alone
is the gateway topology upstream intends (`plans/AMAREL_ENDPOINT.md` §2, D4).

**The lint is clean and should stay that way** — `E,F,I` at line-length 100, pinned in
`pyproject.toml` so the rule set does not drift with the ruff version. Two places not to "fix": the
long line in `molviz_agent.py`'s `SYSTEM` prompt is reflowed rather than `noqa`'d, because a comment
inside a triple-quoted string becomes part of the prompt; and `except Exception:` in the degradation
paths is deliberate, which is why `BLE` is not enabled. Whether to widen to `B,UP` is an open
question with a measurement in `plans/BACKLOG.md`.

Changing backend code moves line numbers the deck cites, so run `slides/check_anchors.py` after any
edit — see Slides below.

`addopts = "-m 'not live'"` in `pyproject.toml` deselects the 12 live tests by default, because they
start a real broker. `-m live` on the command line overrides it; naming the file alone does not, and
collects nothing.

## Architecture

A LangGraph loop behind a FastAPI/SSE chat, with long work pushed off the event loop through a Task
Interface layer. The whole design follows from one constraint: **a chat interface must answer in
milliseconds while a fold takes tens of seconds and an HPC job takes hours.**

```
browser ─SSE─ app.py ── graph/build.py ── nodes ── Deps ── TaskManager ── interfaces
                                                    │                        │
                                            lake/store.py            local │ query │ hpc
                                         (Kuzu/SQLite/Parquet)                       │
                                                              flowgentic → asyncflow → rhapsody pool
                                                              Orbit (EndpointRuntime) │ Globus Compute
```

### The rules that are not obvious from one file

**Nodes reach the outside only through `Deps`** (`graph/deps.py`: settings, tasks, history,
artifacts). Never import a store or an interface into a node. This is convention, not an enforced
check, and it is the only reason 417 tests run with no network, no process pool and no endpoint — the
suite hands nodes an in-process `TaskManager` and a `tmp_path` lake.

**Every interface's `submit()` returns immediately with a handle whose `future` resolves later.**
Nothing in the graph awaits at submission time. A *failed submission* becomes a settled FAILED handle
rather than an exception (`tasks/manager.py`), so callers have exactly one shape to handle and
`gather()` never raises.

**State must not carry bulky payloads.** LangGraph serializes a checkpoint every turn, so coordinates
go to a content-addressed blob (`history.write_blob`) and travel as `structure_path`. Pinned by
`tests/test_graph.py::test_structures_are_not_carried_in_state`, which asserts `"ATOM  "` never
appears in serialized state. The regression is invisible — nothing breaks, it just grows every turn.

**A turn's graph input carries only what that turn contributes** — the user message, `session_id`,
and the two turn-scoped resets (`pending_results`, `status`). Everything else comes from the
checkpoint. Most of `DesignState` is reduced with `replace` (`state.py`), so any key present in the
input **overwrites** the saved value: passing a fresh `new_state()` wipes the reference design,
metric, lead, ensemble and view, and commits the blank. That was a real bug — the second prompt of
every session answered "No reference design is loaded yet" and re-ran the initializer. Pinned by
`tests/test_graph.py::test_state_survives_into_the_next_turn`, and `_send` in that file deliberately
mirrors `app.py` rather than threading state by hand, because threading it is what hid the bug.

**A pool worker is *handed* its `Settings`; it does not derive them.** `runtime.py` passes
`install_settings` as the `ProcessPoolExecutor` initializer, because task bodies call
`get_settings()` themselves (`tools/http.py`, `tools/esmfold.py`, and `llm.build_llm` reached from
`tools/molviz_agent.py`) and cache the answer for the worker's lifetime. The consequence is in
`runtime.needs_rebuild`: changing anything a worker reads — the API key above all — needs a **new
pool**, and a new pool needs a new flowgentic integration and compiled graph, so that case rebuilds
the whole runtime. Orbit credentials are read only in the server process, so they are applied in
place by `Runtime.reconfigure`. `config.adopt` is for that in-place case and `install_settings` for a
fresh process: the first keeps the override bookkeeping, the second resets it.

**Logins are off unless `DESIGNAGENT_AUTH_ENABLED=true`, and off they change nothing.** With no
`runtime.auth`, `app._user` returns None and every ownership check passes, which is why the rest of
the suite needed no edit when they arrived. On (the Linode), every route except `/api/login`,
`/api/logout` (which reads a cookie but does not require one, so signing out is idempotent) and a
bare `/api/health` needs the session cookie. A chat session must be the caller's (404 otherwise),
session ids are minted by `POST /api/sessions`, and the operator's settings routes need the admin
role instead of the shared token. `tests/test_auth.py` is the contract. One consequence to remember:
`/api/health` tells an anonymous or non-admin caller only `{ok, auth}`, so anything that waits on
`hpc: true` — `scripts/dev.sh` does — has to sign in first (backlog A21).

**A turn's user travels in a `ContextVar`, never in `configurable` or state.** `context.current_turn`
is set inside `/api/chat`'s `run_graph` task, and asyncio copies it to every task the turn creates.
`Deps.settings` is a property that resolves it, so every node gets the user's own key and endpoint
without knowing users exist, and `TaskManager._hpc` routes `hpc` work to that user's interface. Not
`configurable`: LangGraph's `get_checkpoint_metadata` copies every string in it into checkpoint
metadata, so a key put there would be saved every turn. Pinned by
`test_no_user_secret_is_persisted_by_a_turn`, which scans the checkpointer, the task snapshots and
the data dir for the key.

**A pool task that calls the model gets the key as a call argument.** `TaskDef.needs_llm` marks it,
and `tasks/local.llm_credentials` captures the turn's key at submission and passes it as `_llm`. It
is **never** put in `spec.params`, which `record_task_submitted` writes to the lake and the task
routes return. A worker's own settings were fixed at fork and are the operator's, and a plain user
must not fall through to them: `llm.settings_for_task` keeps an empty key empty. ChemGraph reads
its key only from the environment, so `run_chemgraph` sets the variable for the call and restores
it.

**User credentials are a fixed, small surface** (`auth/credentials.USER_FIELDS`): the Anthropic key
and model, and an Orbit broker, token, cert, endpoint, account and queue. They are stored as Fernet
ciphertext with each value bound to its user and field, and validated by rejection. A broker must
be on `DESIGNAGENT_ORBIT_ALLOWED_BROKERS`, because it is a host this server will dial and hand a
token to. A plain user's baseline has every operator credential *removed*; an admin's is the
operator's. Every path, shell and scheduler choice stays operator-only. Each user's endpoint gets
its own `OrbitInterface` under a unique client name (`runtime.OrbitRegistry`, backlog A18).

**A secret is `SecretStr` and is rendered only by `config.mask`.** No response body carries a value —
`config.describe` emits `{present, source, hint}` — and a `logging.Filter` in `app.lifespan` scrubs
live secret values out of records, because the text that could contain one is a provider's exception,
not ours. `llm.complete(on_fallback=...)` is what separates "no key" from "key rejected": nodes hand
it `deps.llm_caveat`, which records the reason for `/api/health` and pushes a line onto the `warnings`
channel so a degraded turn says so in its reply instead of looking normal. Do not add a permissive
CORS policy: the settings-write routes are open on loopback precisely because no CORS middleware
exists, so a cross-origin JSON `PUT` dies at the preflight.

**Every user-visible sentence is attributable.** A node that authors text puts `reply_source` in its
update — `interpreter:_rule_based_summary`, `coordinator:llm`, `initializer:summary_line` — and the
value is grep-able straight to the function. `app.py` puts it, and the node, on the `message` frame;
the `traced()` wrapper in `build.py` records it per node in the `trace` channel. Without this a reply
reads the same whether a rule or a model wrote it, and the only way to find the code was to grep for
the sentence. The prose map below says which function writes what.

**The trace is turn-scoped and `replace`, not accumulating.** `graph/trace.py` explains why it lives
in state (several sessions share the process, so a module-level accumulator would interleave them) and
why the channel cannot use an appending reducer: `[*saved, *[]]` is `saved`, so the per-turn reset in
`app.py`'s payload would not reset anything. The wrapper appends to the list it read instead, which is
correct only because nodes run one at a time — a fan-out node would need a reducer and a reset
sentinel. Entries hold counters and names, never payloads.

**Only prose may stream to the chat.** `llm.complete(stream=True)` is opt-in and only the two
user-facing calls pass it; everything else is tagged `langsmith:nostream`. LangGraph's messages stream
emits a whole message on `on_llm_end` whether or not the model streamed, so an untagged call puts the
intent classifier's JSON and the round planner's JSON into the assistant's bubble. Invisible without
a key, which is why it survived this long.

**Routing is `Command(goto=..., update=...)` from the node body**, so the routing decision and the
state write are one atomic return. `build.py` declares `destinations` per node for validation; the
only static edge is `START → coordinator`.

**A view request's own words reach the visualization generator**, not the campaign goal: the
coordinator writes `goal` only for `design`/`initialize`, so on a "label the active site" turn `goal`
is still the design objective. `_make_visualization` falls back to `last_user_text(state)`
(`graph/state.py`) for this reason.

**Process-pool constraints** (`runtime.py`, `tools/`): task bodies live at module level with no
closures over clients, HTTP/LLM clients are constructed *inside* the body, and the entry point needs
its `__main__` guard or uvicorn re-imports under fork.

**Lake writes are individually guarded.** A storage failure must not lose a round the user waited two
minutes for: the analyst catches per-tier, falls back to `_rank_in_memory`, and pushes a message onto
the `warnings` state channel, which the interpreter appends to its reply as "Caveats from this run".

**Kuzu takes an exclusive file lock.** A running backend holds it, so read `data/lake/graph` by
copying it aside (see `slides/run_model.py`). Tiers 2 and 3 are a plain SQLite file and Parquet, and
can be read in place. It also sizes its buffer pool at ~80% of the host's RAM by default, which is
most of a small VM and is why `DESIGNAGENT_KUZU_BUFFER_POOL_MB` exists; the 4 GB Linode runs with
256. Environment-only, like `data_dir`.

### The protocol node

`graph/nodes/protocol.py` runs the enzyme-redesign protocol — the multi-day cluster pipeline from the
`enzyme-redesign-protocol` skill — as **one stage per turn**. It is the only node that answers the
user every turn and never routes onward, and the only one whose state has to survive the checkpoint
to be useful. Five things about it are not obvious:

**The turn boundary is the checkpoint mechanism.** The skill names three points where the run must
stop and wait for a human. There is no `interrupt()` anywhere in this repo, so instead a stage sets
`protocol.awaiting` and returns; the next message answers it. `STAGES` maps a stage to what it runs
and `ANSWERS` maps an `awaiting` key to how a reply is read — two tables, so each entry is testable
on its own. Consequence: **`coordinator` routes to `protocol` on `awaiting` before classifying
intent at all**, because "liu", "310,364" and "go" all classify as `chat`, which would answer from
session state and leave the campaign waiting forever. `ESCAPE_WORDS` is the way out.

**Two job modes, and which one a step uses is the decision that matters.** `artifacts.wrap` rewrites
any spec declaring `inputs`/`outputs` into a `bash -lc` script working in a `mktemp -d` that its own
`trap` deletes, and globs the outputs *there*. So compute specs declare **neither** and set
`directory` to a `$PROJ` subdirectory — their files persist for the next stage and their stdout is
the job's stdout, which the task chip tails. Transfer specs (`push_job_spec`, `fetch_job_spec`)
declare both, name **absolute** sources, and run on the `local` PSI/J executor: on the endpoint host,
no queue slot. Unwrapped does not mean "no shell" — every spec is still `bash -lc`, because that is
where `module` is defined. Parameters never ride in `environment`: the Slurm template renders
`export name=value` unquoted, so `--sampling_temp "0.1 0.2 0.3"` could not travel there.

**The skill's `#SBATCH` headers are dead on this path**, because PSI/J generates its own submit
script. `protocol/specs.py` is therefore the source of truth for every walltime, core count and
memory figure, and `ResourceSpecV1.memory` is in **bytes** — `{"memory": 32}` renders `--mem=0K`,
which Slurm reads as the whole node, so everything goes through `gib()`. Two things are lost in the
move: `--requeue`, so a preempted job is just a failure, and the per-job `slurm.%N.%j.out` names, so
the lab notebook's job table comes from `handle.meta["native_id"]` plus `sacct`.

**Progress is read from the filesystem, never from a handle.** Handles and pollers are in-memory, a
`close()` deletes the stdout directory, and `register_session()` mints a fresh id every start — so
re-attaching to an in-flight job cannot be built without changing `refcodes/`. `af3_collect`
therefore consults no handle: it lists the output tree and reads the small summary files, which makes
it idempotent, restart-proof and identical whether the submit was minutes or days ago. The general
form: **the data outlives the handle, so recovery is a re-fetch, not a re-attach.**

**Everything from a chat message is validated before it reaches a script.** `NAME`, `UNIPROT`,
`DOMAINS`, `CAT_RES` and the netid end up inside a `bash -lc` body, in argv and in `$PROJ` paths that
the endpoint writes to under the site's allocation. `protocol/inputs.py` rejects rather than
sanitizes, and the `protocol_*` settings are environment-only for the same reason `mpnn_command` is.
Guarding the settings and not the inputs would have been the wrong half.

The protocol's own scripts stay in the skill repository and are read through `protocol_scripts_dir`,
not vendored: copying them in would fork them, and `plans/BACKLOG.md` already records one place where
this repo's port and the skill's original disagree. `plans/AMAREL_ENDPOINT.md` is the deployment
prerequisite — nothing here runs until a broker and an endpoint exist.

### Deliberate deviations

`DESIGNAGENT_WRAP_NODES` ships **false**, against the original design, which had every node wrapped as
a flowgentic `EXECUTION_BLOCK`. That runs the node body on asyncflow's loop, outside LangGraph's
runnable context, so `get_stream_writer()` raises and every status line and token is dropped
*silently* — the graph still completes. Tasks are still wrapped (`FUNCTION_TASK`), which is where the
parallelism matters. Do not flip this default without re-verifying that status lines still stream.

The visualization agent emits a **sanitized JSON view spec**, not generated JavaScript, even though
the brief said "codes a browser-based visualization". Shipping LLM-written JS into the viewer would
let a prompt get code into the page. `tools/molviz_agent.py::sanitize_spec` repairs or drops every
field; a bad generation degrades to a plain cartoon.

With no HPC endpoint attached, `proteinmpnn` falls back to a heuristic proposer. The catalog entry
stays pointed at `proteinmpnn_local_fallback`, which relabels at the point of substitution — *"no HPC
endpoint was attached, so the job was never submitted"* — because pointing it straight at
`propose_variants` made a heuristic round indistinguishable from a real one in the output. Keep that
label honest.

With an endpoint, the samples are real, and **their provenance is read rather than asserted**:
`variants_from_mpnn_fasta` lifts `model_name`/`git_hash` out of ProteinMPNN's own FASTA header, and a
run that declares neither earns a caveat saying so. The cheapest discriminator in a finished round is
the mutation count — the heuristic only ever emits single substitutions, so a multi-position design
did not come from the table.

**ProteinMPNN's result has to be adapted before the graph can see it.** A job result is
`{job_id, state, exit_code, stdout, artifacts}`; `_collect_variants` reads `result["variants"]`.
`_adapt_mpnn_records` (`nodes/orchestrator.py`) bridges the two so `_collect_variants` stays generic,
and the adapter itself is a pure function in `tools/proteinmpnn.py` — it cannot live in the task body,
because on the `hpc` path `_submit_job` reads `params["job_spec"]` and the body never runs. That is
why `parse_mpnn_fasta` sat with no callers for so long. If a round yields nothing usable the
orchestrator submits the heuristic proposer as a **fresh** spec rather than ending as "No candidate
designs were produced" — fresh because `interface_for` pops `_interface` out of the params it is
given.

**An `hpc` spec must carry a `job_spec`, or the submission is `/bin/true`.** `OrbitInterface._submit_job`
reads `params["job_spec"]`, and `to_psij_spec({})` defaults the executable; the orchestrator fills it
from `mpnn_job_spec`/`fold_job_spec` via `_job_params`, which also supplies the executor, account and
queue from settings. Two traps found by running it: PSI/J names its resource fields differently from
us (`processes` → `process_count`, `gpus` → `gpu_cores_per_process`) and answers **HTTP 500** on an
unexpected one, which `PSIJ_RESOURCE_KEYS` now maps; and `task_timeout_sec` (900 s) is shorter than a
job's own walltime, so `_batch_timeout` widens it for `kind="job"` — otherwise a queued job is failed
before the scheduler starts it.

### Working around the middleware

Three flowgentic behaviours are worked around at the call site, each commented there:
`ConcurrentExecutionBackend` lives in `rhapsody.backends` (not `radical.asyncflow`, as its examples
claim); `_default_retryable_exceptions()` does `except Exception: raise` around optional
`httpx`/`aiohttp` imports, making both hard requirements; and `RetryConfig` defaults to a 30 s timeout
with 3 attempts, which would cancel and silently re-run a fold, so tasks pass
`timeout_sec=None, max_attempts=1`.

Orbit's clients are all synchronous (wrapped in `asyncio.to_thread`) and its push callbacks arrive on
a listener thread (hopped with `call_soon_threadsafe`). Its terminal task event omits stdout, so
`_finish_task_enriched` re-fetches; a FAILED job carries only an exit code, so the reason is
synthesized. `tasks/hpc/local_orbit.py` stands up a real broker + endpoint for tests — note that
`--no-auth` disables ingress auth only, and the broker still requires a cert and key.

**A job's stdout is read once, from offset 0, and it is the only way a file comes back.** Three
things follow, and all three were bugs first. `drain_logs` and `_poll_job` tail the same file from
independent cursors, so only the drain may write `handle.log_tail` — it is a capped UI tail, never
the job's output, and `_finish_job` returning it duplicated every byte up to three times. A terminal
push event can beat the scheduler's flush, so `_read_whole_stdout` waits for the file to appear, but
**only** for a job that staged something (`handle.meta["expects_artifacts"]`); everything else may
legitimately print nothing and must settle at once. And `_finish_job` builds a fixed result dict, so
anything new has to be named in `_STAGING_KEYS` or it is silently dropped.

**Staging is in band, because the broker forwards neither `outputs` nor `stdin_text`** (backlog C6,
and `refcodes/` is gitignored so a fix there is unshippable). `tasks/hpc/artifacts.py` rewrites any
spec declaring `inputs`/`outputs` into a `bash -lc` script: inputs are gzipped, base64'd and split
across argv elements (`MAX_ARG_STRLEN` caps a single element at ~128 KiB, not all of them), the real
command's own output goes to **stderr** so stdout stays a clean data channel, and each output is
printed as a framed block with its size and sha256. `outputs: ["**"]` means "everything this job
created", for a job whose output shape is not known until it runs. Frames are not optional: `bash
-lc` sources the login profile, and a stray `>` line is a valid FASTA header. `TaskManager`
materializes the returned bytes into blob paths before anything persists them, so `_slim` never sees
a megabyte. A spec declaring neither field is passed through unwrapped.

### What the browser actually shows

A design round's provenance is legible in three places, and nowhere else. The **task chip** names a
task's `interface` when it is not `local` (`chat/TaskChips.tsx`), so a `proteinmpnn` chip reads
`hpc`; expanding it shows a streaming log tail, which only the Orbit PSI/J path can produce
(`supports_log_stream` is false on both local interfaces). The session summary artifact's
**`## Tasks run`** section prints `` `proteinmpnn` (hpc): DONE `` straight from the Kuzu Task row
(`artifacts/render.py`). And the **Ensemble table** carries an `mpnn score` column, whose only source
is ProteinMPNN's negative log-likelihood. A design's `provenance.source` is plumbed end to end and
rendered by nothing; `state.warnings` reaches the user only inside the reply and as a count in the
trace disclosure.

`SettingsPanel.tsx` builds its form from **hardcoded** `FIELDS` and `GROUPS`, hand-maintained against
`CREDENTIAL_FIELDS` — a group the backend reports but the panel omits is dropped silently, which is
how the `mpnn` group stayed invisible after being added server-side, and `globus` still is —
`test_every_reported_credential_group_reaches_the_settings_panel` now reads the component and
fails on a group that is reported but unrendered, with the exceptions named.

**Reported is not the same as writable, and the direction matters.** With logins off,
`PUT /api/settings` is unauthenticated on loopback by design (`_authorize_write`), so the write
surface is a security boundary, not a convenience. With logins on it needs an admin, but the
operator's settings are still everyone's defaults, so the same care applies. `mpnn_command` and `mpnn_prologue` are reported and deliberately
**refused**: both name shell the server will run — the prologue is appended to the job script
verbatim (`tasks/hpc/artifacts.py`) and the command names the executable — so accepting them would
make that route arbitrary code execution, as the server user locally and on the endpoint under the
site's allocation remotely. They are environment-only, like `data_dir` and `pool_workers`, and the
panel shows them `readonly` so a user sees the value without being offered an edit that would 422.

The invariant worth enforcing is therefore one-way: everything **writable must be reported**, so a
change is never invisible (`test_a_writable_field_is_always_reported`). The converse is not a rule;
the exceptions are listed with their reasons in `NOT_REMOTELY_WRITABLE`
(`tests/test_settings.py`), and a test asserts that set is exactly right so neither a forgotten
field nor a quietly widened surface passes.

## Who writes the text the user reads

Needed often enough to write down. With no key every one of these is the rule-based path; the
`reply_source` column is what a running session reports, and `how this answer was made` in the UI
shows it under the reply.

| Text | Written by | `reply_source` |
| --- | --- | --- |
| `Loaded 1OIL — … Found 5 relevant paper(s).` | inline, `nodes/initializer.py` | `initializer:summary_line` |
| `I could not identify a protein target from that.` | inline, `nodes/initializer.py` | `initializer:no_target` |
| `I have no sequence to redesign yet.` | inline, `nodes/orchestrator.py` | `orchestrator:no_sequence` |
| `Round 1: …` progress line | `_round_line` (`nodes/analyst.py`) | — (a status, not a reply) |
| a round's task-failure note | `_failure_note` (`nodes/orchestrator.py`) | — (a status) |
| `There is nothing loaded to display yet.` / `I could not build that view.` | inline, `nodes/analyst.py` | `analyst:nothing_loaded` / `analyst:no_view` |
| `PDB 1OIL; functional residues in blue` | `_caption` (`tools/molviz_agent.py`), **in a pool worker** | `analyst:molviz:_caption` |
| the session summary | `_rule_based_summary` (`nodes/interpreter.py`) or the LLM | `interpreter:_rule_based_summary` / `interpreter:llm` |
| the `Caveats from this run` block | inline, `nodes/interpreter.py`; appended to the summary | — (part of the same message) |
| a chat answer | `describe_state` (`nodes/coordinator.py`) or the LLM | `coordinator:describe_state` / `coordinator:llm` |
| the markdown/docx artifact | `summary_markdown` / `summary_docx` (`artifacts/render.py`) | — (an artifact) |

Every stage of the protocol node authors its own sentence, and all of them are rule-based — a stage
is reporting a measurement, so none of this text comes from a model. The value is
`protocol:<stage or answer>:<what it is>`, grep-able to the function:

| Text | Written by | `reply_source` |
| --- | --- | --- |
| the campaign's input form, and the conservation question | `stage_intake` (`nodes/protocol.py`) | `protocol:stage_intake:ask_inputs` / `:ask_method` |
| `Loaded AF-… 785 residues … pLDDT mean 88.0.` | `stage_structure` | `protocol:stage_structure:summary_line` |
| `Conservation done. cpos50: 1284 designable.` | `stage_conservation` | `protocol:stage_conservation:summary` |
| `Redesign done. cpos50_halo: 48 designs…` | `stage_mpnn` | `protocol:stage_mpnn:summary` |
| the selected-design list and the GPU-hour cost | `stage_score` | `protocol:stage_score:selection` |
| `3 of 10 designs have finished folding.` | `stage_af3_collect` | `protocol:stage_af3_collect:in_progress` / `:complete` |
| `No HPC endpoint is attached, so no job was submitted.` | `unavailable` | `protocol:unavailable:no_endpoint` / `:site_unconfigured` |
| a refused answer at a checkpoint | whichever `answer_*` raised `InvalidInput` | `protocol:invalid_input` |
| `NOTEBOOK.md` | `protocol/notes.py`, appended per stage under one artifact id | — (an artifact) |

## Backlog

Open issues and their evidence live in `plans/BACKLOG.md`. Add to it when you find something worth
fixing later rather than leaving it in a commit message; each entry names the file and how to
reproduce.

`plans/AMAREL_ENDPOINT.md` is how to stand up the Orbit broker and endpoint the protocol needs, with
an acceptance ladder whose rungs are meant to be filled in with what they actually returned. The
pair is up: the broker runs on the Linode under systemd and the `amarel3` endpoint has registered
with `psij,sysinfo` across the internet, and rung 4 has put real Slurm jobs through it — the path
works, which retired backlog A1. What it found is **A18–A20** and **A25**, the sharpest being that a
missing `--chdir` runs a job in `/tmp` and reports success. Rung 4b re-ran the tier from `amarel3`
itself on 2026-10-21 — 7 passed, 1 skipped — which put a test behind `directory` and the protocol's
fetch shape for the first time, and confirmed **D4**. No protocol stage has run yet, so every
walltime, core count and memory figure in `protocol/specs.py` is still a guess.

`plans/PEAT_MIGRATION.md` is how this rewrite moves into `KhareLab/PEAT` — the archive-then-force-push
shape our write-only permissions force, the traps a renaming pass walks into, and what was redacted
before a public lab repo got it. **The local half is done and nothing has been pushed:** the ladder in
§4 is unrun. Read §2 before touching a name or an address anywhere in this repo. The displayed name is
`PEAT`; the Python package and the `DESIGNAGENT_` prefix deliberately still are not (backlog B12).
Every host and account identifier in `plans/` is a placeholder, keyed in `AMAREL_ENDPOINT.md`.

`plans/LINODE_DEPLOY.md` is the plan *and* the record for the Linode (<linode-ip>) that hosts the
broker and the agent. **Phases 0, 1 and 2 have shipped:** host setup, the UI behind Caddy, and then
per-user logins as the gate — Caddy's basic auth was removed once they worked, and the Phase 1
Caddyfile is kept beside it. Phase 3, a broker per user, is open, and what Phase 2 leaves open is
backlog A21. The trap it records is still worth knowing: behind a same-host proxy,
`bound_to_loopback` is true for every caller, so the loopback-open settings routes are open to the
internet unless something closes them. With logins on the admin role does (A17), and
`DESIGNAGENT_ADMIN_TOKEN` is then not consulted at all; with logins off that token is the only thing
standing there.

## Slides

`slides/` holds a code-walk deck built from real data. If you change backend code that a slide cites:

```bash
.venv/bin/python slides/check_anchors.py        # 46 cited line numbers, re-derived
.venv/bin/python slides/run_model.py            # re-mine run.json — read M2 first
.venv/bin/python slides/bench_staging.py        # re-measure the staging channel → staging.json
.venv/bin/python slides/make_script.py          # regenerate DECK_SCRIPT.md from the deck's notes
NODE_PATH=<dir with pptxgenjs> node slides/build_deck.js
```

`DECK_SCRIPT.md` is generated — edit the `addNotes` blocks in `build_deck.js`, not the Markdown.
`run.json` is generated too, but **do not re-run `run_model.py` casually**: it aggregates the whole
lake and takes whichever campaign Kuzu lists first, so on today's data dir it replaces the deck's
worked example wholesale and exits 0 (backlog M2). The committed file is the 2026-10-01 campaign
with only its `code` block refreshed.

`staging.json` is generated by `bench_staging.py` and is deliberately a **separate** file, so
re-measuring F5 on another host cannot touch that campaign. It runs the real `wrap()`/`collect()`
through a local `bash -lc` in a temp dir, and it is where backlog **A22** came from — the outbound
cap is checked on raw bytes while the cost is paid in compressed ones, and the inbound half has no
cap at all and dies at `exec`.

The deck is in three acts — functionality (what new tasks it makes possible), performance (what it
costs to run, and what was never measured) and usability (what it is usable *as*: application,
platform, component). Slides are numbered within that spine, so a slide number in `CODE_FOR_DECK.md`,
`DECK_OUTLINE.md` and `check_anchors.py` is load-bearing; the framing itself is written up in the
`code-walk-deck` skill.
