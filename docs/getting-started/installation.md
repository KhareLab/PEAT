# Installation

!!! danger "A fresh public clone cannot be built yet"

    PEAT depends on four middleware packages — `radical.asyncflow`, `rhapsody`, `flowgentic` and
    `radical.orbit` — that are installed as **local editable checkouts** from a `refcodes/`
    directory. That directory is gitignored and is not in the repository, so `./scripts/setup.sh`
    on a clean clone stops and names what is missing rather than building a broken environment.
    You need those four checkouts separately; the revisions this project was last verified
    against are recorded in `scripts/setup.sh`. This is tracked in `plans/BACKLOG.md` as *A fresh
    clone cannot be built*, and it is the first thing to fix for outside users.

## Prerequisites

- **Python 3.12**, via [`uv`](https://docs.astral.sh/uv/) — `setup.sh` builds the virtual
  environment with `uv venv --python 3.12`. The package metadata allows 3.11 or newer.
- **Node.js** with npm, for the frontend.
- Local checkouts of the four middleware packages under `refcodes/` (see the note above).
- Optional: an `ANTHROPIC_API_KEY`, and an Orbit broker and endpoint for real HPC work. Neither
  is required to start the application.

## Install

```bash
git clone https://github.com/KhareLab/PEAT.git
cd PEAT
./scripts/setup.sh              # builds .venv and verifies it
cd frontend && npm install && cd ..
```

`scripts/setup.sh` is the **only supported install path**, and `uv sync` is not an alternative.
`flowgentic` pins two of its own dependencies to git URLs that fight the local checkouts, so it
is installed `--no-deps`; `radical.orbit` is installed the same way, with `opentelemetry-sdk` and
`nvidia-ml-py` added by hand — without the OpenTelemetry SDK every rhapsody session inside Orbit
fails to open, and the whole live test tier skips itself on an import error rather than failing.
`pyproject.toml` carries the full reasoning where a `[tool.uv.sources]` block used to be.

Verify an existing environment without installing anything — run this after every pull, since it
fails with the missing piece named rather than letting the application die deep inside an import:

```bash
./scripts/setup.sh --check
```

### Optional: a real ProteinMPNN

```bash
./scripts/setup_mpnn.sh          # ProteinMPNN (CPU) in .venv-mpnn
./scripts/setup_mpnn.sh --check  # verify, and print the variable to export
```

Deliberately **not** part of `setup.sh`: Torch is a ~200 MB download that no offline test needs.
The script pins ProteinMPNN's revision, and `--check` prints the `DESIGNAGENT_MPNN_COMMAND` to
export. With that plus the local Orbit stack, the `hpc` path runs the real model on your own
host through a real broker — a 76-residue target takes a few seconds on CPU. That proves the
model, not a scheduler, a queue or an allocation.

## Configure

Settings come from environment variables with the prefix `DESIGNAGENT_`; `.env.example` lists
them all with comments, and `.env` is resolved against the current working directory.

```bash
cp .env.example .env
.venv/bin/python -m designagent --check-config           # what is in effect, secrets masked
.venv/bin/python -m designagent --check-config --probe   # ...and whether each credential works
```

Precedence is: values set in the running application, then the environment, then `.env`, then the
defaults in `config.py`. A credential is never rendered in full — responses carry presence,
source and a masked hint — and nothing refuses to start: a missing or rejected credential shows
up in `/api/health`, is badged in the UI, and makes the turn say in its reply that it fell back.

The settings that change behaviour most:

| Variable | Effect |
| --- | --- |
| `ANTHROPIC_API_KEY` | Enables LLM reasoning. Without it every node uses its rule-based path |
| `DESIGNAGENT_FOLD_BACKEND` | `esmatlas` (public API, ≤400 aa), `local`, or `hpc` |
| `DESIGNAGENT_MAX_ROUNDS` | Redesign rounds before the loop summarizes |
| `DESIGNAGENT_POOL_WORKERS` | Process-pool size |
| `DESIGNAGENT_ORBIT_ENABLED` + `RADICAL_ORBIT_*` | Remote HPC, with `DESIGNAGENT_ORBIT_PSIJ_EXECUTOR`, `_ACCOUNT` and `_QUEUE` for the site |
| `DESIGNAGENT_ORBIT_LOCAL` | Bring up a localhost broker and endpoint at startup, for development |
| `DESIGNAGENT_AUTH_ENABLED` + `DESIGNAGENT_SECRETS_KEY` | Per-user logins, and the Fernet key their credentials are stored under |
| `DESIGNAGENT_KUZU_BUFFER_POOL_MB` | Caps Kuzu's buffer pool; `0` keeps its default of ~80% of RAM, which on a small VM is most of the machine |

A group of settings is **environment-only and refused over HTTP** — the data directory, the pool
size, the ProteinMPNN command and prologue, and the whole `DESIGNAGENT_PROTOCOL_*` group. Each
names a path or a shell word the server will run, locally or on the endpoint under the site's
allocation, so accepting them over a settings route would make that route arbitrary code
execution. They are reported read-only in the UI so you can see the value without being offered
an edit that would fail.

!!! note "Run everything from the repository root"

    `config.yml` is read from the **current working directory** at `import flowgentic` time, and
    both its `agent_execution` and `logger` keys must be present.

## Run it

```bash
./scripts/dev.sh up           # backend on :8000, frontend on :5173, endpoint if installed
./scripts/dev.sh up --no-mpnn # no endpoint: the heuristic path
./scripts/dev.sh down | status | restart | logs
```

Or start the two halves by hand — `.venv/bin/python -m designagent --reload` and, in another
shell, `cd frontend && npm run dev`. `dev.sh` does the same thing and also handles four traps
worth knowing about either way: Kuzu's exclusive lock means a second backend on the same data
directory **dies** rather than degrading, so `up` refuses and names the holder; the backend does
not stop on `SIGTERM`, because the workflow engine installs its own handler, reports a completed
shutdown and leaves the process running, so every stop has to escalate; the local Orbit endpoint
registers a few seconds *after* the port opens, so `up` waits for the health endpoint to report
`hpc: true` — a round planned before then silently takes the heuristic branch; and a naive
`pgrep` for the server's command line matches the shell running it and kills itself.

Then open <http://localhost:5173> and ask for something, for example *"redesign 1UBQ to improve
thermostability"*.

### Against a real HPC endpoint

```bash
DESIGNAGENT_ORBIT_ENABLED=true \
RADICAL_ORBIT_BROKER_URL=https://<broker-host>:<port> \
RADICAL_ORBIT_BROKER_CERT=/path/to/broker_cert.pem \
RADICAL_ORBIT_BROKER_TOKEN=<token> \
DESIGNAGENT_ORBIT_PSIJ_EXECUTOR=slurm \
DESIGNAGENT_ORBIT_ACCOUNT=<account> \
.venv/bin/python -m designagent
```

Standing the broker and the endpoint up is its own exercise; `plans/AMAREL_ENDPOINT.md` in the
repository is the runbook, with an acceptance ladder whose rungs record what they actually
returned. For development, `DESIGNAGENT_ORBIT_LOCAL=true` has the server bring up a localhost
broker plus an endpoint itself, so the real client path is exercised with no allocation.

## Turning logins on

Logins are off unless `DESIGNAGENT_AUTH_ENABLED=true`, and off they change nothing: one implicit
user, and every ownership check passes. On, every route but login, logout and a bare health check
needs the session cookie, a chat session must belong to the caller, and the operator's settings
routes need an admin role.

Accounts are made from the CLI, in the server's own environment, never over HTTP:

```bash
.venv/bin/python -m designagent --gen-secrets-key          # a DESIGNAGENT_SECRETS_KEY
.venv/bin/python -m designagent --add-user NAME [--admin]  # an account
.venv/bin/python -m designagent --passwd NAME              # set a password
.venv/bin/python -m designagent --list-users
```

Each signed-in user then supplies their own Anthropic key and their own Orbit broker, token,
certificate, endpoint, account and queue, which replace the operator's for their turns only.
Read [what a multi-user deployment shares](../concepts/architecture.md#across-a-group) before
inviting a second person: the design history and the broker are both shared.

!!! tip "Behind a reverse proxy"

    Behind a same-host proxy every caller looks like loopback, so the settings routes that are
    deliberately open on loopback are open to the internet unless logins or an admin token close
    them. Keep the secrets key in the same backup as the broker's key — losing it makes every
    user re-enter their credentials.

## Tests

```bash
.venv/bin/python -m pytest -q            # 417 tests, offline; stubs replace every tool
.venv/bin/python -m pytest -q -m live    # 12 tests; starts a real Orbit broker + endpoint
.venv/bin/python -m pytest -q -m remote  # 8 tests; submits to a real HPC endpoint
.venv/bin/python -m pytest -q -m llm     # 4 tests; calls Anthropic with a real key

cd frontend && npm test                      # 45 tests in jsdom; no servers needed
cd frontend && npm run test:e2e              # 2 tests in Chromium, canned stream
cd frontend && E2E_LIVE=1 npm run test:e2e   # ...plus one real round trip
.venv/bin/ruff check backend tests           # E/F/I, line length 100
```

The offline tier needs no network, no process pool and no endpoint, because nodes reach the
outside only through injected dependencies. The other three tiers are each deselected separately
in `pyproject.toml`, so that `-m live` cannot drag in a tier that needs an allocation or spends
money — and a marker is the only thing that selects them, so naming the file alone collects
nothing. Chromium needs a one-time `npx playwright install chromium`.
