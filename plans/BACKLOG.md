# Backlog

Open issues, with the evidence for each. An entry earns its place by naming where the problem is and
how to see it — "needs refactoring" is not an entry. When something is fixed, delete the entry rather
than marking it done; git history is the record.

Convention: **A** = correctness or honesty of a claim · **B** = developer experience ·
**C** = upstream, in the middleware rather than here · **D** = known-unknown, needs investigation
before it can be sized.

Status as of 2026-10-21. A number is retired with its entry rather than reused, and the rest keep
theirs — the alternative renumbers every reference in the code, the tests and the plans each time
something is fixed. Refer to issues by their title in commit messages all the same; a title survives
a rewrite, and the first numbers were handed out before this rule.

---

## A — correctness and honesty

### A2 · The Globus adapter has never met a live endpoint
`tasks/hpc/globus.py` implements the ABC and is tested with an injected executor
(`tests/test_tasks.py::test_globus_interface_runs_with_injected_executor`), which proves the shape
and the argv handling, not the integration. Capability flags are set from the documented behaviour of
Globus Compute, not from observation.

**Risk if ignored:** the flags may be wrong in a direction that matters —
`supports_cancel=False` is conservative, but `supports_staging=False` may be too pessimistic.

Now reachable from configuration (`DESIGNAGENT_GLOBUS_ENABLED` + `_ENDPOINT_ID`, constructed in
`runtime._make_globus`), but still unproven, and `globus-compute-sdk` is **not installed** — so
enabling it reports why it could not start rather than connecting. Credentials come from the SDK's own
login, not from our settings; if that turns out to need plumbing, it is a new entry.

### A3 · `proteinmpnn` is a heuristic proposer unless an endpoint with ProteinMPNN is attached
With no `hpc` interface, `interface_for` falls back to the heuristic, which produces single-point
substitutions from a fixed table. The catalog entry now points at
`proteinmpnn_local_fallback` rather than `propose_variants` itself, so the result relabels at the
point of substitution — *"no HPC endpoint was attached, so the job was never submitted"* — because
the old wiring made a heuristic round indistinguishable from a real one in the output while the
ledger recorded that ProteinMPNN was asked for.

**The real path now works and has been run.** Weights `v_48_020` on CPU, through a real localhost
broker: 3 sequences of a 320-residue target in 4.6 s, and a three-round 1UBQ campaign whose designs
carry `source=proteinmpnn`, `mpnn_score`, and 30–36 substitutions from the reference. See
`scripts/setup_mpnn.sh`.

Honesty no longer rests on a hand-written note. `variants_from_mpnn_fasta` lifts `model_name` and
`git_hash` out of ProteinMPNN's own FASTA header, so provenance is *read* rather than asserted, and a
run that declares neither earns a caveat saying its samples cannot be attributed to specific weights.
The cheapest discriminator in a finished round is the mutation count: the heuristic table never emits
more than one substitution per variant.

**Still keep it honest** — the whole reference campaign in `slides/run.json` is heuristic output until
it is regenerated, and a reader who misses the label will over-read a 0.13 pLDDT difference.

### A4 · Every ProteinMPNN round redesigns the *reference* backbone
`_mpnn_job` (`graph/nodes/orchestrator.py`) reads `reference["structure_path"]`, so round 2 and round
3 re-sample the same original backbone with a different seed rather than building on the round's
lead. Meanwhile `parent_sequence` *is* the evolving lead, so `mutations` is the delta from the lead
while ProteinMPNN's own `seq_recovery` is measured against the reference sequence. Both numbers are
correct and they are not comparable, which is why a finished round can read
"5 mutations ... 51% identity to the input backbone's sequence".

Measured on a real 3-round 1UBQ run (`model_name=v_48_020`): round 1 designs sat 30-36 substitutions
from the reference; round 2 designs were 11-15 from their parent but still 33-36 from the reference.
So the rounds are near-independent samples of one fold, not refinement.

The heuristic path does not have this shape -- `propose_variants` mutates `parent_sequence`, so it
iterates. The asymmetry was invisible until ProteinMPNN actually ran.

**The choice:** feed each round the lead's own predicted structure
(`lead_design["structure_path"]`, already written as a blob by the fold step) and a campaign
iterates; keep the reference and a campaign is a wider sample of one backbone with
`rmsd_to_reference` still meaningful. Either is defensible, but it should be deliberate and the
reply should say which. Reproduce with `DESIGNAGENT_ORBIT_LOCAL=true` plus a real
`DESIGNAGENT_MPNN_COMMAND` and three rounds on one target.

### A5 · `--fixed_positions` was not a ProteinMPNN flag
`mpnn_job_spec` carried `--fixed_positions <space-separated ints>`, which ProteinMPNN does not
accept: the real flag is `--fixed_positions_jsonl` and it takes a file. No caller ever passed it, so
nothing broke, but a real run would have exited non-zero on the first use of the feature. Removed
rather than left in place, with a comment at the site.

**To bring it back correctly:** write the JSONL into the job's `inputs` (the staging mechanism added
for C6 can carry it) and pass `--fixed_positions_jsonl` pointing at that name. The caller-facing
argument can stay a list of residue positions. Worth having — it is how a campaign protects an active
site from redesign, which is precisely what the `functional_features` already in `reference` describe.

### A6 · An inlined structure has a hard size ceiling
Stage-in rides in argv (C6), so `mpnn_job_spec` refuses a structure whose gzipped, base64'd backbone
exceeds `MAX_INLINE_B64` = 1 MiB, half of this host's `ARG_MAX` of 2 MiB. The round then falls back to
the heuristic with a caveat naming the measured size.

Backbone-only stripping buys most of the headroom: 1UBQ's 76 residues go from ~80 KB to ~25 KB before
gzip, and a real 320-residue chain inlined at ~120 KB of backbone without trouble. The ceiling bites
on a very large single chain or a multi-chain design, and `ARG_MAX` is not guaranteed to be 2 MiB
everywhere — a site with a smaller limit will fail at submission rather than at the guard.

**The real fix is upstream staging (C6)**, or a site-side input cache the job reads by path. Until
then the guard is the honest behaviour: a named refusal beats a scheduler error.

### A7 · A design's provenance is not visible in the UI
`provenance.source` is set by the generator (`"proteinmpnn"` / `"heuristic"` / `"literature"` /
`"user"`), carried into each design by `nodes/analyst.py`, and typed in the frontend
(`lib/types.ts`) — and rendered by nothing. The Ensemble table rows (`nodes/interpreter.py`) and the
Markdown/.docx summary (`artifacts/render.py`) both omit it, so the honest discriminator a reader
needs is only inferable: a `mpnn score` column, or counting substitutions in `mutations`.

Adding a `source` column to the Ensemble rows is a two-line change at `interpreter.py` and a
`TableView` pin; the reason to pause is that a mixed round (ProteinMPNN plus a heuristic fallback)
should probably say so in the reply too, not just in a table a user has to open. Related: the task
chip now names a non-local `interface`, and the summary's `## Tasks run` prints it, so *where* a
round ran is visible while *what proposed each design* is not.

### A8 · `state.warnings` has no UI element of its own
The channel is streamed (`"warnings"` is in `UI_STATE_KEYS`) and merged into the frontend's state,
and then nothing renders it. Caveats reach the user only because `nodes/interpreter.py` appends a
`**Caveats from this run:**` block to the reply text, capped at the first 5, plus a count in the
`TurnTrace` disclosure. So a degraded turn whose reply the user skims looks normal, and caveats 6+
are dropped silently.

### A9 · The settings write surface is a security boundary
`PUT /api/settings` is open on loopback with no `DESIGNAGENT_ADMIN_TOKEN`, which is the development
default, so every field added to `SettingsUpdate` is reachable by any local process. That was fine
while the surface held credentials and config values. It stopped being fine the moment
`mpnn_command` and `mpnn_prologue` were added: the prologue is appended to the generated job script
verbatim (`tasks/hpc/artifacts.py`), so a `PUT` was arbitrary shell, run as the server user locally
and on the HPC endpoint under the site's allocation remotely. Caught in review before it shipped
anywhere; demonstrated against the pre-fix process, which accepted
`{"mpnn_prologue": "touch /tmp/pwned"}` with HTTP 200 and held it as an active override awaiting the
next design round.

Both are now refused, listed in `NOT_REMOTELY_WRITABLE` with the reason, and shown `readonly` in the
panel. The general lesson is recorded because the next field to name a program will look just as
harmless: **adding a field to `SettingsUpdate` widens an unauthenticated surface**, and a value that
is interpreted rather than stored needs a reason before it goes there.

**Worth considering separately:** whether loopback-without-token should stay open at all now that the
surface is wider, or whether `orbit_psij_executor` and `orbit_broker_url` — a scheduler name and a
URL the client will trust — deserve the same scrutiny.

**On a multi-user host, loopback is not "only local processes".** On the `amarel3` login node
(2026-10-07) about 86 users share `127.0.0.1`, and one of them already held `:8000`. There, the open
default admits every user on the node to the write routes. Repro: start the backend there with no
`DESIGNAGENT_ADMIN_TOKEN`; any other account on the node can `PUT /api/settings`. The deployment in
`plans/AMAREL_ENDPOINT.md` sidesteps it by running the agent on a single-user Linode VM and
setting the token anyway (§2.1); the question remains for anyone who runs the backend on a shared host. A cheap
middle ground: have `--check-config` warn when bound to loopback with no token and other users are
logged in.

### A10 · A heuristic round never says so in its reply
Measured in a browser with the endpoint turned off: the round completes, produces ranked designs, and
the reply reads exactly like a real one — *"It was proposed because glutamine to glutamate avoids
deamidation"*, no caveats block, no mention that these are rule-based proposals. The generator does
label its own output (`"note": "heuristic proposals, not ProteinMPNN samples"`), but the note stays in
the task record and `nodes/interpreter.py` never surfaces it, because nothing put it on the
`warnings` channel: from the orchestrator's point of view nothing failed, so there was nothing to
caveat.

What a user is left with, all requiring prior knowledge: the chip is named `propose variants`
rather than `proteinmpnn`, each design carries exactly one substitution, the rationale is a chemistry
phrase instead of a sample and score, and there is no `mpnn score` column.

This is the other half of **A3**. The label is honest where it is written and absent where it is
read. The cheap fix is for the orchestrator to push a line onto `warnings` when it takes the
heuristic branch *by configuration* rather than by failure — the fallback-after-failure path already
does exactly that. The reason to think first: a user with no endpoint would then see the same caveat
on every single round, which is noise that trains people to ignore the block. Possibly it belongs on
the first round of a session only, or in the ensemble table as a provenance column (**A7**) instead
of in prose.

Reproduce from the browser: turn off the development-broker toggle in the credentials panel, start a
new session, run a redesign. `frontend/e2e/BROWSER_TESTS.md` T10 is this, written up as a test with
the gap stated.

### A11 · The relabelling fallback body is unreachable in practice
`CATALOG["proteinmpnn"]` points at `proteinmpnn_local_fallback`, which exists to relabel a heuristic
round as *"no HPC endpoint was attached, so the job was never submitted"*. The orchestrator never
reaches it: it guards on `hpc_available` **before** enqueuing, so it names `propose_variants` on the
local interface rather than letting `interface_for` downgrade a `proteinmpnn` spec. The body therefore
runs only if an endpoint detaches between planning and submission, or if something calls the catalog
entry directly.

Not harmful — the label is correct whenever it does appear, and the guard it duplicates is the right
one — but it means the clearest wording of the heuristic-substitution message is the one users almost
never see, which is worth knowing before anyone counts it as covering **A10**.

### A12 · The skill's `add_fixed_positions.py` crashes on its own upstream's sentinel
`backend/designagent/protocol/fixed_positions.py` is a port of
`enzyme-redesign-protocol/scripts/amarel/conservation/add_fixed_positions.py`, and the port
deliberately diverges in one place. When a conservation level finds nothing conserved,
`hhblits_search.py` writes `{"<model>": {"A": "-"}}` rather than an empty list. The script then does
`sorted(set(positions) | add_residues)` with `positions == "-"`, so `set("-")` is `{"-"}` and the
sort raises `TypeError: '<' not supported between instances of 'str' and 'int'`.

Reproduce:

```bash
printf '%s\n' '{"m": {"A": "-"}}' > in.jsonl
python3 add_fixed_positions.py in.jsonl "1-L-5-6-CD-10-11-IDR-12" out.jsonl
```

The port reads the sentinel as "nothing conserved" and emits the domain-and-termini set, which is
what the pipeline means by it. Pinned by
`tests/test_protocol_fixed_positions.py::test_the_nothing_conserved_sentinel_is_read_as_an_empty_set`.

**Why it matters anyway:** the two implementations now disagree, so a by-hand run of the skill and a
run through the agent can produce different fixed sets for the same conservation output. The fix
belongs upstream in the skill repo; until it lands, a by-hand run that hits an empty level fails
loudly rather than silently, which is the better of the two failure modes but is not the same answer.

### A13 · The protocol's deliverables that are not produced, and what cannot be brought back
`graph/nodes/protocol.py` runs the enzyme-redesign protocol, and four of the skill's outputs are
missing. Each is listed so nobody counts the node as covering the whole skill.

**The three PyMOL `.pse` sessions** (Steps 4, 6 and 11) need PyMOL's own python, which this backend
does not have. The skill calls them a "standard deliverable" and says "always build" one. A Mol* view
spec through `tools/molviz_agent.py::sanitize_spec` is the intended substitute for the on-screen
check, and the reply has to **say** the `.pse` files were not made — a silent substitution is exactly
what the "keep that label honest" rule exists to prevent.

**The FoldSeek upload (Step 3) stays manual**, by the skill's own instruction ("try nothing
automated"), so a campaign with no `PAPERS` cannot complete unattended: `stage_structure` ends by
giving the user the portal URL and waiting.

**Step 10b's alignment page and Step 12's comparison are unimplemented.** 10b would also exceed the
staging ceilings even if run: the HTML plus a vendored 3Dmol is megabytes.

**The ceilings bite on the deliverables, not the data.** `orbit_artifact_max_bytes` is 1 MiB per file
and `orbit_job_output_max_bytes` 4 MiB per job, so the `.a3m`, the notebook's plot PNGs, the AF3
`.cif` models and the vendored Open Sans faces all stay on the cluster and the user gets a path.
`protocol/notebook.py::analysis_outputs` deliberately fetches no PNG for this reason. The skill's
"pull summary plots locally" is the one instruction the node cannot honour.

### A14 · A restart orphans the protocol's in-flight jobs
`af3_collect` is filesystem-shaped precisely because re-attaching cannot be built from this repo
(three blockers, all in `refcodes/`: `connect()` calls `register_session()` with no sid so every
start is a new session; `close()` ends in `shutil.rmtree` of the stdout directory; and the endpoint
exposes no attach route for its `psij.Job` objects). So the *results* survive a restart — the output
tree is still there and re-reading it is the normal path.

**What does not survive is control.** A backend restart during Step 11 leaves N GPU jobs running with
nothing able to cancel them, and they keep burning the allocation to completion. `protocol.native_ids`
records the scheduler ids, so `scancel` by hand is possible; nothing automates it.

**No notification channel either.** HHblits is 15-20 minutes and AlphaFold3 is an hour per design,
both of which end the turn by design, and there is no way for the agent to tell the user a stage
finished — they have to come back and ask. The most-wanted follow-on.

### A15 · `jupyter nbconvert --inplace` mutates the notebook it ran
Step 10 runs the scoring notebook with `--execute --inplace`, so the copy in `$PROJ/analysis` carries
the executed outputs afterwards. A rerun is therefore not reproducible from that copy; the install
stage pushes a fresh one patched by `protocol/notebook.py` each time, which is why it works, but
anyone reading `$PROJ/analysis/analyze_stabilization.ipynb` is reading a used notebook rather than
the one that would run next.

### A16 · The development broker runs `--no-auth`, which is unsafe on a shared host
`LocalOrbitStack.start` (`tasks/hpc/local_orbit.py`) launches the broker with
`--no-auth --host 127.0.0.1`, with a `psij`-enabled endpoint behind it. That stack is what
`pytest -m live`, `DESIGNAGENT_ORBIT_LOCAL=true` (`runtime.py`) and `./scripts/dev.sh up` with an
endpoint all start. On a workstation loopback is one user's; on an HPC login node such as `amarel3`
it is everyone's, so for the stack's lifetime any user who finds the random port can submit jobs
through it as the account that started it — and with `DESIGNAGENT_ORBIT_PSIJ_EXECUTOR=slurm`, on its
allocation. Repro: on a login node, `DESIGNAGENT_ORBIT_LOCAL=true .venv/bin/python -m designagent`,
then from another account `ss -ltn` shows the port. Not yet demonstrated end to end; the reasoning is
from the flags.

Upstream agrees on the stakes: `plans/security_token_mitigation.md` in the Orbit checkout describes
psij submit on an unauthenticated ingress as arbitrary command execution on every connected endpoint,
which is why its broker made auth the default. `--no-auth` is that escape hatch.

Fix idea: generate a throwaway token per stack, write it `0600` into the work dir, and pass it to
broker, endpoint and client instead of `--no-auth`. The broker already reads `--token`, so this is
local to `local_orbit.py` and the client settings it hands back.

**Not on the production path.** The deployed broker runs on a Linode VM with auth on, and
`plans/AMAREL_ENDPOINT.md` runs ladder rungs 1–2 on that VM, where loopback is private. The entry
stands for anyone who runs the dev stack on a login node.

### A17 · Behind a same-host proxy, `bound_to_loopback` is true for every caller
**Closed on the Linode 2026-10-09 by logins** (`DESIGNAGENT_AUTH_ENABLED=true`). With logins on,
`_authorize_write` needs a signed-in admin and a same-origin request, and never consults the bind
address. The trap below still applies to any deployment behind a proxy with logins **off**.

`Settings.bound_to_loopback` (`config.py`) reads the address uvicorn *binds*, not the address a
request comes from. On the Linode the backend binds `127.0.0.1` and Caddy proxies the internet to it
(`plans/LINODE_DEPLOY.md` Phase 1), so every request is local by that test. With no
`DESIGNAGENT_ADMIN_TOKEN`, `_authorize_write` then opens `PUT`/`DELETE /api/settings` and
`POST /api/settings/test` to anyone who gets past the proxy.

**Repro:** with the token unset, start the backend on loopback behind any reverse proxy. A `PUT
/api/settings` through the proxy is accepted.

**Fixed on the Linode by Phase 2, which has shipped:** `_authorize_write` is a role check there, and
Caddy's `basic_auth` was removed once logins worked. The loopback-open default itself is still in the
code, for the development server, so **any deployment behind a proxy with logins off must set the
token.**

### A18 · Two clients with the same Orbit name silently steal each other's replies
`orbit_client_name` defaults to `designagent`, and the broker evidently routes replies by that
name (inferred from behaviour, not yet read in its source). Found 2026-10-09 on the Linode:
- The backend service was started while `pytest -m remote` was mid-run, with the same environment.
- The backend logged `[Runtime] name 'designagent' in use; retrying with backoff`, then registered a
  few seconds later.
- From then on the test process's calls got no answers. Its job-status polls blocked forever in
  `orbit/runtime.py:876` (`call` → `future.result()`, no timeout), the last three tests failed, and
  pytest hung in teardown, shutting down an executor whose threads would never return.

Nothing errors: the second process looks healthy and the first just stops hearing back.

**Repro:** run the backend and `pytest -m remote` against the same broker with the same
`DESIGNAGENT_ORBIT_CLIENT_NAME`.

**Workaround:** give every extra client its own name. The rung 4 re-run used
`DESIGNAGENT_ORBIT_CLIENT_NAME=designagent-rung4`.

**Fix:**
- A per-process suffix on the default name (hostname plus pid, or a random tag).
- Phase 2's per-user `OrbitRegistry` **must** mint a distinct client name per interface, or two
  users' interfaces will collide in exactly this way. **Done** (`runtime.OrbitRegistry`,
  `test_each_users_orbit_interface_is_their_own`). The process-wide interface still uses the bare
  default, so a backend restart collides with *its own* previous connection. The new process
  retries until the broker drops the old one, about 10 s on 2026-10-09, during which `hpc` is not
  yet available.
- The unbounded `future.result()` is upstream's (backlog C), and is worth reporting.

### A19 · A `custom_attributes` flag that takes no value cannot be expressed
PSI/J's Slurm template renders every custom attribute as `--<name>=<value>`. `{"slurm.requeue": ""}`
therefore became `--requeue=`, and the endpoint answered **HTTP 500**: `sbatch: option '--requeue'
doesn't allow an argument`. Found by rung 4 on 2026-10-09, in
`test_custom_attributes_are_accepted_by_the_endpoint`. That was a bug in the *test*, which now sends
`slurm.comment` and passes on Amarel and against the development stack.

**Why it matters anyway:** `--requeue` is exactly the flag CLAUDE.md lists as lost in the move off
the skill's `#SBATCH` headers, so restoring it is not a one-line `custom_attributes` change.

The protocol's own use, `slurm.constraint` with a value (`protocol/specs.py`), renders correctly.

### A20 · A missing `--chdir` runs the job in `/tmp` and reports success
On Amarel, a job whose `directory` does not exist is **not** rejected: Slurm falls back to `/tmp`,
and the job ends DONE with exit 0. Probe of 2026-10-09:
- `directory=/scratch/<netid>/no-such-dir-rung4` printed `/tmp`, job `62380977`, DONE, exit 0;
- the same body with `directory=/scratch/<netid>` printed that directory.

`AMAREL_ENDPOINT.md` §6 had assumed the opposite.

**Exposure in the protocol:** small but real.
- The push stage `mkdir -p`s the project subdirectories before any compute stage, and compute bodies
  read relative inputs under `set -euo pipefail`. So a missing directory *usually* fails loudly on
  the first missing input.
- A stage whose first act is to create its own output (`mkdir -p "$out"` in the MPNN and AF3 specs)
  could instead do its work in the compute node's `/tmp` and report success, and those files would
  be lost.

**Fix:** a guard line at the top of every compute body in `protocol/specs.py`:
`[ "$PWD" = <directory> ] || { echo "not in <directory>: Slurm fell back to $PWD" >&2; exit 97; }`.
This turns the silent fallback into a named failure.

**And two remote tests assume a shared filesystem.** `test_a_jobs_working_directory_is_honoured`
and `test_a_file_comes_back_out_of_a_project_directory` build their directories under the test's
`tmp_path`, on the machine running pytest. That holds for the development stack and never for a real
endpoint, so both fail on Amarel for that reason alone. They need a remote base directory, for
example `DESIGNAGENT_REMOTE_TEST_DIR`, defaulting to `tmp_path` when `ORBIT_LOCAL`, with the files
created and checked by jobs rather than by the test process.

**Both tests passed on 2026-10-21, and the entry still stands.** Run from `amarel3` — the endpoint
host — with `--basetemp=/scratch/$USER/...`, the jobs could see the paths and both passed
(`AMAREL_ENDPOINT.md` rung 4b). That is a run recipe for one host, not the fix: `/scratch` is shared
with the compute nodes while `/tmp` is node-local, and from the VM both tests still fail. The
remote-base-directory change above is what makes the tier runnable from anywhere, so keep it.

The **primary defect is untouched**: a missing `--chdir` still falls back to `/tmp` and reports
success, and the guard line is still unwritten. Nothing in the 2026-10-21 run exercised a
*nonexistent* directory — both tests `mkdir -p` their own.

---

### A21 · What Phase 2's logins leave open
Logins and per-user credentials landed 2026-10-09 (`plans/LINODE_DEPLOY.md` Phase 2,
`tests/test_auth.py`). Known gaps, none of them a hole in what was built:

- **The broker is still shared, and it is not a tenant boundary** (the plan's Phase 3). A user who
  brings their own endpoint must point at the allow-listed broker on this VM, with its one ingress
  token. Every endpoint holds that token, and anyone with it can submit to every endpoint on the
  broker. Per-user isolation of *HPC* needs one broker per user. Until then, give only trusted users
  the token.
- **`OrbitRegistry` never closes an idle interface.** One connection stays open per user who has
  used `hpc` since the last restart, closed only on sign-out or a credential change. That is fine at
  lab scale and a slow leak beyond it.
- **Sessions from before logins have no owner** and are unreachable under logins (404 for everyone).
  `AuthStore.assign_session` exists for adoption, but no CLI exposes it yet.
- **The login lockout is in memory**: a restart forgets the counts. It also keys on
  `X-Forwarded-For` only from a loopback peer, which is correct behind Caddy and wrong behind any
  other proxy that is not on this host.
- **The Playwright tier was not run on this change.** The VM has no Chromium. Both e2e tests stub
  `/api/**` without `auth: true`, which reads as logins off, so they should be unaffected. Run
  them on a workstation to confirm.
- **`/api/health` under logins** tells a non-admin only `{ok, auth}`. `scripts/dev.sh` waits on
  `hpc: true` there, which a deployment with logins on never returns to an anonymous caller.
  `dev.sh` is for the development stack, where logins are off, so this has not bitten. A health
  check for the Linode should sign in or read `systemctl`.

### A22 · The staging channel's two ceilings are in the wrong units
Measured by `slides/bench_staging.py`, which drives the real `wrap()` and `collect()`
(`tasks/hpc/artifacts.py`) through a local `bash -lc` and writes `slides/staging.json`. Reproduce
with `python3 slides/bench_staging.py`; the slide that quotes it is deck slide 16.

**Outbound**, `_stage_out` compares the **raw** file size against `ARTIFACT_MAX_BYTES` (`wc -c`,
`artifacts.py:146`), while what crosses the broker is gzip then base64. So the refusal is
entropy-blind and the cost is not: a 2 MiB PDB is skipped as `too_large` although it compresses to
0.67 MB, and a 1 MiB incompressible file is accepted and costs 1.40 MB of stdout. Measured ratios at
1 MiB: **×1.33** for incompressible bytes, **×0.33** for structure text.

**Inbound** has no declared cap at all. Chunking defeats `MAX_ARG_STRLEN`, the per-element limit, but
all the chunks together still have to fit `ARG_MAX`, so `wrap()` builds a spec that cannot be
executed and the failure is an `OSError: Argument list too long` at exec, not a named refusal.
Bisected on this host: **~1.5 MiB** of incompressible payload, against a `getconf ARG_MAX` of 2 MiB.
`mpnn_job_spec` has its own guard for exactly this (**A6**, `MAX_INLINE_B64` = 1 MiB) — `wrap()`,
which every other caller goes through, does not.

**Fix:** outbound, gzip to a file in `$work` first and compare *that* size against the cap, then
`cat` it — one extra pass over a file that is about to be read anyway, in exchange for a cap that
means what the name says. Inbound, give `wrap()` the A6 guard: measure the assembled argv against a
configured ceiling and refuse by name, rather than handing `exec` a spec it cannot run. Both are
local; neither waits on **C6**.

### A23 · The displayed name was "Protein Design Agent", and the package still is
The UI, the README H1 and the FastAPI title said *Protein Design Agent* while the repo, the lab and
every document called the project PEAT. Fixed on the surface the user reads — `frontend/index.html`'s
`<title>`, `App.tsx`'s `.brand`, `ChatPane.tsx`'s and `LoginPage.tsx`'s `<h1>`, `app.py`'s
`FastAPI(title=...)`, `README.md`'s H1, and the deck's title-slide kicker — all now say `PEAT`.

**What is left is the discoverability problem that caused it.** There was no shared title constant:
four independent frontend literals plus the server's, so nothing made them move together, and
`frontend/e2e/smoke.spec.ts` asserting the heading text was the only thing that would have caught a
partial rename. A fifth place that displays the name will drift again.

**Risk if ignored:** low severity, but it recurs. The name is now correct in five files that have no
mechanical relationship to each other.

**Fix:** export one `APP_NAME` from a shared frontend module and read the server's from
`config.Settings`, so the string has a single definition and the e2e assertion covers all of it.

### A24 · Account and host identifiers were committed, and are already public
This repo accumulated six classes of identifier in tracked files, and `drawadiagram/rePEAT` was
public the whole time, so all of them were world-readable before anyone noticed. Redacted on
2026-10-09 — counts, placeholders and the reasoning are in
[PEAT_MIGRATION.md](PEAT_MIGRATION.md) §3: the Linode address in both dotted (8) and reverse-DNS (5)
spellings, a collaborator's netid (30, including the example value *printed on screen* by
`graph/nodes/protocol.py`'s intake form), the operator's netid (9), allocation paths (18), and the
campus NAT address and firewall block (9).

Two of those classes were found only by accident. A dotted-quad search does not match
`<linode-rdns>`, which was the live UI URL; and an identity scan keyed on
`git config user.email` and `$USER` cannot see a *colleague's* netid, which is why it survived in 30
places. Both lessons went into the `repo-sensitive-scan` skill.

**The three commits carrying the Linode address (`93067d7`, `5e37a8e`, `f9c5825`) are deliberately
not rewritten.** The values were already public, so a rewrite buys no secrecy while churning every
SHA after `93067d7`; the honest remediation for an exposed host address is a firewall rule or a new
address. Revisit only if the address is rotated, and note that the cheapest moment was before the
first push to `KhareLab/PEAT`.

**Risk if ignored:** the redaction is a point-in-time pass. `plans/` is written from live job output
and quotes real paths, so the next deployment record re-introduces the same classes unless the
scanner is run before each push.

### A25 · Every job asks for a GPU by default, and a CPU partition rejects it
`job_params` fills a GPU count into any spec that does not set one
(`tasks/jobspec.py:54-60`), from `orbit_job_gpus` — which **defaults to 1**. On a CPU-only partition
Slurm refuses the submission outright:

```
HTTP 500 — [amarel3/psij] psij submit '/bin/echo' on 'slurm'
  — sbatch: error: Requested GRES option unsupported by configured SelectType plugin
```

Repro, 2026-10-21: `-m remote` against `amarel3` with `DESIGNAGENT_ORBIT_QUEUE=main` and the default
`orbit_job_gpus`. Three of the eight tests failed this way — every one that builds its spec through
`job_params`, which is every one except the two that hand-write a `resources` dict and the fetch test
that runs on the `local` executor. `DESIGNAGENT_ORBIT_JOB_GPUS=0` makes all three pass
(`AMAREL_ENDPOINT.md` rung 4b).

**Why it matters beyond the tests:** every protocol compute spec goes through `job_params`
(`tasks/jobspec.py:36-63`, reached from `protocol/specs.py`), so on a CPU queue each would be
rejected the same way, at
submission, before any walltime is spent. The conservation and MPNN stages are CPU work; only AF3
wants a GPU, which is why `protocol_gpu_queue` exists as a separate setting. A whole campaign would
fail at stage one against `queue=main`.

**What makes it expensive:** the message names GRES and the SelectType plugin, never
`orbit_job_gpus`, and it arrives as an HTTP 500 from the endpoint — so it reads as a broker or
endpoint fault. The comment at `jobspec.py:50-53` already reasons carefully about *zero versus
absent* (a site handed `gpu_cores_per_process: 0` may render `--gres=gpu:0`), so the care went into
the wrong half: the problem is the non-zero default, not the encoding of zero.

**Options, undecided.** Default `orbit_job_gpus` to 0, so a GPU is opt-in the way the account and
queue are — cheapest, and matches "only a positive number is sent" in spirit. Or derive it from the
queue the spec is headed for, which is the real coupling but needs a site table this repo does not
have. Or leave the default and make the rejection legible, by having `_submit_job` recognise a GRES
rejection and name the setting. Measure first: nothing yet shows whether a GPU partition would
*reject* a job that asks for none, which would argue for keeping a non-zero default somewhere.

---

## B — developer experience

### B1 · A fresh clone cannot be built
`refcodes/` is gitignored and is where `radical.asyncflow`, `rhapsody` and `flowgentic` come from, so
`scripts/setup.sh` cannot run against a clone — it detects this and says so, which is the most it can
do. There is no vendoring, submodule or pinned-revision strategy, and the working tree is the only
record of which revision of each was used.

**Why it matters:** three of this project's findings (C1, C2, C3) are claims about specific revisions
of those packages, and right now nothing records which.

**Options:** git submodules pinned to a SHA; or a `refcodes/VERSIONS.md` recording each package's
commit; or vendoring the three into the repo. The middle one is cheap and would do.

**Partly closed, 2026-10-09: the revisions are now recorded.** The user's working `refcodes/`
was copied to the Linode as clean git trees. `scripts/setup.sh` carries their commits in
`REFCODES_PINS`, and `--check` warns when a checkout differs. The list is in the repo, not in
`refcodes/` itself, because that directory is gitignored. Still open: a fresh clone has no way to
*get* the checkouts, only to verify them.

| Checkout | Commit | Date | Branch | Used by |
| --- | --- | --- | --- | --- |
| `radical.asyncflow` | `038d52a` | 2026-08-20 | main (v0.5.1) | `setup.sh` |
| `rhapsody` | `71536ac` | 2026-09-08 | main | `setup.sh` |
| `flowgentic` | `dd27bd8` | 2026-08-19 | **`demo/radical`**, not main | `setup.sh`, `--no-deps` |
| `radical.orbit` | `c7ede0c` | 2026-09-29 | devel (0.8.0) | `setup.sh`; the broker; the Amarel endpoint |
| `ProteinMPNN` | `8907e66` | 2023-06-27 | detached | `setup_mpnn.sh` (its own `MPNN_REV` pin) |
| `ChemGraph` | `d7a34ca` | 2026-10-01 | main | `.[chem]` only |
| `langgraph` | `b36b1d5` | 2026-10-01 | main (1.2.12) | reference reading |
| `hpc-bridge` | `46f63bf` | 2026-09-22 | main | reference reading |
| `rcsb-molstar` | `7153df7` | 2026-09-22 | master | reference reading |
| `enzyme-redesign-protocol` | `20a3600` | 2026-09-30 | main | read at runtime: `DESIGNAGENT_PROTOCOL_SCRIPTS_DIR` points at its `scripts/` |

**`radical.orbit`, the fourth package, is now installed by `setup.sh`** when its checkout is present.
`tasks/hpc/orbit.py` imports it lazily and `local_orbit._script` looks for its CLI scripts. Before
this, a venv that passed `--check` could not use `hpc`, and `-m live` errored on "Orbit CLI scripts
not found".

It is not a plain `pip install -e`: its requirements pull `rhapsody-py` from PyPI, which would
displace the editable `refcodes/rhapsody`. So it goes in `--no-deps` with the rest named, and
`--check` asserts that `rhapsody` still resolves to `refcodes/`.

### B2 · Is `E,F,I` the right lint baseline?
`ruff check backend tests` is clean at `E,F,I` and that is what `pyproject.toml` pins. The open
question is whether to widen it. Measured now that the baseline is clean, `E,F,I,B,UP` reports **29**:

| Rule | n | What it is |
|---|---|---|
| UP035 | 11 | `typing.Iterable`/`Dict` etc. — deprecated, belongs in `collections.abc` |
| B905 | 8 | `zip()` without `strict=` |
| UP017 | 5 | `datetime.timezone.utc` → `datetime.UTC` |
| UP034/37/41/42 | 4 | one each: extra parens, quoted annotation, `TimeoutError` alias, `NamedTuple` → class |
| B017 | 1 | a test asserting on bare `Exception` |

All 29 are mechanical and most are auto-fixable. **Correcting an earlier claim in this file:** the
old version of this entry said the extra findings were "mostly `B` (blind `except Exception:`)" and
would need per-site `noqa`. That was wrong — blind `except` is `BLE001`, in the `BLE` set, which is
not enabled by `B`. There is no conflict with the deliberate `except Exception:` in the degradation
paths, so the argument against widening was based on a misreading.

**What it costs:** `UP035` and `UP017` touch imports and `datetime` calls across many files, which
moves line numbers the deck's 46 anchors cite — so, as with the cleanup itself, the widening and an
anchor pass have to land together (see **M1**).

### B3 · The classifier's keyword lists miss `-ing` forms
`_has_word` (`coordinator.py`) matches a leading word boundary only, so each entry acts as a prefix.
That works for `fold` → "folding" but not for any verb whose gerund drops the final `e`:
`optimize` misses "optimizing", `generate` misses "generating", `iterate` misses "iterating". So
"keep iterating" and "optimizing solubility" classify as **chat** and silently do nothing.

Affects the no-LLM path only. Noted in a comment at the list, which is the honest minimum, but not
fixed: stripping a trailing `e` before matching would fix the class at the cost of real false
positives (`create` → `creat` fires on "creature"). A stem list, or two entries for the verbs that
matter, is the safe version.

### B4 · A question containing a past participle runs a redesign round
Same root cause as B3, opposite direction: `_has_word` is a prefix match, so **"what is loaded?"**
matches `load` in `ACTION_WORDS`, which sets `wants_action` and disables the
`is_question → chat` branch (`coordinator.py`). With a reference in state the turn classifies as
**design** and runs a full round — folds, scoring, a summary — in answer to a question.

```python
>>> classify_rules("what is loaded?", state_with_reference)["intent"]
'design'          # also "which PDB is loaded?", "what did you load?"
```

Found by a trace test that used that phrasing as a throwaway question
(`tests/test_graph.py::test_the_trace_is_turn_scoped` now avoids it and says why).

**Why it is not a one-liner.** `load`/`fetch` cannot simply leave `ACTION_WORDS`: they are what makes
"load a thermostable lipase" (no identifier) bootstrap, and moving the identifier branch above the
question branch would then swallow "can you load 1OIL?". The shape that works is probably a separate
`LOAD_WORDS` list consulted by the identifier branch, plus a participle guard on `wants_action`, and
it needs the classifier table in `tests/test_graph.py` extended with the interrogative forms first.

### B5 · A branch switch under the running dev server kills the browser tab
Cost a session. Vite was serving `wire-user-config-and-secrets`, a `git checkout main` deleted
`src/settings/SettingsPanel.tsx` and `src/chat/TurnTrace.tsx` underneath it, and the browser applied
an HMR update for modules whose dependencies had vanished — a dead tab with no error in the server
log. The vite log is the tell: HMR updates naming files that no longer exist on disk.

**Recovery:** check out the branch the tab was loaded from (or restart the server on the branch you
want), then **hard-reload**; a soft reload can keep the broken module graph.

**Possible guard:** a `postcheckout` git hook that touches a sentinel, or just a line in the README.
Not obviously worth automating, but worth knowing, because the symptom points at the browser and the
cause is in git.

### B6 · The browser tests stop at the Mol* canvas
`frontend/e2e` asserts the artifact pane opens and the viewer mounts; nothing checks that a structure
actually renders, that highlights land on the right residues, or that a focus call moves the camera.
Those are the parts of the visualization path a user would notice first and the tests would not.

**How it could be done:** a Playwright screenshot comparison on a fixed PDB with a fixed view spec,
or reading back Mol*'s own state through `page.evaluate`. Both are more machinery than the rest of
this tier, which is why neither is here yet.

### B7 · `asyncflow.session.*` directories accumulate in the repo root
Eight of them at the time of writing. Gitignored, so harmless to the repo, but they make `ls` useless
and they are never cleaned up. They come from `WorkflowEngine` and are created per run.

**Fix:** point asyncflow at `data/flow/` the way `config.yml` already points its other outputs, if
the engine supports it; otherwise clean them in `Runtime.aclose()`.

### B8 · Nothing prunes `data/`
Blobs are content-addressed, so duplicates are free, but nothing ever removes them — the reference
campaign alone is 5.1 MB across 24 files. Checkpoints, artifacts and the Kuzu WAL grow the same way.
Fine for development, wrong for anything long-lived.

### B9 · Runtime settings are not persisted
`PUT /api/settings` and the Settings panel hold values in memory only: a restart returns to `.env`,
and `GET /api/settings` reports the source so nothing is hidden. That was deliberate — a secrets file
is a second source of truth and a new thing to leak — but it means a credential typed into the UI has
to be typed again after a restart, or copied into `.env` by hand.

**If it becomes annoying:** write the override layer to `data/overrides.json` at 0600 and load it
below the environment, or offer an "append to .env" action. Not until someone actually wants it.

### B10 · Tier 1 cannot be read while the server is running
Kuzu takes an exclusive file lock. Any out-of-process reader has to copy the database aside first
(`slides/run_model.py` does). Tiers 2 and 3 are a plain SQLite file and Parquet and read fine in
place.

**Not necessarily a bug** — it is how embedded Kuzu works — but it means no read-only analytics, no
dashboard and no second process can touch provenance while the app is up. If that becomes a
requirement, tier 1 needs either a read replica or a different store.

### B11 · The PEAT migration cannot be finished without an org owner
Our token has `WRITE` on `KhareLab/PEAT` — `admin: false`, `maintain: false`, only push/pull/triage
(`gh api repos/KhareLab/PEAT -q .permissions`, 2026-10-09). Three things therefore need an org owner:

- **branch protection** on the new `main`, which is currently unprotected on a public repo;
- **deleting the five original branches** once the hand review of the archived work is done — `main`
  cannot be deleted at all while it is the default;
- **changing the default branch**, if the layout is ever revisited.

The migration itself is designed around this and needs none of them: `main` is already the default,
so archiving the old refs and force-pushing onto `main` works with push access alone. See
[PEAT_MIGRATION.md](PEAT_MIGRATION.md) §1.

**Risk if ignored:** a public repo whose default branch anyone with write access can force-push,
and ten branches where five are archives that look like live work.

### B12 · The package and env prefix still carry the old name
The user-visible name is now PEAT (**A23**), but `designagent` and `DESIGNAGENT_` remain: ~480
occurrences across 58 files — the Python package directory, `pyproject.toml`'s `name` and console
script, `frontend/package.json`, `COOKIE = "designagent_session"`, the `X-Designagent-Admin` header
on both sides (`app.py` and `frontend/src/lib/api.ts`), `dev.sh`'s `/proc` cmdline match, and the 41
`backend/designagent/...` paths in `slides/check_anchors.py`.

Deferred deliberately, because two of those are not internal:

- `config.py`'s `orbit_client_name` default becomes the broker client name and is expanded per user
  by `runtime.py`, so it shows up in broker logs and Slurm job metadata — and
  `tests/test_auth.py` asserts the `designagent-` prefix. Changing it has to land together with the
  Linode's `backend.env`.
- `config.py`'s `user_agent` is sent to RCSB, UniProt, Europe PMC and ESM Atlas, so it is how this
  project identifies itself to third parties.

Every deployed `backend.env` is written against the `DESIGNAGENT_` prefix, and `config.py`'s
`env_prefix` is the single line that defines it — so the rename is mechanical but the rollout is not.

**Risk if ignored:** mostly confusion; a reader meets three names for one project. The real cost is
that it gets harder as more deployments are configured against the prefix.

**Fix:** one change set — rename the package directory, set `env_prefix`, keep `DESIGNAGENT_*` as
accepted aliases for one release so existing `.env` files keep working, then retire them.

---

## C — upstream

These were found while building on the middleware and are the asks on slide 18 of the code walk.
`slides/CODE_FOR_DECK.md` carries the verbatim code and the reproduction for each.

### C1 · flowgentic hard-imports `aiohttp` and `httpx`
`fault_tolerance.py:71–72` and `:83–84` do `except Exception: raise` around imports the comment
describes as optional ("Try to include aiohttp timeouts **if present**"). Both become hard
requirements. Fires whenever `RetryConfig.retryable_exceptions` is left at its default, which is `()`
— the common case.

**Worked around** by installing `aiohttp` (see the comment in `pyproject.toml`) and passing
`retryable_exceptions` explicitly. Looks like a one-word fix upstream: `raise` → `pass`.

### C2 · `EXECUTION_BLOCK` loses the caller's runnable context
A node wrapped as one runs on asyncflow's loop, so LangGraph's `get_stream_writer()` raises
`Called get_config outside of a runnable context` and every custom event is dropped **silently** —
the graph completes and the chat goes quiet. Isolated both ways: a plain node streams its status
lines, a wrapped one streams none.

**The open question:** is `EXECUTION_BLOCK` *meant* to propagate context? If yes, bug. If no,
flowgentic's node wrapping and LangGraph's streaming are mutually exclusive and that belongs in the
README. Until it is answered, `DESIGNAGENT_WRAP_NODES` has to ship `false` against the approved
design (`config.py:37–40`), which is the deviation slide 17 of the code walk leads with.

### C3 · `RetryConfig` defaults cancel long work silently
`timeout_sec=30.0`, `max_attempts=3` (`fault_tolerance.py:23–37`). Measured against this repo's own
workload, folds ran 13.3–42.4 s, so the default would have cancelled and re-run roughly half of one
round. Reasonable for a service call, wrong for the agent tasks flowgentic exists to wrap.

### C4 · Orbit's terminal task event omits stdout
A completed rhapsody task resolves with an empty result until `get_task` is called a second time.
Worked around in `orbit.py:315` `_finish_task_enriched`. This and C5 both produce a *silent wrong
answer* rather than an error, which is what makes them the two worth fixing first.

### C5 · A FAILED Orbit job carries no reason
Only a non-zero exit code reaches the client. `orbit.py:376–382` synthesises an explanation from the
exit code, then stderr, then the log tail, so the user sees something — but the real reason never
left the endpoint.

### C6 · PSI/J spec fields are dropped in transit
`to_psij_spec` (`orbit.py`) does not forward `stdin_text` or `outputs`, and neither does the broker's
`plugin_psij.py`. `fold_job_spec` (`esmfold.py`) relies on `stdin_text` to feed a sequence without
hitting argv limits, and both job specs declare `outputs` for staging. Reproduce by submitting
`fold_job_spec(...)` through `hpc` and watching the remote command read an empty stdin.

**Still open upstream, and worked around in one place.** Re-checked against `radical.orbit`
`c7ede0c` (2026-10-07): `PluginPsij.submit_job` reads `executable`, `arguments`, `directory`,
`environment`, `attributes`, `resources` and `custom_attributes`, and still neither `stdin_text` nor
`outputs`. `refcodes/` is gitignored and an editable
install with no recorded revision (**B1**), so a patch to `plugin_psij.py` is neither shippable nor
verifiable by anyone else. The client therefore honours both fields in band, over the one channel the
broker does carry whole: stdout. `tasks/hpc/artifacts.py` rewrites any spec declaring `inputs` or
`outputs` into a `bash -lc` script that reassembles its inputs from argv, redirects the real
command's own output to stderr, and prints each declared output as a framed block carrying its size
and sha256. `OrbitInterface._submit_job` applies it to every job spec, so `fold_job_spec` gets the
same treatment; `TaskManager._materialize_artifacts` turns the returned bytes into blob paths before
anything persists them. A spec declaring neither field passes through unwrapped.

Two ceilings that workaround buys, both measured:

* **Inputs**: `MAX_ARG_STRLEN` caps a *single* argv element at ~128 KiB, so the payload is gzipped,
  base64'd and split into 64 KiB chunks; the limit on all of them together is `ARG_MAX`, megabytes.
  A 533 KB incompressible input round-trips in 9 chunks.
* **Outputs**: bounded by `orbit_artifact_max_bytes` per file and `orbit_job_output_max_bytes` in
  total. An over-budget file is *named* in the manifest rather than dropped.

The protocol detects rather than infers a short read: a missing closing frame, a missing manifest, a
file count below the manifest's, and a digest mismatch are all distinguishable, which matters because
a clipped base64 payload is still valid base64. Pinned by `tests/test_artifacts.py` (15 offline tests
that run the generated script with bash) and three `-m live` tests through a real broker.

**What would close this:** the broker forwarding `outputs` and `stdin_text`, at which point the
wrapper becomes a fallback for older endpoints rather than the only path.

### C7 · Broker documentation gaps
Two afternoon-sized traps: there is no HTTP topology route (`/topology` is read as a plugin name and
404s after a 307, so readiness must come from the client's `rt.topology()`, which propagates
asynchronously) — **half-resolved at `c7ede0c`**, whose gateway serves token-gated `GET /endpoints`
and `POST /endpoint/list`, so a readiness check exists, just not at `/topology`; and `--no-auth` disables *ingress auth only* — the broker still serves TLS and
refuses to start without a cert and key. Both are documentation fixes, not code.

### C8 · asyncflow swallows SIGTERM, so the backend never exits on its own
`radical.asyncflow.workflow_manager` installs its own `SIGTERM` handler. On a
`kill`, it logs *"Received external shutdown signal (SIGTERM), initiating graceful shutdown"*,
then *"Shutdown completed for all components"* — and the process stays alive. uvicorn's own shutdown
never runs, so `app.lifespan` never closes Kuzu or the checkpoint store. Deterministic: 3 of 3 clean
attempts, each needing `SIGKILL` after a 30 s wait.

Consequences. Every backend stop is effectively a kill, so the graph store is never closed cleanly
and the checkpoint WAL is never checkpointed on exit — both are crash-safe by design, which is why
this has never shown as corruption, but neither gets the orderly path it has code for. And a stop
takes 30 s longer than it should, which is most of what `./scripts/dev.sh down` spends its time on;
the script treats escalation as the expected route rather than an error.

Reproduce: start the backend, `kill <pid>`, watch the log print a completed shutdown while the
process keeps answering `/api/health`.

**The ask:** asyncflow should not install a process-wide signal handler when it is a library inside
someone else's server — or should re-raise after its own teardown so the host's handler still runs.
Until then the only correct client behaviour is to escalate, which cannot distinguish "hung" from
"finished but did not exit".

---

## D — needs investigation

### D1 · A turn is still not recorded anywhere durable
The per-turn trace lives in state and is reset each turn, so the checkpoint holds the *current* turn
only: once the next prompt arrives, the previous turn's path is gone. Good enough to explain the
answer on screen, not enough to compare turns across a session or a restart.

**What blocks the obvious fix.** `record_analysis` keys its row
`f"{campaign_id}:{kind}:{round_no}"` with `ON CONFLICT DO UPDATE` (`lake/scores.py`), so two turns in
the same round overwrite each other — a turn is not a concept the lake models. Persisting traces means
either a turn id in that key or a `Turn` entity in tier 1, and then a route to read them back.

**Also not recoverable:** attribution *within* one message. The interpreter's reply is the summary plus
an appended caveats block, transmitted as one string with one `reply_source`; and task handles live in
an in-memory dict, so `/api/tasks` empties on restart while the lake's Task rows persist.

### D1a · One unexplained `-m live` failure, seen once
`test_real_proteinmpnn_runs_and_its_samples_reach_the_adapter` failed once in a full `-m live` run
that immediately followed the offline suite, two Vite builds and the jsdom tier. It then passed in
isolation and in **five** further full runs, including a deliberate replay of the same sequence. The
traceback was not captured — the run was `-q | tail -2` — which is the first thing to fix if it
recurs.

Not called fixed, and not dismissed. The honest state is: observed once, unexplained, 5/5 green
since.

It has more moving parts than any other test: a broker subprocess, an endpoint subprocess, a real
torch process and a 600 s future. One structural race is worth naming because it fits the
circumstances — `_free_port` (`tasks/hpc/local_orbit.py`) binds port 0, reads the assigned port and
**closes the socket**, and the broker binds it only later. Anything can take the port in between,
including another `LocalOrbitStack` doing exactly the same thing, and during the failing run a
`dev.sh` backend was up with its own stack while each test span a throwaway one. Under load that
window widens.

**If it recurs:** run `-m live` with `--tb=long -s` and keep the broker log
(`<tmp>/orbit/logs/broker.log`). If it is the port, the broker's log says the address is in use. The
fix would be for `LocalOrbitStack` to hold the socket until the child inherits it, or to retry on a
bind failure rather than assuming a chosen port stays free.

### D2 · The LLM classifier path is unmeasured
Everything known about routing comes from `classify_rules`: the reported session, the whole reference
campaign and all 417 offline tests run with `llm: false`. When a key is present, `CLASSIFY_SYSTEM`
(`coordinator.py`) decides instead and the rules are never consulted — so the phrasings fixed in the
rule path ("label the active site residues", "run another round") are unverified there.

**Unblocked, not answered.** `tests/test_llm_live.py::test_llm_routing_matches_the_rule_table`
(`-m llm`) replays the two tables in `test_graph.py` — `CASES_WITHOUT_REFERENCE` and
`CASES_WITH_REFERENCE`, 26 phrasings — through `CLASSIFY_SYSTEM` and prints every divergence. It
reports rather than fails, because the fix for a divergence is a prompt change and a red test would
make the tier useless for finding them. **Still needs a key and one run.**

Already found while wiring that tier, with a key present: `build_llm` passed `temperature=0.0`, which
`claude-sonnet-5-5` rejects outright — so *every* LLM call failed and fell back to rules. Invisible
with no key, because the call never happened. Fixed in `llm.py`; the lesson is that an unmeasured path
is not a working path.

### D3 · ESM Atlas drops requests under concurrent load
One of six round-2 folds came back with no structure (`s-tutp87hw-r2-5`). The campaign absorbed it
correctly — sequence-only scoring, a warning to the user — so this is not a bug report against us.
But the fold path has **no retry**, and a 1-in-6 drop rate at a fan-out of 6 is high enough that it
is probably load-related rather than random.

**Worth knowing:** whether it is rate limiting (then back off and retry), a size limit interacting
with the 400 aa threshold, or genuinely random. One scripted run of 20 folds at varying concurrency
would answer it.

### D4 · `-m live` on an HPC login node is expected to lose its function-task test
`LocalOrbitStack` starts its endpoint with `-p rhapsody,psij` (`tasks/hpc/local_orbit.py`). Orbit
loads rhapsody only when `utils.host_role` reports `compute` or `standalone`
(`plugin_rhapsody.py: is_enabled`, at `c7ede0c`); a login node with Slurm installed and no allocation
is `login`, so rhapsody is skipped with an INFO line and the endpoint serves psij alone.
`tests/test_orbit_local.py::test_executable_task_runs_and_returns_output` submits `kind="function"`,
which `OrbitInterface._submit_task` refuses with "endpoint has no rhapsody plugin". Read from the
code on `amarel3`, not run — the venv does not exist yet. Repro: `pytest -m live` on a login node,
then again under `srun`, where it should pass. If confirmed, the test wants a skip naming the role
rather than a failure.

**Confirmed and fixed, 2026-10-21.** `-m remote` from `amarel3` failed
`test_a_rhapsody_task_runs_at_the_far_end` with exactly that message, so the gate is real and not
just a reading of the code. Both tests now skip on `not capabilities.supports_function_tasks`, a new
`Capabilities` field that `OrbitInterface` resolves from the live endpoint's plugin set rather than
declaring as a class constant (`tasks/hpc/orbit.py`). The `-m remote` tier is **7 passed, 1 skipped**
on a login-node endpoint.

Worth recording because it changes how the entry reads: the veto is **absolute**, not a default.
`is_enabled` is checked before instantiation, so `-p rhapsody` on a login node is refused too — the
reason upstream gives is that Dragon's launcher raises outside an allocation, and
`radical-orbit-endpoint-wrapper.sh` re-implements the same login-node test in shell. `psij` is the
deliberate opposite, with `is_enabled` returning `True` unconditionally: "batch system access
matters, not the participant role". So this was never a thing to work around — a login-node gateway
serving psij alone is the deployment Orbit intends (`AMAREL_ENDPOINT.md` §2, re-verified against
`c7ede0c`).

The deployment ladder avoids it: `plans/AMAREL_ENDPOINT.md` runs rungs 1–2 on the Linode VM, which
has no batch system, so the role is `standalone` and rhapsody loads.

### D5 · Job output now crosses the internet, at an unmeasured rate
With the broker and agent on a Linode VM and the endpoint on `amarel3` (`plans/AMAREL_ENDPOINT.md` §2),
every job's stdout — and with it every in-band staged file (`tasks/hpc/artifacts.py`) — travels
endpoint → broker → agent over WSS from Rutgers to the Linode region (Newark, if chosen), where the dev stack only ever moved it
over loopback. The size is bounded by `orbit_artifact_max_bytes` and `orbit_job_output_max_bytes`; the
time is not measured, and `_read_whole_stdout` and the task timeouts were tuned on loopback. Rung 4
came and went without the measurement — its jobs printed a few lines each — so this now belongs to
rung 5. Repro: a job that stages out a file of known size (the `-m live` staging tests are the
template), timed end to end. If it is slow, the protocol's large outputs (AF3 models) should stay in `$PROJ` and
come back only as summaries — which `af3_collect` already does.

### D6 · Closing the Orbit interface hangs for five minutes after a job-heavy run
Every `-m remote` run that actually completes jobs spends **about five minutes past the last
assertion** before the process exits. Measured 2026-10-21/22 on `amarel3`: pytest reported
`7 passed, 1 skipped … in 439.93s`, and the process did not exit until 00:17:27 — the log's own
timestamps put ~5 minutes between the two. The run prints, in order:

```
RuntimeWarning: The executor did not finishing joining its threads within 300 seconds.
Task was destroyed but it is pending!
task: <Task pending ... coro=<EndpointRuntime._call() running at .../orbit/runtime.py:859>>   (x5)
```

300 seconds is exactly the warning's threshold, so the five minutes *is* the executor join timing
out, and the five abandoned tasks are `EndpointRuntime._call()` coroutines still waiting on a reply
future when the loop is torn down. Five is suggestive: it is the number of Slurm jobs the run
submitted, so the likely shape is one orphaned call per job — a poll or status request whose reply
never arrived, or arrived after we stopped listening.

**Why it is not just noise:** `OrbitInterface.close()` is what `Runtime.reconfigure` and app
shutdown call. If the same five minutes is paid there, a settings change that rebuilds the Orbit
interface would stall the server, and nothing in the offline suite can see it — every local test
uses an in-process manager.

**Not caused by the capability change:** it appears in the 2026-10-21 `JOB_GPUS=0` run that predates
it, and is absent from the first run only because three of those tests failed at submission and so
never polled.

**Repro:** `-m remote` from `amarel3` with `DESIGNAGENT_ORBIT_JOB_GPUS=0`, timed end to end; compare
pytest's own duration against wall-clock process exit. Then instrument `close()` (`tasks/hpc/orbit.py`)
to log before and after each client's teardown, to find whether the wait is ours or Orbit's.

---

## Maintenance

### M1 · The deck cites 46 source line numbers
Any backend refactor can drift them. `slides/check_anchors.py` re-derives every one and reports where
a moved line actually is; run it before presenting and after any significant edit. The table is not
self-maintaining: three citations in `CODE_FOR_DECK.md` had no anchor and drifted unnoticed until the
deck was scanned for every `file:line` it prints. When adding a citation, add the anchor — including
the four added when the deck went to three acts, which are cited as *numbers* on slide 16 rather than
inside a code block, which is exactly the kind that rots unnoticed. `run.json`, `staging.json` and
`DECK_SCRIPT.md` are generated — see `CLAUDE.md`.

### M2 · `run_model.py` has no campaign pin, so re-running it rewrites the deck's story
The deck's figures come from `slides/run.json`, which `run_model.py` mines from `data/lake`. It
aggregates the **whole** lake and takes whichever campaign Kuzu lists first. Run on 2026-10-09 it
turned the committed figures — 12 designs, 37 tasks, 114 scores, 2 rounds, a pLDDT progression of
81.66 → 81.79 — into 222 designs, 641 tasks, 2244 scores, 37 rounds and a different campaign's best
value, because every browser check and protocol test since went into the same data dir. Nothing
warns: it exits 0 and the deck builds, narrating numbers that no longer match the story around them.

`slides/staging.json` is a separate file for this reason: F5 can be re-measured on a new host
without any chance of touching the campaign the rest of the deck narrates.

So the committed `run.json` is deliberately **not** regenerated. Only its `code` block was refreshed
in place on 2026-10-09 (12 047 backend lines, 6 527 of tests), and a `note` key in the file records
the split; `run_model.py`'s docstring says the same.

**Fix:** take the campaign id as an argument, defaulting to the one the deck was built on
(`s-tutp87hw`), and filter tiers 1–3 by it — tier 2's `rounds` and `by_metric` and tier 3's `sets`
are already per-campaign in shape, so this is a `WHERE`, not a redesign. Reproduce on any data dir
with more than one campaign: run it and diff `run.json`.

### M3 · The sensitive scan cannot see the committed deck binaries, and grepping them misleads
`slides/designagent-codewalk.pptx` and `.pdf` are tracked build artifacts, and neither a scan nor an
ad-hoc grep reads them correctly.

Both failure directions were seen on 2026-10-09, during the redaction pass of **A24**:

- **False positive.** `git grep -E '([0-9]{1,3}\.){3}[0-9]{1,3}'` reported the `.pptx` as matching,
  which read as "the Linode address is baked into the deck". It is not. Unzipping every pptx part and
  inflating the PDF's zlib streams found the address in neither file, and in no slides *source*
  either — the match was compressed bytes that happen to look like a dotted quad. A deck rebuild was
  scoped and then dropped.
- **False negative.** A scanner that passes `git grep -I`, as `repo-sensitive-scan` did, skips
  binaries silently. Had the address been in there, nothing would have said so.

**Risk if ignored:** both answers are wrong in a way that costs time. The false positive sends you
rebuilding a deck for nothing; the false negative publishes a value you believe you redacted.

**Fix:** check binaries for *literal* values with container awareness — pptx/docx/xlsx are zip
archives and PDF content sits in zlib streams, so the bytes on disk are not the text. Done for the
skill; the repo-side half is that the fix for a build artifact is always regenerating it from
redacted sources, never editing the blob, which `NODE_PATH=<dir> node slides/build_deck.js` does.
