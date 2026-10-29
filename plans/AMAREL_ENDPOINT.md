# Standing up Orbit: endpoint on Amarel, broker on a Linode VM

What the enzyme-redesign protocol needs that is not code. Every heavy step of that protocol is
site-bound — the UniRef30 database, the ProteinMPNN and HaloMPNN weights, the `aifold` /
`shared_als515` / `pyr2` conda environments, IPC2, PyRosetta, the AlphaFold3 apptainer image — so it
runs through the `hpc` task interface, which means through an Orbit broker and an endpoint on
Amarel. Until that pair is up, `TaskManager.interface_for` has no `"hpc"` key and degrades to
`local` with only a `log.info` (`tasks/manager.py:76-88`).

**The decided layout (2026-10-08):** the Orbit **endpoint** runs on the Amarel login node `amarel3`;
the **broker** and the **PEAT agent** run on one Linode (Akamai Cloud) VM. Nothing listens on
Amarel. §2 has the full picture, §3 the Linode half, §4 the Amarel half.

This is a deployment record as much as a procedure: fill in what each rung of the ladder at the
bottom actually returned, so the next person reads results rather than intentions.

### Placeholders

This repository is public, so the host and account identifiers this record was written with are
replaced by the names below (backlog **A24**). Substitute the real values from the running
deployment; nothing here is guessable from the repo, and that is deliberate.

| Placeholder | What it stands for |
| --- | --- |
| `<linode-ip>`, `<linode-ip6>`, `<linode-rdns>` | the VM's public IPv4, its IPv6 block, and its reverse name — the last is the hostname the UI's certificate is issued for |
| `<campus-nat-addr>`, `<campus-nat-rdns>` | the campus NAT address the endpoint's traffic was seen leaving from, and its reverse name. A *pool* address: it may change, which is why §3 allow-lists a range |
| `<campus-block>` | the university's IPv4 block, as returned by `whois` for the address above |
| `<netid>` | the operator's cluster account — a home and `/scratch` path component, and the admin login on the VM |
| `abc123`, `/projects/f_proj00_1` | a campaign netid and an allocation directory, in examples and test fixtures. Both are fictitious and shaped to validate |

Status as of 2026-10-09: **rungs 0-pre and 0a–3 pass.** The broker runs on the Linode under systemd,
and the endpoint on `amarel3` has dialled in over the internet and registered with `psij,sysinfo`.
The agent's own probe sees it. **No job has been submitted yet** — rung 4 is the first that spends
queue time — so everything about PSI/J against the real Slurm (§6's four things to write down) is
still unverified. The ladder at the bottom records what each rung returned.

### Environment survey, 2026-10-07

**Amarel**, taken read-only from a session on `amarel3` itself (`amarel3.amarel.rutgers.edu`, a login
node, no `SLURM_JOB_ID`), with the repo at `/cache/home/<netid>/rePEAT`. Nothing was started.

| Check | Result | Consequence |
| --- | --- | --- |
| host | `amarel3`, 64 cores, ~86 users logged in | loopback is shared with every one of them — §2.1 |
| outbound network | no proxy variables; TCP to an external host connected on 443, 8000 and 8443 | the endpoint can dial a cloud broker directly, no tunnel |
| egress address | `<campus-nat-addr>`, reverse DNS `<campus-nat-rdns>` | a Rutgers **NAT pool**: the source address may change, so allow-list the range, not one IP — §3 |
| Orbit install | `.venv/` and `refcodes/` absent; nothing for Orbit | §4 installs `radical.orbit` alone; the endpoint needs nothing else from this repo |
| system python, `uv` | 3.9.21; `uv` in `~/.local/bin` | a 3.12 venv via `uv` — Orbit needs ≥3.10 |
| `~/.radical/orbit` | absent | §4 |
| Slurm | `sbatch`/`sacct`/`squeue` on `PATH`; `sacctmgr` association `general`, qos `normal` | §5 values; the group `g_sdk94_1` and partition `p_sdk94_1` suggest a lab allocation may be the one to charge |
| partitions | `main`* `gpu` `mem` `nonpre` `cmain` `cgpu` `cmem` `p_sdk94_1`, all 3-day limits but the last | `protocol_gpu_queue=gpu` matches a real partition |
| `127.0.0.1:8000` | LISTENing, held by another user | matters only to the dev stack, if anyone runs it on Amarel |
| `apptainer`, `module` | present; `/projects/community/modulefiles` offers `alphafold/vs3.0.0-pgarias` | a candidate for `protocol_af3_modules`, unverified |
| keep-alive | `tmux`, `screen` present; `systemd --user` runs but `Linger=no` | §4 |
| `$HOME`, `/scratch/<netid>` | both `0700` | cert and token files stay private |

**Orbit source:** reference checkout at `/home/<netid>/radical.orbit` — 0.8.0, branch `devel`, commit
`c7ede0c` (2026-09-29), clean. Every flag in §1 was checked against it and matched. `refcodes/` holds
no recorded revision (backlog **B1**), so re-read `--help` against any other revision. Upstream's own
`DEPLOYMENT.md` in that checkout is the companion to this document.

**Linode**, as built 2026-10-09 (`plans/LINODE_DEPLOY.md` is the deployment plan for this host).

| Item | Value |
| --- | --- |
| label / region | — / Newark, NJ (IPv6 `<linode-ip6>`) |
| plan / image | **Linode 4 GB** (2 vCPU AMD EPYC 7642, 3.9 GB RAM, 79 GB disk) / Ubuntu 24.04.4 LTS. The floor below, not the 8 GB recommended; final. Plus a 2 GB swapfile |
| public IPv4 | `<linode-ip>`, reverse `<linode-rdns>` — see **Placeholders** below |
| Cloud Firewall | not yet confirmed. The host's UFW mirrors §3 step 2: 22 any, 8443 from `<campus-block>`, plus 80/443 any for the UI (`LINODE_DEPLOY.md` Phase 1) |
| broker port | `8443`, `orbit-broker.service`, broker RSS 81 MB at idle |
| cert generated (expires +365 d) | 2026-10-09, expires **2027-10-09 03:22 UTC**; SAN `IP:<linode-ip>, IP:127.0.0.1` |

---

## 1 · The two processes

Orbit is a broker plus one or more endpoints. The broker is a server; **the endpoint is a client of
the broker**, and so is this agent. That single fact decides the topology (§2).

`radical-orbit-broker.py`:

| Flag | Notes |
| --- | --- |
| `--cert PATH` | CLI > `$RADICAL_ORBIT_BROKER_CERT` > `~/.radical/orbit/broker_cert.pem` |
| `--key PATH` | CLI > `$RADICAL_ORBIT_BROKER_KEY` > `~/.radical/orbit/broker_key.pem`. **Refuses to start if the file is more permissive than 0600.** |
| `--host` / `--port` | default `0.0.0.0` / `8000` |
| `--plugins`, `-p` | default `default` (the broker role's set); `all`, `""`, and wildcards like `iri*` are accepted |
| `--token T` | CLI > `$RADICAL_ORBIT_BROKER_TOKEN` > `~/.radical/orbit/broker.token`. **Required unless `--no-auth`, and never auto-generated.** |
| `--no-auth` | local dev only; disables the ingress gate |
| `--no-gateway` | headless: only the token-gated WebSocket `/register`, no HTTP/SSE tier |

`radical-orbit-endpoint.py`:

| Flag | Notes |
| --- | --- |
| `--name`, `-n` | **Set it explicitly.** Defaults to `socket.gethostname()`; the source comments that a serving endpoint wants a stable, recoverable name. This is what `DESIGNAGENT_ORBIT_ENDPOINT` must match. |
| `--url`, `-u` | CLI > `$RADICAL_ORBIT_BROKER_URL`. **No file fallback** — unlike the cert and token. `https://` is rewritten to `wss://`. |
| `--cert`, `-c` | CLI > `$RADICAL_ORBIT_BROKER_CERT` > `~/.radical/orbit/broker_cert.pem` |
| `--token`, `-t` | same resolution as the broker's |
| `--plugins`, `-p` | **must include `psij`**, which submits every batch job. Use **`psij,sysinfo`** (below). Default `default` expands by host role (`plugin_host_base.py`) |
| `--tunnel` | `none` \| `forward` \| `reverse` — only for an endpoint inside an allocation (§2.3) |
| `--tunnel-via HOST` | login host for `forward`; falls back to `$SLURM_SUBMIT_HOST` |
| `--log-level`, `-l` | or `RADICAL_ORBIT_LOG_LVL`, falling back to `RADICAL_LOG_LVL`; `DEBUG` before a first run is worth it |
| `$RADICAL_ORBIT_PSIJ_DIR` | where PSI/J keeps job stdout (`output/`) and generated submit scripts (`work/`). Default `~/.radical/orbit/psij` — **set it to `/scratch/<netid>/orbit-psij`**; upstream moved it after a full home quota failed every submit on Perlmutter |
| `$RADICAL_ORBIT_PSIJ_KEEP_FILES=1` | keep PSI/J's generated sbatch scripts — the direct way to see the `--chdir` and `--mem` it rendered (§6, rung 4) |

**Rhapsody does not load on a login node.** `tasks/hpc/orbit.py` opens a session on each of `rhapsody`
and `psij` *if the endpoint has it*, but Orbit decides per host: `PluginRhapsody.is_enabled` is true
only when `utils.host_role` says `compute` (inside an allocation) or `standalone` (no batch system).
On `amarel3` Slurm is detected and there is no allocation, so the role is `login` and rhapsody is
skipped with one INFO line (`[PluginHost] Skipping plugin (not applicable here)`). With the explicit
`-p psij,sysinfo` below there is no skip line at all — rhapsody is never asked for; the 2026-10-09
startup logged only `Loading plugins: ['psij', 'sysinfo']` and one load line each, and `host_role`
confirmed `role: login`. PSI/J loads on every host. That costs this path nothing: every `hpc`
submission here is `kind="job"` (`nodes/orchestrator.py`, `nodes/protocol.py`), which goes to psij.

**And the veto is absolute, not a default.** Asking for it does not lift it: `is_enabled` is a
classmethod checked *before* instantiation (`plugin_host_base.py:166-169`), so `-p rhapsody` on a
login node is refused the same way. The reason upstream gives is Dragon — "both can host Dragon
workers; brokers and login nodes deliberately don't load Rhapsody"
(`plugin_rhapsody.py:1440-1447`) — and `bin/radical-orbit-endpoint-wrapper.sh:95-109` re-encodes the
same login-node test in shell, because "dragon's launch_selector raises in that case". psij is the
deliberate opposite: `is_enabled` returns `True` unconditionally, commented "PsiJ loads on any host,
including a broker embedded on a login node — batch system access matters, not the participant role"
(`plugin_psij.py:703-707`), and pinned by a test that loads it on a broker. So the two plugins are
asymmetric on purpose: psij is steered by `default_roles`, rhapsody is hard-walled by role.

Observed on 2026-10-21: `-m remote`'s one function-task test failed here with "endpoint has no
rhapsody plugin", which is this gate working as designed. It now skips rather than fails — backlog
**D4**, and the reasoning is in the test's own docstring.

**Why `psij,sysinfo` and not the default.** On a login node `default` is
`psij,staging,sysinfo,queue_info,globus` — `globus` only when `globus-sdk` imports, which it does
not here, and the set is derived per role rather than listed anywhere: `default_plugins(role, ...)`
selects every plugin whose class names that role in `default_roles`
(`plugin_host_base.py:19-26`), and upstream's `tests/unittests/test_plugin_host_base.py:149-176`
pins the result for all four roles. Upstream's `plans/security_token_mitigation.md` describes psij
submit as arbitrary command execution and staging as able to create files under `$HOME`; both sit
behind the token, but nothing here uses `staging`, so it need not be exposed. `sysinfo` adds the
`host_role` route, which reports the role and batch system the endpoint actually detected.

Cert and token both fall back to files under `~/.radical/orbit/`, so once those are placed, neither
process needs a flag for them. Only `--url`, `--name` and `--plugins` have to be passed.

**Every path must be absolute.** `LocalOrbitStack` resolves its work dir in `__init__` for exactly
this reason: `_spawn` runs the children with `cwd=work_dir`, so a relative `--cert` resolved against
the work dir twice, and the broker exited "TLS cert not found" (the trap recorded in `CLAUDE.md`).

---

## 2 · Topology: who dials whom

| Process | Host | Kept alive by | Connects to |
| --- | --- | --- | --- |
| broker | Linode VM, `0.0.0.0:8443` | a systemd service (§3) | nothing — **the only listening port in the deployment** |
| agent (PEAT backend) | the same VM, `127.0.0.1:8000` | `tmux` or a systemd service (§3) | the broker at `https://127.0.0.1:8443` |
| endpoint | `amarel3` login node | `tmux` (§4) | the broker at `https://<linode-ip>:8443`, outbound |
| protocol jobs | Amarel compute nodes | Slurm, one job each | nothing |
| Caddy | the same VM, `:80`/`:443` | `caddy.service` | the agent at `127.0.0.1:8000`; serves the built frontend from `/srv/repeat/www` |
| browser | the user's laptop | — | Caddy, over HTTPS; the app's own logins are the gate (`plans/LINODE_DEPLOY.md` Phase 2); `ssh -L` still works |

This is Orbit's intended pattern, not an adaptation of it. Upstream's `DEPLOYMENT.md` puts the broker
on a "public-facing" host under systemd and the endpoint on "one per cluster or login node",
and says the reason: "endpoints initiate the outbound WebSocket connection to the broker. No inbound
ports need to be opened on the HPC firewall." `docs/machine_guide.md` gives the same order for the
PsiJ path: start the broker "somewhere reachable from the machine", then the endpoint on the login
node. The survey confirmed the one thing this layout needs from Amarel — outbound TCP from `amarel3`
to an arbitrary port.

**Re-verified against the `c7ede0c` checkout, 2026-10-21** (the question: is a login-node endpoint
holding no batch job the intended gateway to Slurm, or an improvisation?). It is intended, and the
intent is encoded in the plugins rather than only asserted in prose:

- `utils.py:311-356` defines four host roles — `broker`, `login`, `compute`, `standalone` — and
  `login` is a *named* role, not an error case: scheduler on `PATH`, no allocation env var. It is
  exactly what `amarel3` is.
- `psij` declares `default_roles = ('login', 'standalone')` and `rhapsody` declares
  `('compute', 'standalone')`. Upstream's own gloss: "That is why a login node gets `psij` by
  default while a compute node gets `rhapsody`" (`docs/tutorial_plugin.md:235-250`). The division
  of labour *is* the design — submit on the login node, execute in the allocation.
- `DEPLOYMENT.md:140-154` names "long-running daemon processes on login nodes" as a deployment
  mode, and its login-node launch line carries `psij` and omits `rhapsody`.
- `docs/machine_guide.md:22-28` makes "On the machine's login node, start an endpoint" the numbered
  happy path; compute nodes follow only as the firewalled case needing `--tunnel`.
- `docs/rest_api.md:177-182` lists "endpoint on a login node → no SLURM job → `{"allocation": null}`"
  **first**, as the normal state — which is the direct answer to "must a job hold the endpoint?" No.
- `docs/plugin_federation.md:216-219` and `federation_state.py:55-62` formalise the two as named
  join modes: `login`, where "the endpoint sits on a login node and declares what it may ask the
  batch system for", versus `allocation`, where "the endpoint that joined **is** the pilot".
- Upstream has *hardened* the split rather than blurred it: `plans/122-allocation-endpoint-is-the-pilot.md`
  fixed a dispatcher that treated an allocation member like a login member, noting "the compute-node
  default plugin set has no `psij`".

Two gaps are upstream's, not ours, and worth knowing. There is a systemd unit for the **broker**
(`DEPLOYMENT.md:116-138`) but **none for an endpoint**, despite recommending login-node daemons —
`DEPLOYMENT.md:180-191` adds that sessions are not persisted and tells clients to wrap their loop in
a reconnect strategy. So `tmux` plus `scripts/amarel_endpoint.sh` fills a real gap rather than
ignoring advice, and §4's keep-alive question has no upstream answer to defer to. And nothing
upstream discusses login-node *site policy* — process reapers, CPU limits, how many endpoints per
node; that is Amarel's question to answer, not Orbit's.

The broker holds no job state; sessions live in the endpoint. The agent sits beside the broker
because it is the broker's only other client, the protocol's scripts are read on the agent's host
(§3), and on a single-user VM the agent's loopback assumptions hold (§2.1).

### 2.1 · Exposure

- **The broker's port faces the internet**, so the token is the gate. Auth is on by default and must
  stay on: **never `--no-auth`** on this broker. Narrow the port with a Linode Cloud Firewall to the
  Rutgers range the endpoint egresses from (§3), and keep the key on the VM.
- **The agent's backend stays on loopback**, and Caddy fronts it (`plans/LINODE_DEPLOY.md`
  Phase 1). It ships no CORS middleware, and `_authorize_write` (`app.py`) opens settings writes to
  loopback callers when `DESIGNAGENT_ADMIN_TOKEN` is unset.
  - **Behind a same-host proxy that test is true for every caller** (backlog **A17**). On this VM
    logins are on, so the settings routes need a signed-in admin and the bind address is never
    consulted. With logins off, the token would be mandatory.
  - Never bind the backend to a public address.
- **Nothing listens on `amarel3`.** The shared-loopback problem found in the survey — about 86 users
  on one `127.0.0.1` — now matters only to the dev stack (`pytest -m live`,
  `DESIGNAGENT_ORBIT_LOCAL=true`), which starts a `--no-auth` broker (backlog **A16**). Run that on
  the VM (§6), not on Amarel.

### 2.2 · Is Orbit started inside an Amarel job script? No

**No Orbit code runs inside any Slurm job.** The endpoint is a long-lived login-node process. The batch
scripts are generated by PSI/J on the endpoint from the specs in `protocol/specs.py` (the skill's own
`#SBATCH` headers are dead on this path), and each contains only that step's `bash -lc` body. A job
never connects to the broker: the endpoint submits it with `sbatch`, polls it through Slurm, and reads
its stdout file from the shared filesystem — which is why `RADICAL_ORBIT_PSIJ_DIR` must be on one
the compute nodes write to.

So the agent needs exactly four things, all fixed for the life of the deployment, and nothing per job:

| What the agent needs | Setting | On the VM |
| --- | --- | --- |
| broker URL, as reachable from the agent | `RADICAL_ORBIT_BROKER_URL` | `https://127.0.0.1:8443` |
| broker cert, to pin | `RADICAL_ORBIT_BROKER_CERT` | `~/.radical/orbit/broker_cert.pem` |
| ingress token | `RADICAL_ORBIT_BROKER_TOKEN` or the token file | `~/.radical/orbit/broker.token` |
| endpoint name | `DESIGNAGENT_ORBIT_ENDPOINT` | `amarel3`, the endpoint's `--name` |

plus the scheduler choices the agent puts on each spec (`DESIGNAGENT_ORBIT_PSIJ_EXECUTOR`,
`_ACCOUNT`, `_QUEUE`; §5). The agent never needs a compute node's hostname or a port inside the
cluster; a job's Slurm id comes back to it in `handle.meta["native_id"]`. The endpoint needs three of
the same things — URL (the VM's public IP), cert and token — copied to `amarel3` (§4).

### 2.3 · Fallback: the endpoint inside an allocation

Only if site policy forbids a long-lived login-node process. Then Orbit *is* started from a job
script: an `sbatch` script whose payload is the endpoint (upstream `DEPLOYMENT.md` has a template).
The broker does not move. Costs: transfer specs run with `executor: "local"`, so they run inside the
allocation and die with it; and the endpoint itself dies at the walltime — at most 3 days on `main` —
which a multi-day protocol outlives.

```bash
#!/bin/bash
#SBATCH --partition=main --time=3-00:00:00 --cpus-per-task=4 --mem=16G
# --name: fixed, because the default is the compute node's hostname
# -p:     psij named explicitly, because the compute-node default set lacks it
export RADICAL_ORBIT_PSIJ_DIR=/scratch/$USER/orbit-psij
radical-orbit-endpoint-wrapper.sh \
    --name amarel-alloc \
    -p psij,sysinfo,queue_info \
    --url https://<linode-ip>:8443
```

No tunnel, *if* compute nodes have outbound access like `amarel3` does — untested. If they do not,
add `--tunnel forward --tunnel-via amarel3` and `--url` stays the same: the `ssh -L` lands on
`amarel3`, which can reach the VM. That needs passwordless ssh from compute nodes to `amarel3` (the
user's key is in `authorized_keys`; whether Amarel allows compute→login ssh is unverified).

The agent's four settings do not change except the endpoint name. What is new is information about
the endpoint's own job:

| New information | Why the agent needs it | How it can get it |
| --- | --- | --- |
| the endpoint is up | queue wait is unbounded; a round planned before registration silently takes the heuristic branch | `GET /endpoints` (§3), or `rt.topology()` |
| the allocation's end time | work submitted near the end is lost: `executor: "local"` transfer jobs die with the allocation, and Slurm jobs it submitted survive but their handles do not | `queue_info`'s `job_allocation` route returns `end_time`; `sysinfo`'s `host_role` returns the allocation's `job_id` |
| the endpoint job's Slurm id | to extend, cancel, or tell it apart from the protocol's own jobs in `sacct` | `sysinfo`'s `host_role` |
| that the endpoint restarted | Orbit does not persist sessions: a new endpoint has none of the old handles (upstream `DEPLOYMENT.md`, "Session Persistence") | a fresh `registered as` line; the protocol recovers by re-fetching from `$PROJ`, not by re-attaching (backlog **A14**) |

**The last three are not read by anything in this repo**: `OrbitInterface` asks the broker for the
topology and nothing else. That is a reason to stay on the login node, not a gap to fix first.

---

## 3 · Linode: the broker and agent host

Linode (Akamai Cloud) facts below are from Akamai's pricing page and TechDocs (Cloud Firewall, rDNS,
billing), read 2026-10-08. Prices move; re-check `akamai.com/cloud/pricing` before buying.

**1. Launch.** Ubuntu 24.04 LTS, region **Newark, NJ** (closest to Rutgers, which shortens the path in
backlog **D5**; confirm it offers the plan), with your SSH key. Plan: **Linode 8 GB** (Shared CPU:
4 vCPU, 8 GB, 160 GB disk, **$48/month**, $0.072/hour). The broker alone would run on the smallest
plan; the agent is what needs the room — LangGraph, a process pool, Kuzu and a frontend build. Linode
4 GB (2 vCPU, $24/month) is the floor. The instance comes with a **static public IPv4**; its default
reverse name is `<ip-with-dashes>.ip.linodeusercontent.com`. No DNS is needed: pinned certs skip
hostname checks (`runtime.py`), so the endpoint can dial the bare IP. Record the instance in the
survey table.

**Billing.** Hourly, capped at the monthly price. **A powered-off Linode is billed in full** — only
deleting it stops charges. To pause the deployment, capture an Image of the disk and delete the
Linode; restoring gives a new IP, which means re-pointing the endpoint's `--url` (§4) and re-issuing
the cert (step 4), since its SAN names the old IP.

**2. Cloud Firewall.** Create one (free) and attach it to the Linode. A new firewall's inbound default
is **Drop**; keep it, and add two rules:

| Direction | Protocol / port | Source | Why |
| --- | --- | --- | --- |
| inbound | TCP 22 | where you administer from | ssh, including the browser's `ssh -L` |
| inbound | TCP 8443 | `<campus-block>` | the endpoint on `amarel3` |
| outbound | default Accept | — | apt, git, pip, the Anthropic API |

`<campus-block>` is Rutgers' block (`whois`: "Rutgers, The State University") and covers the NAT pool
the survey saw (`<campus-nat-addr>`); widen it only if the endpoint's address turns out to come from
elsewhere. **An uncommitted Cloud Firewall rule is indistinguishable from a stopped broker** by
error text: it blocked rung 0c once with the broker already active and answering on its own
loopback. Only the *shape* of the failure separates them — a dropped packet hangs until the timeout
(`timeout 8 bash -c 'exec 3<>/dev/tcp/<ip>/8443'` returning after the full 8 s, and an unused port
like 9999 behaving identically), while a reachable host with nothing listening answers with an RST
at once. `amarel_endpoint.sh check` prints "nothing answers" for both.

The allow-list is defence in depth, not the gate — the token is. The agent reaches the
broker over loopback, which the Cloud Firewall never sees. The image enables no host firewall; a UFW
mirror of the same rules is optional (`ufw allow ssh`, `ufw allow from <campus-block> to any port 8443
proto tcp`, `ufw enable` — allow ssh first or you lock yourself out). If the account defines a Default
Firewall for new resources, check it does not already open more than this.

**3. A user, then the install.** The image boots as `root`. Create an unprivileged user for the
broker and the agent, and do everything after this as that user:

```bash
# as root
adduser --disabled-password --gecos "" orbit
install -d -m 700 -o orbit -g orbit /home/orbit/.ssh
install -m 600 -o orbit -g orbit ~/.ssh/authorized_keys /home/orbit/.ssh/
apt update && apt -y upgrade && apt -y install git build-essential
```

```bash
# as orbit
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/KhareLab/PEAT && cd PEAT
# place refcodes/ (radical.asyncflow, rhapsody, flowgentic) — backlog B1
./scripts/setup.sh
git clone https://github.com/radical-cybertools/radical.orbit ~/radical.orbit
git -C ~/radical.orbit checkout c7ede0c        # the revision this document was checked against
uv pip install --python .venv --no-deps -e ~/radical.orbit
uv pip install --python .venv psij-python websockets websocket-client msgpack cloudpickle
./scripts/setup.sh --check
# the skill repository, for protocol_scripts_dir (§5)
```

**The directory names in this document say `rePEAT`, and the deployment's really are.** The repo
moved to `KhareLab/PEAT` after that host was built (`PEAT_MIGRATION.md`), so a fresh clone lands in
`PEAT/` while the running checkouts remain `/root/rePEAT` and `/home/orbit/rePEAT` — named in the
systemd unit in §3 step 5. Renaming them is a server-side operation; the paths below are the record
of what is there now, not a target to edit.

`setup.sh` installs asyncflow, rhapsody and flowgentic and never `radical.orbit`; `pyproject.toml`
does not list it, and `tasks/hpc/orbit.py` imports it lazily, so the gap stays invisible until `hpc`
is switched on (backlog **B1**). `--no-deps` because Orbit requires `rhapsody-py` from PyPI, which
would displace the editable `refcodes/rhapsody`; install the rest of the checkout's `requirements.txt`
by name, then confirm `uv pip show rhapsody-py` still points at `refcodes/`.

**The skill checkout belongs on the VM.** `read_protocol_file` (`graph/nodes/protocol.py`) reads
`protocol_scripts_dir` on the agent's own host and pushes each script to the endpoint in band. The
`protocol_*` *paths* (`proj_root`, `scratch_root`, the conda envs, the databases) are the opposite:
cluster paths, checked only to be absolute (`protocol/site.py`) and never touched on the VM.

**4. Credentials**, on the VM as `orbit`. A self-signed pair is the supported shape, and **hostname
matching is disabled for pinned certs**, so one cert serves the agent dialling `127.0.0.1` and the
endpoint dialling the public IP. Endpoints and clients pin the *cert*; the key never leaves the VM.

```bash
mkdir -p ~/.radical/orbit && chmod 700 ~/.radical/orbit
openssl req -x509 -newkey rsa:4096 -nodes \
    -keyout ~/.radical/orbit/broker_key.pem \
    -out    ~/.radical/orbit/broker_cert.pem \
    -days 365 -subj "/CN=<linode-ip>" \
    -addext "subjectAltName=IP:<linode-ip>,IP:127.0.0.1"
chmod 600 ~/.radical/orbit/broker_key.pem      # the broker refuses to start otherwise

python3 -c "import secrets; print(secrets.token_urlsafe(32))" \
    > ~/.radical/orbit/broker.token
chmod 600 ~/.radical/orbit/broker.token
```

The token is never generated by the software; `~/.radical/orbit` is operator-owned configuration.
The cert expires in 365 days — record the date in the survey table. Rotating the token means
re-copying it to `amarel3` (§4); an endpoint whose reconnect is rejected exits non-zero rather than
waiting.

**5. The broker as a system service.** Root on the VM, which `amarel3` never gives us, means the
broker gets a real unit (adapted from upstream `DEPLOYMENT.md`):

```ini
# /etc/systemd/system/orbit-broker.service
[Unit]
Description=Orbit broker
After=network.target

[Service]
User=orbit
WorkingDirectory=/home/orbit
Environment=RADICAL_ORBIT_LOG_LVL=INFO
ExecStart=/home/orbit/rePEAT/.venv/bin/radical-orbit-broker.py --host 0.0.0.0 --port 8443 -p sysinfo
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

Cert, key and token resolve from `~orbit/.radical/orbit/`. `-p sysinfo` keeps the broker's own
plugin set small: its default adds `staging`, `task_dispatcher`, `federation` and the IRI/SFAPI
connectors, none of which this deployment uses (unverified that a narrower set changes nothing for
routing — check at rung 0b). `sudo systemctl enable --now orbit-broker`, then
`journalctl -u orbit-broker`; the startup banner prints the credential-staging one-liners.

**Readiness**, from the VM. `/topology` is still not a route (backlog **C7**), but the gateway serves
`GET /endpoints`, token-gated like every capability route:

```bash
curl -s --cacert ~/.radical/orbit/broker_cert.pem \
     -H "Authorization: Bearer $(cat ~/.radical/orbit/broker.token)" \
     https://127.0.0.1:8443/endpoints        # expect amarel3, "connected": true, plugins incl. psij
```

`GET /endpoints` does not exist with `--no-gateway`; don't pass it. The client's `rt.topology()`,
which `OrbitInterface.connect()` polls, is the other check.

**6. The agent.** Backend on the VM's loopback, with the §5 environment. Port 8000 is free on a fresh
VM, so `./scripts/dev.sh up` brings up the frontend too — on `amarel3` it could not. The browser
reaches both from the laptop:

```bash
ssh -N -L 5173:127.0.0.1:5173 -L 8000:127.0.0.1:8000 orbit@<linode-ip>
```

And the ordering trap from `CLAUDE.md`: **an endpoint registers a few seconds after it connects**, and
a round planned before then silently takes the heuristic branch. Start the endpoint (§4) before the
backend, or let `dev.sh up` wait for `hpc: true`.

---

## 4 · Amarel: the endpoint

**`scripts/amarel_endpoint.sh` does all of this section** — `install`, `check`, `start` (in tmux),
`status`, `stop`, `logs`, and `selftest`. The commands below are what it runs, for reading or for
doing by hand. It needs only `ORBIT_BROKER_URL`; the endpoint name defaults to `amarel3` and
everything else to the values here.

```bash
./scripts/amarel_endpoint.sh selftest          # no broker needed: a throwaway one on loopback
export ORBIT_BROKER_URL=https://<linode-ip>:8443
./scripts/amarel_endpoint.sh check             # venv, cert, token, PSI/J dir, sbatch, port probe
./scripts/amarel_endpoint.sh start             # refuses if check fails; waits for registration
```

`check` probes the broker's TCP port because a broker that is down or firewalled does not fail the
endpoint's startup — it shows up as a reconnect loop in the log. `selftest` keeps auth on with a
one-off token and a random port, since loopback on a login node is shared (backlog **A9**), checks
that a wrong token gets 401, and removes its processes and files on exit.

**Install Orbit only.** The endpoint needs nothing from this repository — no `refcodes/`, no
`setup.sh`. In a venv of its own (`amarel_endpoint.sh install`):

```bash
uv venv --python 3.12 ~/orbit-venv
uv pip install --python ~/orbit-venv -e /home/<netid>/radical.orbit   # at c7ede0c
```

Here the full dependency set is fine — there is no `refcodes/rhapsody` to displace, and rhapsody does
not load on a login node anyway (§1).

**Credentials**, pulled from the VM. The key stays there.

```bash
mkdir -p ~/.radical/orbit && chmod 700 ~/.radical/orbit
scp orbit@<linode-ip>:.radical/orbit/{broker_cert.pem,broker.token} ~/.radical/orbit/
chmod 600 ~/.radical/orbit/broker.token
```

**Start it**, in `tmux` on `amarel3`:

```bash
export RADICAL_ORBIT_PSIJ_DIR=/scratch/$USER/orbit-psij
~/orbit-venv/bin/radical-orbit-endpoint.py \
    --name amarel3 -p psij,sysinfo \
    --url https://<linode-ip>:8443
```

It logs `registered as 'amarel3'` to `~/.radical/orbit/logs/amarel3.log`, with the plugins that
actually loaded — that line is what `LocalOrbitStack._wait_for_endpoint` greps for. Then check from
the VM with `GET /endpoints` (§3).

`radical-orbit-endpoint-wrapper.sh` is the better entry point when the endpoint is launched from
somewhere that may scrub the interpreter (PSI/J, a batch script): it resolves the venv from its own
location and sets up `PATH` and `PYTHONPATH`. Started by hand in `tmux`, the plain script is enough.

**Keeping it alive.** The endpoint must outlive the login session, and sites do reap long-lived
login-node processes. Use **`tmux`** (installed). A user systemd unit is not an option as things
stand: `systemd --user` runs, but lingering is off (`loginctl show-user` reports `Linger=no`), so the
unit stops at logout, and enabling it needs an administrator. `amarel3` is one of several login nodes
and a tmux session lives only on the node it was started on, so reconnect to `amarel3` by name, not
through a round-robin alias. How to tell it died: `GET /endpoints` no longer lists it, or no fresh
`registered as` line after a restart. An endpoint restart loses its sessions, and with them every
in-flight handle; the protocol recovers by re-fetching from `$PROJ` (backlog **A14**).

---

## 5 · The settings this produces

**On the VM**, for the agent. These are environment-only settings by design: each names a path or
shell the server will run on the endpoint under the site's allocation, so none is remotely writable
(`tests/test_settings.py::NOT_REMOTELY_WRITABLE`).

```bash
export DESIGNAGENT_ORBIT_ENABLED=true
export RADICAL_ORBIT_BROKER_URL=https://127.0.0.1:8443   # the broker, on this VM
export RADICAL_ORBIT_BROKER_CERT=$HOME/.radical/orbit/broker_cert.pem
# RADICAL_ORBIT_BROKER_TOKEN: unset, so ~/.radical/orbit/broker.token is read
export DESIGNAGENT_ORBIT_ENDPOINT=amarel3                # must equal the endpoint's --name
export DESIGNAGENT_ORBIT_PSIJ_EXECUTOR=slurm
export DESIGNAGENT_ORBIT_ACCOUNT=general                 # from sacctmgr; confirm it is the one to charge
export DESIGNAGENT_ORBIT_QUEUE=main
export DESIGNAGENT_ORBIT_JOB_GPUS=0                      # the protocol's specs ask per job
export DESIGNAGENT_ADMIN_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

# cluster paths — on Amarel, never read on this VM
export DESIGNAGENT_PROTOCOL_PROJ_ROOT=/projects/...      # absolute
export DESIGNAGENT_PROTOCOL_SCRATCH_ROOT=/scratch
# ...the conda envs, MPNN path and weights, UniRef DB, AF3 modules and image likewise

# a path on this VM
export DESIGNAGENT_PROTOCOL_SCRIPTS_DIR=$HOME/<skill checkout>/scripts
```

`ACCOUNT=general` is what `sacctmgr show assoc user=$USER` returned at the survey; the
`g_sdk94_1` group membership suggests a lab account may be intended instead.

`DESIGNAGENT_ORBIT_JOB_GPUS=0` matters: `_job_params` otherwise forces one GPU onto *every* spec,
and hhblits and the MPNN jobs need none. Check the lot with
`.venv/bin/python -m designagent --check-config --probe` before spending a queue slot — it masks
secrets and tries each credential.

**On `amarel3`**, for the endpoint: only `RADICAL_ORBIT_PSIJ_DIR` (§4). Cert and token are read from
`~/.radical/orbit/`, and the URL is on the command line.

---

## 6 · Acceptance ladder

Each rung is a command, and each has a result to record. Do not skip to the bottom: rungs 1 and 2
cost nothing in queue time.

**Rungs 1 and 2 run on the VM.** They start the dev stack — a `--no-auth` broker and an endpoint on
loopback (`LocalOrbitStack`). On the VM loopback is private and there is no Slurm, so the host role
is `standalone` and both `rhapsody` and `psij` load. On `amarel3` neither holds: loopback is shared
with every logged-in user (backlog **A16**), and the role is `login`, so rhapsody is skipped and
`test_executable_task_runs_and_returns_output` should fail with "endpoint has no rhapsody plugin"
(backlog **D4**).

| # | Where | Command | Proves | Result |
| --- | --- | --- | --- | --- |
| 0a | VM | §3 steps 1–3, ending `./scripts/setup.sh --check` and `.venv/bin/python -c "import radical.orbit"` | the agent's venv exists and can import Orbit | 2026-10-09: `setup.sh --check` all ok, every refcodes pin matched; offline suite 396 passed, 2 skipped. Orbit is now installed by `setup.sh` itself, from `refcodes/radical.orbit` |
| 0b | VM | `systemctl status orbit-broker`; `GET /endpoints` (§3) returns `{"endpoints": [], …}` | the broker is up, TLS and the token work | 2026-10-09: active. `GET /endpoints` returned `{"endpoints":[{"name":"broker","plugins":["sysinfo"],"connected":true,…}],"total":1}`; the broker lists **itself**, not `[]` as predicted. Wrong token → 401, no token → 401; the public IP answers 200 from the VM. The same authenticated call also answers **from `amarel3`**, so the pinned cert and the token work across the internet and not only on loopback |
| 0-pre | login node | `./scripts/amarel_endpoint.sh selftest` | the endpoint starts with `psij,sysinfo` and registers; auth is on | 2026-10-08, `amarel4`, Orbit 0.8.0 @ `c7ede0c`, Python 3.12: registered in 1 s with `plugins=['psij', 'sysinfo']`; `GET /endpoints` listed it `connected: true`; wrong token → 401. tmux `start`/`status`/`stop` also exercised: after `stop` the broker no longer lists it |
| 0c | amarel3 → VM | start the endpoint (§4); `GET /endpoints` lists `amarel3`, `connected: true`, with `psij` | the endpoint reaches the broker across the internet | 2026-10-09 05:14 UTC: `WebSocket /register [accepted]` from **`<campus-nat-addr>`** (the Rutgers NAT address the survey saw, so the `<campus-block>` rule fits). `GET /endpoints` listed `amarel3`, `connected: true`, plugins `psij, sysinfo`. From the endpoint side the same minute: `amarel_endpoint.sh check` all ok, and `start` logged `registered as 'amarel3' (role=endpoint, plugins=['psij', 'sysinfo'])` at 05:14:33 UTC, the same second as `Starting ORBIT endpoint`. `GET /endpoints` from `amarel3` listed **two** rows, `amarel3` with `plugin_count: 2` and the broker's own. `GET /amarel3/sysinfo/host_role` through the gateway → `{"role": "login", "scheduler": "slurm", "psij_executor": "slurm", "job_id": null, "python_version": "3.12.13"}` — the first capability call to make the whole `amarel3 → broker → amarel3` round trip, and it settles one of §6's four rung-4 questions early. At 05:14:40 the endpoint logged `[psij] Registered session … (owner=designagent)` twice and one unregister: that is rung 3's probe, seen from the other end |
| 1 | VM | `.venv/bin/python -m pytest -q -m live` | the client path against a localhost broker we start ourselves | 2026-10-09: **11 passed, 1 skipped** (the ProteinMPNN test, which is not installed on the VM by design). The first run skipped all 12 with `No module named 'opentelemetry.sdk'`: Orbit's rhapsody plugin calls `start_telemetry`, which needs rhapsody's `telemetry` extra. `setup.sh` now installs and checks it |
| 2 | VM | `DESIGNAGENT_ORBIT_LOCAL=true .venv/bin/python -m pytest -q -m remote` | the remote tier's assertions, rehearsed with no allocation | 2026-10-09: **8 passed** |
| 3 | VM | `.venv/bin/python -m designagent --check-config --probe` | the agent's credentials reach the real broker and the endpoint is visible | 2026-10-09, with `/etc/repeat/backend.env` loaded: `orbit [ok] endpoint amarel3`, `hpc: configured`, exit 0. `llm` absent (no key yet); `protocol_*` cluster paths still unset |
| 4 | VM | `.venv/bin/python -m pytest -q -m remote` | submit → poll → logs → cancel across a real scheduler. **This is the question backlog A1 asked; answering it retired the entry.** | 2026-10-09, run with `DESIGNAGENT_ORBIT_CLIENT_NAME=designagent-rung4`, since the backend holds the default name (backlog **A18**). **4 passed**: endpoint names itself; a PSI/J job runs through Slurm and returns its logs; a queued job is cancelled; the manager routes `hpc` work here. **4 failed, none of them the path itself:** rhapsody is absent on a login node (**D4**, expected); `custom_attributes` with `slurm.requeue: ""` → HTTP 500, a test bug, fixed and re-run green (**A19**); `directory` and the fetch test name paths that exist only on the VM (**A20**). **The path works** |
| 4b | **amarel3** | the same, run *from the endpoint host* | give the two A20 tests paths the jobs can actually see, and exercise `directory` from a test rather than a hand probe | 2026-10-21, `DESIGNAGENT_ORBIT_CLIENT_NAME=designagent-amarel3-pytest`, `DESIGNAGENT_ORBIT_JOB_GPUS=0`, `--basetemp=/scratch/$USER/...`. **7 passed, 1 failed** — the failure is D4's function task, now a skip. `directory` **confirmed by test**: `landed.txt` holding `marker` persisted under `.../proj/conservation/` after the job exited, and the fetch test round-tripped a 900-byte FASTA out of a project directory. Note what this does *not* settle: A20's own defect is the silent `/tmp` fallback, untouched here, and these two tests still fail when pytest runs anywhere but the endpoint host. First run of the tier from Amarel at all, which needed `refcodes/` rebuilt from `setup.sh`'s pins |
| 5 | VM | one real hhblits run | a 12-hour walltime, 0 GPUs, 32 GiB, and a `directory` that persists | |
| 6 | VM | the full protocol spine | everything else | |

Four things to write down from rung 4, because each is the first evidence we will have of it:

- **the Slurm `native_id`** reported back in `handle.meta` — the only link between a PSI/J handle and
  `sacct`, and therefore the only route to the lab notebook's `### Jobs` table now that the skill's
  `slurm.%N.%j.out` filenames are gone;
- **the endpoint's detected batch system**, since
  `batch_system.detect_batch_system().default_custom_attributes()` merges site defaults underneath
  our own `custom_attributes`, and a surprise there surfaces as a scheduler rejection rather than a
  Python error. At `c7ede0c` the Slurm backend does not override it and the base returns `{}`
  (`batch_system.py`), so on Amarel nothing should be merged — confirm with `sysinfo`'s `host_role`
  (expect `role: login`, `scheduler: slurm`, `psij_executor: slurm`) and with a kept submit script;
- **whether `directory` is honoured.** It is forwarded by `to_psij_spec` and set on the PSI/J spec by
  the broker's plugin, and the Slurm template emits `#SBATCH --chdir=`, but no caller in this repo
  sets it and no test exercises it. The protocol's compute jobs depend on it entirely — and Slurm
  fails a job whose `--chdir` does not exist before anything runs, so `$PROJ` must be created first;
- **how long a known-size output takes to come back.** Job stdout and in-band staged files now
  travel `amarel3` → Linode over the internet (backlog **D5**).

**What rung 4 answered (2026-10-09):**

- **`native_id` comes back.** A probe job reported `native_id: "62380976"`, which matched the
  `SLURM_JOB_ID` it printed. It ran on `hal0140`.
- **Batch system:** `host_role` returned `role: login`, `scheduler: slurm`, `psij_executor: slurm`
  (rung 0c, from the endpoint side). Nothing site-specific surfaced as a rejection. The only 500 was
  our own malformed attribute (**A19**). No kept submit script has been read yet.
- **`directory` is honoured when it exists, and silently replaced when it does not.**
  - `directory=/scratch/<netid>` printed `/scratch/<netid>` (job `62380976`).
  - `directory=/scratch/<netid>/no-such-dir-rung4` printed **`/tmp`** and still reported **DONE,
    exit 0** (job `62380977`).
  - So the claim above, "Slurm fails a job whose `--chdir` does not exist", is **wrong on Amarel**:
    it falls back to `/tmp`. `$PROJ` must exist before a compute stage, and a stage cannot rely on
    Slurm to notice when it does not (backlog **A20**).
- **Output transfer time** is still unmeasured (D5): rung 4's jobs printed a few lines each.

**What rung 4b added (2026-10-21), running the tier from `amarel3` itself:**

- **`directory` now has a test behind it, not just a probe, and Slurm's own accounting agrees.**
  `test_a_jobs_working_directory_is_honoured` passed against real Slurm: the job printed its `$PWD`
  as the requested directory and `landed.txt` was still there afterwards. Independently,
  `sacct -j <id> --format=WorkDir` reports
  `/scratch/<netid>/peat-remote-tmp3/test_a_jobs_working_directory_0/proj/conservation` for that job
  (`62801273`) and `/cache/home/<netid>/PEAT` — the endpoint's own cwd — for every job that set no
  `directory`. That is the `--chdir` question answered from the scheduler's side rather than from
  the job's stdout, and it is the check to repeat for a protocol stage.
  `test_a_file_comes_back_out_of_a_project_directory` passed too, so `fetch_job_spec`'s
  `outputs: ["**"]` and the client-side reader agree over a real broker — the first end-to-end
  exercise of the protocol's fetch shape.
- **Five Slurm jobs for six job-submitting tests, which is correct.** `sacct` lists
  `62801247`, `62801261` (the cancelled one, never started), `62801262`, `62801273`, `62801276` —
  and nothing for the fetch test, because a transfer spec runs on the `local` PSI/J executor, on the
  endpoint host, with no queue slot (§2.2). So the absence in `sacct` is itself the evidence that
  the two job modes are routed differently.
- **No GPU was requested.** `ReqTRES` is `billing=1,cpu=1,mem=1G,node=1` on every job, with no
  `gres`, confirming `DESIGNAGENT_ORBIT_JOB_GPUS=0` reached `sbatch` (A25). Note `mem=1G` and
  `TimelimitRaw=10`: PSI/J rendered defaults, not the figures in `protocol/specs.py`, because these
  specs set neither.
- **Why it passed now, and what that does not mean.** Both tests build their paths with `tmp_path`,
  so they need a filesystem the *job* can see. Running pytest on the endpoint host with
  `--basetemp` on `/scratch` (shared with the compute nodes; `/tmp` is node-local) satisfies that.
  This is a run recipe, not the fix: from the VM both still fail, and **A20's proposal of a remote
  base directory with job-side verification stays open**. A20's primary defect — a missing
  `--chdir` falling back to `/tmp` with exit 0 — is untouched and needs the guard line in
  `protocol/specs.py`.
- **A GPU is requested by default, and a CPU partition rejects it.** Three tests failed on the first
  attempt with `sbatch: error: Requested GRES option unsupported by configured SelectType plugin`,
  an HTTP 500 from the psij plugin. Cause is ours: `job_params` injects
  `resources["gpus"] = orbit_job_gpus`, default **1**, and `queue=main` is CPU-only. Backlog
  **A25**. Nothing about the endpoint or the broker was involved.
- **Still unmeasured:** D5's transfer time. These jobs printed a few lines each, and the one
  sizeable payload (the fetch test's 900-byte FASTA) is far too small to time.
- **No kept submit script has been read yet** — `$RADICAL_ORBIT_PSIJ_KEEP_FILES=1` would settle what
  `--mem`, `--chdir` and `--gres` PSI/J actually rendered, and would have named the GRES cause in
  seconds.
