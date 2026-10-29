# Code for the deck — staging file

Blocks to put on slides for the designagent code walk. Companion to `DECK_OUTLINE.md` (not yet
written) and `build_deck.js` (not yet written); slide numbers and snippet IDs are meant to match
across all three.

**Derived against `main` @ `7e72b74`** (2026-10-09; the first pass was `e8467e6`, 2026-10-01). Every
anchor below was verified by reading the cited lines, not from memory, and `check_anchors.py`
re-derives all of them mechanically.

**The deck is in three acts** — functionality (slides 6–10), performance (11–17), usability (18–21) —
so the IDs were renumbered when the slides were. An ID is `S<slide>-<letter>`, and the mapping is in
this table rather than in anyone's memory: what used to be `S16-A` is now `S10-A`, and the two
demoted slides keep their blocks under `B3` and `B4`.

**Fidelity marking, on every block:**

| Mark | Meaning |
|---|---|
| **VERBATIM** | byte-identical to the source at the cited lines — safe to present as "this is the code" |
| **TRIMMED** | verbatim lines with whole lines removed (elisions marked `...`); nothing rewritten |
| **EDITED** | restructured for legibility — **say so on the slide**, and show the real file in the terminal |
| **ILLUSTRATIVE** | **not from the repo** — label it on the slide, never let it be quoted as shipped code |

Priority: ★ must show · ○ show if time. ~20 lines is the legible maximum at presentation size.

| ID | Slide | Source | Lines | Fidelity | Pri |
|---|---|---|---|---|---|
| S7-A | 7 | `backend/designagent/graph/build.py:147–158` | 12 | VERBATIM (two comment lines elided) | ★ |
| S7-B | 7 | `backend/designagent/graph/nodes/coordinator.py:145–177` | 20 | TRIMMED | ★ |
| S8-A | 8 | `backend/designagent/graph/state.py:211–240` | 20 | TRIMMED | ★ |
| S8-B | 8 | `backend/designagent/graph/state.py:159–204` (reducers) | 16 | TRIMMED | ○ |
| S8-C | 8 | `tests/test_graph.py::test_structures_are_not_carried_in_state` | 12 | TRIMMED | ★ |
| S8-D | 8 | `backend/designagent/graph/nodes/orchestrator.py:273–297` | 19 | TRIMMED | ★ |
| S8-E | 8 | `backend/designagent/graph/nodes/orchestrator.py:314–330` | 17 | VERBATIM | ○ |
| S9-A | 9 | `backend/designagent/lake/graph.py:24–51` (the schema) | 20 | TRIMMED | ★ |
| S9-B | 9 | `backend/designagent/lake/store.py:89–120` | 18 | TRIMMED | ★ |
| S10-A | 10 | `backend/designagent/graph/nodes/coordinator.py:269–274` | 6 | VERBATIM (the slide wraps one line) | ★★ |
| S10-B | 10 | `backend/designagent/graph/nodes/protocol.py:865–882` (`STAGES`, `ANSWERS`) | 18 | TRIMMED | ★ |
| S13-A | 13 | `backend/designagent/tasks/base.py:22–34, 62–67, 70–83` | 20 | TRIMMED | ★ |
| S13-B | 13 | `backend/designagent/tasks/base.py:99–127` (`TaskHandle`) | 18 | TRIMMED | ★ |
| S13-C | 13 | `backend/designagent/tasks/base.py:155–193` (the ABC) | 20 | TRIMMED | ★★ |
| S13-D | 13 | `backend/designagent/tasks/manager.py:134–151` | 18 | VERBATIM | ★ |
| S13-E | 13 | `backend/designagent/tasks/manager.py:95–113` (`interface_for`) | 19 | VERBATIM | ○ |
| S14-A | 14 | `backend/designagent/runtime.py:270–310` | 20 | TRIMMED | ★★ |
| S14-B | 14 | `backend/designagent/tasks/local.py:81–86` | 6 | VERBATIM | ★ |
| S14-C | 14 | `refcodes/flowgentic/src/flowgentic/langGraph/fault_tolerance.py:73–84` | 12 | **VERBATIM — the bug** | ★★ |
| S14-D | 14 | `refcodes/flowgentic/.../fault_tolerance.py:23–37` (the defaults) | 15 | TRIMMED | ★ |
| S15-A | 15 | `backend/designagent/tasks/hpc/orbit.py:348–363` | 16 | VERBATIM | ★★ |
| S15-B | 15 | `backend/designagent/tasks/hpc/orbit.py:365–389` | 20 | TRIMMED | ★★ |
| S15-C | 15 | `backend/designagent/tasks/hpc/base.py:80–103` (`drain_logs`) | 20 | TRIMMED | ★ |
| S15-D | 15 | `backend/designagent/tasks/hpc/orbit.py:400–426` (`_finish_job`) | 20 | TRIMMED | ★ |
| S17-A | 17 | `backend/designagent/graph/nodes/analyst.py:166–195` | 20 | TRIMMED | ★★ |
| S17-B | 17 | `backend/designagent/graph/nodes/analyst.py:405–411` (`_rank_in_memory`) | 7 | VERBATIM | ★ |
| S19-A | 19 | `frontend/src/lib/api.ts:40–62` | 20 | TRIMMED | ○ |
| S20-A | 20 | `backend/designagent/graph/deps.py:49–51` | 3 | VERBATIM | ★ |
| S21-A | 21 | — (shell) | 8 | VERBATIM | ★ |
| B1-A | B1 | `backend/designagent/config.py:69–72` | 4 | **VERBATIM** | ★★ |
| B3-A | B3 | `backend/designagent/tasks/hpc/globus.py:31–36` (capabilities) | 6 | VERBATIM | ★ |
| B3-B | B3 | `backend/designagent/tasks/hpc/globus.py:90–104` | 15 | VERBATIM | ★ |
| B4-A | B4 | `backend/designagent/tools/molviz_agent.py:143–154` | 12 | TRIMMED | ○ |

**Re-deriving anchors.** Do this before presenting; drift hides here. Every anchor above is checked
by matching the snippet's first code line against the current source, which is automated:

```sh
python3 slides/check_anchors.py      # 46/46
```

It reports, for any anchor that moved, where the line actually is — so this file and `build_deck.js`
get corrected rather than presented wrong. Add an entry to `ANCHORS` whenever a snippet is added
here. Do not present a drifted anchor: a slide citing `tasks/base.py:135` while showing code from
somewhere else is worse than a slide with no citation at all.

---

## The three blocks that carry the asks

These are the ones the middleware authors will read closely. They are quoted here in full because
the slide text must not paraphrase them.

### S14-C — flowgentic hard-imports `aiohttp` · **VERBATIM**

`refcodes/flowgentic/src/flowgentic/langGraph/fault_tolerance.py:73–84`

```python
		# Try to include aiohttp timeouts if present
		try:
			import aiohttp  # type: ignore

			ex.extend(
				[
					aiohttp.ClientConnectionError,
					aiohttp.ServerTimeoutError,
				]
			)
		except Exception:
			raise
```

The comment says *if present*; the code says *or die*. The same shape appears for `httpx` at
`:60–72`. Both become hard requirements of flowgentic. `except Exception: pass` is clearly what was
meant.

Reached whenever `RetryConfig.retryable_exceptions` is left at its default — which is `()`
(`:38–40`), i.e. the common case. Passing an explicit tuple is what sidesteps it, and that is what
`runtime.py:299` does.

Observed as: `ModuleNotFoundError: No module named 'aiohttp'` raised from `fault_tolerance.py:75` on
the first wrapped task, in an env with `httpx` but not `aiohttp`.

### S14-D — the retry defaults · **TRIMMED** from `fault_tolerance.py:23–37`

```python
	max_attempts: int = Field(
		default=3, description="Total attempts including the first try"
	)
	...
	timeout_sec: Optional[float] = Field(
		default=30.0, description="Per-attempt timeout (None disables timeout)"
	)
	retryable_exceptions: Tuple[type, ...] = Field(
		default=(), description="Tuple of exception classes considered transient"
	)
```

30 s with 3 attempts is right for a service call and wrong for a protein fold. Measured against this
repo's own workload: the six round-1 folds in the reference campaign took **13.3 s to 31.7 s** each,
and round 2 ran to **42.4 s** — so the default would have cancelled and silently re-run roughly half
of them. Our wrapper passes `timeout_sec=None, max_attempts=1` (`runtime.py:295–300`).

### B1-A — the deviation, recorded in the code · **VERBATIM**

`backend/designagent/config.py:69–72`

```python
    # Route node bodies through flowgentic's EXECUTION_BLOCK as well as tasks.
    # Off by default: it runs nodes outside LangGraph's runnable context, which
    # silently disables status and token streaming. See graph/build.py.
    wrap_nodes: bool = False
```

The approved design had every node wrapped as an `EXECUTION_BLOCK`. It ships off. A node body run on
asyncflow's loop is outside LangGraph's runnable context, so `get_stream_writer()` raises
`RuntimeError: Called get_config outside of a runnable context` and every custom event is dropped
**silently** — the graph still completes, the chat just goes quiet. Isolated both ways: a plain node
emits its status lines, a wrapped one emits none.

The ask for the room: **is `EXECUTION_BLOCK` meant to preserve the caller's context?** If yes, this
is a bug worth fixing and `wrap_nodes=True` becomes the default. If no, flowgentic's node wrapping
and LangGraph's streaming are mutually exclusive, and that belongs in its README.

---

## S10-A — the protocol route, before classification · **VERBATIM**

`backend/designagent/graph/nodes/coordinator.py:269–274`

```python
        protocol = state.get("protocol") or {}
        if protocol.get("awaiting") and not _wants_out(text):
            return Command(
                goto="protocol",
                update={"intent": "protocol", "status": "Continuing the protocol…"},
            )
```

The protocol stops at three points and waits for a person, and there is no `interrupt()` in this
repo: a stage sets `protocol.awaiting` and returns, and the next message answers it. This route has
to come **before** intent classification, because "liu", "310,364" and "go" all classify as `chat`,
which answers from session state and leaves the campaign waiting forever. `ESCAPE_WORDS`
(`coordinator.py:115–121`) is the explicit way out.

## S10-B — two tables, so each entry is testable alone · **TRIMMED** from `protocol.py:865–882`

```python
STAGES: dict[str, Callable[[Ctx], Awaitable[Outcome]]] = {
    "intake": stage_intake,
    "structure": stage_structure,
    "conservation": stage_conservation,
    "mpnn": stage_mpnn,
    "score": stage_score,
    "af3_submit": stage_af3_submit,
    "af3_collect": stage_af3_collect,
    "report": stage_report,
    "done": stage_done,
}

ANSWERS: dict[str, Callable[[Ctx], Outcome]] = {
    "inputs": answer_inputs,
    "method": answer_method,
    "cat_res": answer_cat_res,
    "designs": answer_designs,
}
```

One stage per turn, and one table for each direction: what a stage runs, and how a reply to its
question is read. 203 of the 417 offline tests are this node, which is the only thing standing in
for a cluster it has not run on yet.

## S20-A — a turn's user, resolved per node · **VERBATIM**

`backend/designagent/graph/deps.py:49–51`

```python
    def settings(self) -> Settings:
        turn = current_turn.get()
        return turn.settings if turn is not None else self.base_settings
```

`context.current_turn` is set inside `/api/chat`'s `run_graph` task and asyncio copies it into every
task the turn creates, so every node gets that user's own key and endpoint without knowing users
exist. Deliberately not LangGraph's `configurable`: `get_checkpoint_metadata` copies every string in
it into checkpoint metadata, so a key put there would be written to disk every turn. Pinned by
`test_no_user_secret_is_persisted_by_a_turn`, which scans the checkpointer, the task snapshots and
the data dir for the key.

---

## Orbit findings, with the lines that work around them

| Finding | Evidence | Workaround |
|---|---|---|
| The terminal rhapsody event carries state and `exit_code` but **not `stdout`** | a completed `/bin/echo` task resolved with an empty result | `orbit.py:323–335` `_finish_task_enriched` re-fetches with `get_task` before settling the future |
| A FAILED job reports **only a non-zero exit code** — no reason reaches the client | `handle.error` was empty for a job that exited 3 | `orbit.py:448–453` synthesises one from exit code, then stderr, then the log tail |
| The broker has **no HTTP topology route** | `/topology` → 307 → 404, `Endpoint 'topology' unknown` (it reads as a plugin name) | readiness comes from the client's own `rt.topology()`, polled in `connect()`; the local stack greps the endpoint's log for `registered as '<name>'` (`local_orbit.py:201–220`) |
| `--no-auth` disables **only ingress auth** — the broker still refuses to start without cert and key | broker exited at startup with `--no-auth` alone | `local_orbit.py:60–83` generates a throwaway self-signed pair, SAN `IP:127.0.0.1,DNS:localhost`, key `0600` |
| All Orbit clients are **synchronous/blocking** | `RhapsodyClient` / `PSIJClient` methods block | every call goes through `asyncio.to_thread` (23 sites in `orbit.py`) |
| Push callbacks arrive on a **listener thread** | — | `orbit.py:348–352` `_dispatch` hops to the loop with `call_soon_threadsafe` |

Not a complaint, worth saying out loud: offset-based log tailing via `PSIJClient.get_job_status(job_id,
stdout_offset, stderr_offset)` is the **only** streaming mechanism either backend offers, and it
works. `drain_logs` (`hpc/base.py:80–101`) is built on it and `tests/test_orbit_local.py:92` proves
incremental tailing against a real local endpoint.

---

## S16 — the staging measurement · **MINED**, not a code block

Slide 16 prints a measurement rather than a snippet, so this is its reproduction. It drives the real
`wrap()` and `collect()` from `tasks/hpc/artifacts.py` through a local `bash -lc`, with no broker in
the path, which makes every number here a **floor** on what a real job pays:

```sh
python3 slides/bench_staging.py --repeats 3     # writes slides/staging.json
```

| What the slide says | Where it comes from |
|---|---|
| ×1.33 of stdout per byte, incompressible | `staging.json` row `random` at 1 MiB |
| ×0.33 for structure text | row `structure` at 1 MiB, a real 1OIL PDB |
| a 2 MiB PDB is refused as `too_large` | row `structure` at 2 MiB: `skipped: ["too_large"]`, though it compresses to 0.67 MB |
| E2BIG at ~1.5 MiB of incompressible payload | `argv_ceiling_bytes`, bisected — `getconf ARG_MAX` is 2 MiB here |
| the caps themselves | `artifacts.py:56` (`CHUNK`), `:59` (`ARTIFACT_MAX_BYTES`), `:60` (`TOTAL_MAX_BYTES`) |
| mean 2.04 tasks in flight, peak 6 | derived in `build_deck.js` from `run.json`'s task rows at build time |
| the GPU-hour checkpoint | `graph/nodes/protocol.py:616`, in `stage_score` |

The asymmetry is the finding and it is worth stating precisely: **the cap is checked on the raw file
size** (`_stage_out` runs `wc -c`), while what crosses the broker is gzip then base64. So the refusal
is entropy-blind and the cost is not.

---

## S21-A — running it · **VERBATIM** (shell)

```sh
./scripts/setup.sh                              # the only supported install path
python -m designagent --reload                  # :8000, needs the __main__ guard
python -m designagent --check-config --probe    # effective config, each credential tried
python -m designagent --add-user NAME --admin   # accounts; --gen-secrets-key for the key
cd frontend && npm install && npm run dev       # :5173, proxies /api
pytest -q                                       # 417 tests, offline
pytest -q -m live                               # 12 tests, a real localhost broker + endpoint
pytest -q -m remote                             # 8 tests, a real endpoint and a real scheduler
```

`uv sync` cannot work: the four middleware packages are local editable installs from `refcodes/`
(gitignored), and flowgentic pins `radical-asyncflow` and `academy-py` to git URLs that fight the
local checkouts, so it is installed `--no-deps`. Orbit goes in `--no-deps` too, with
`opentelemetry-sdk` added by hand: without it every rhapsody session fails to open and the whole
`live` tier *skips* itself rather than failing. Each of `live`, `remote` and `llm` is deselected
separately by `addopts`, so naming `tests/test_orbit_local.py` alone collects nothing — the marker is
what selects it.

No `ANTHROPIC_API_KEY` is required: every node has a deterministic rule-based path, and the
reference campaign in `run.json` was produced with `llm: false`.
