# Moving this rewrite into KhareLab/PEAT

This repo is a from-scratch rewrite of [`KhareLab/PEAT`](https://github.com/KhareLab/PEAT) and shares
no history with it. The lab keeps the repo and its identity — same URL, same issue tracker, same
`main` as the default branch — so the rewrite replaces `main` while the earlier work survives as
`archive/*` branches until someone reviews them by hand.

This is a migration record as much as a procedure: fill in what each rung of the ladder at the bottom
actually returned, so the next person reads results rather than intentions.

**Status as of 2026-10-09: the migration is done.** `KhareLab/PEAT`'s `main` is this rewrite
(`d02ee19`), still the default branch; the earlier Streamlit work is on five `archive/*` branches with
its originals left beside them, plus the tag `peat-v1-streamlit`. A fresh clone of the new `main`
builds and passes its suite, and the published tree reads PEAT. Six classes of identifier were
redacted first.

**Three rungs are open, and all three need a person rather than a command:**

- **Rung 1 was skipped.** `Runsey/PEAT`'s owner was not told before the force-push. Their fork still
  points at `d2a01f93` and now shows a fully divergent `main`. Nothing is lost — `archive/main` and the
  tag both hold that commit — but the notice is owed late instead of early.
- **Rung 10**, triaging the five open issues, is deliberately left: they are other people's issues on
  a lab repo, and closing or re-filing them is a judgement about the lab's work, not a migration step.
- **B11** still wants an org owner: branch protection on the new `main`, and deleting the four
  original branches once the hand review of the archives is done.

## 1. The shape, and why it is the only one

| Fact | Where it came from |
| --- | --- |
| `KhareLab/PEAT` is **public**, 208 KB, default branch `main`, **unprotected** | `gh repo view`, 2026-10-09 |
| Our token has `viewerPermission: WRITE` — **`admin: false`, `maintain: false`**, only push/pull/triage | `gh api repos/KhareLab/PEAT -q .permissions` |
| Branches: `main` (`d2a01f93`), `ceiron-updates`, `client-side-env`, `fix/rag_nodoi`, `skill_loader`. No tags | `gh api .../branches` |
| 5 open issues, 1 fork (`Runsey/PEAT`), 1 merged PR | `gh issue list`, `gh api .../forks` |
| PEAT's `main` is a Streamlit app — `app.py`, `ui.py`, `requirements.txt`, `.github/workflows`, `.agents/` | `gh api .../git/trees/main` |
| PEAT = "Protein Engineering Agent Toolkit" | its own README |
| This repo: 43 commits, 140 tracked files, all four side branches already merged into `main` | `git branch --merged` |

**Without admin we cannot change the default branch, rename `main`, delete `main`, or set branch
protection.** So every design that pushes to a new branch and promotes it is blocked. What *is*
available is pushing to `main`, which is already the default — so the only workable shape is:

> archive the old refs under `archive/*`, verify them from the API, then force-push this history onto
> `main` with a lease pinned to the recorded SHA.

Nothing about the default-branch setting is touched, so no admin is needed for the migration itself.
What still needs an org owner is listed as backlog **B11**.

The two histories are unrelated, so this is a replacement, not a merge. That is what "shares no
history" asks for: no `--allow-unrelated-histories` graft, no synthetic merge commit.

## 2. Traps

Each of these cost real investigation. None is recoverable by reading the code.

**The live name was "Protein Design Agent", not "rePEAT".** The sharpest one. A session that greps for
`repeat` finds nothing user-visible and concludes there is nothing to rename. Three names coexisted:
the displayed title (7 places), `designagent`/`DESIGNAGENT_` (~480 occurrences in 58 files), and
`rePEAT`/`repeat` (34, all of them plan docs, the git remote, one test literal and one generated
path). There was **no shared title constant** — four independent frontend literals plus the FastAPI
title. The displayed name is now `PEAT`; the package and env prefix deliberately still say
`designagent` (backlog **B12**).

**`git grep` on the committed deck binaries lies in both directions.** A dotted-quad regex reported
`slides/designagent-codewalk.pptx` as matching. Checked properly — unzipping every pptx part and
inflating the PDF's zlib streams — **neither binary contains the Linode address**, and no slides
*source* file does either. That was a false positive from compressed bytes. The converse error is just
as available: a sensitive scan using `git grep -I` skips binaries entirely. **No deck rebuild was
needed for redaction.** Recorded as backlog **M3**.

**A dotted-quad regex does not find an address in reverse-DNS form.** The Linode's name
`<linode-rdns>` is the address with dashes, and it is the **live UI URL**
with a Let's Encrypt cert. Five occurrences, invisible to the pattern that found the other eight.
Any address scan has to look for both spellings.

**Writing up a redaction re-introduces the value.** This document and the skill that scans for
addresses both quoted the real reverse-DNS name while explaining why it is easy to miss, and the
skill repository is public too. Caught before either was pushed, by a review of the commit rather
than by the scan — the working tree was clean, so a scan of `HEAD` had nothing to say about prose
that had just been committed. Use RFC 5737 documentation ranges (`203.0.113.0/24`,
`198.51.100.0/24`) in every example; they are excluded by the scanner's own filter, so an example
cannot be mistaken for a finding either.

**Identity scans look for *you*, so a colleague's identifier hides in plain sight.** A netid belonging
to someone else sat in `graph/nodes/protocol.py` as the example value in the intake form **printed on
screen**, and in 28 test fixtures. An autodetecting scanner keyed on `git config user.email` and
`$USER` never sees it. Pass collaborators' usernames explicitly, and read example and placeholder
values as carefully as real ones.

**`drawadiagram/rePEAT` was already public** (0 forks) when this was written. So redaction is
forward-looking hygiene for the repo that will be read for years, not containment — the values were
already world-readable. The decision follows: **the three commits carrying the Linode address
(`93067d7`, `5e37a8e`, `f9c5825`) are deliberately not rewritten.** The honest remediation for an
exposed host address is a firewall rule or a new address, not a git edit, and a rewrite would churn
every SHA from `93067d7` forward for no secrecy gain. If that is ever revisited, the tool is `git
filter-repo --replace-text`, and the moment it is cheapest is before the first push to PEAT.

**The plan docs name live systemd units.** `LINODE_DEPLOY.md` and `AMAREL_ENDPOINT.md` cite
`repeat-backend.service`, `/etc/repeat/backend.env`, `/srv/repeat/www` and `/home/orbit/rePEAT` as
running on the Linode. **Editing the doc does not rename the unit.** A name-cleanup pass that "fixes"
these makes the deployment record wrong and the next deploy session confused. They are left exactly
as they are; renaming them is a server-side operation first, a doc edit second.

**The fork does not follow a force-push.** `Runsey/PEAT` points at the old history and will show a
fully divergent `main`. Tell its owner *before* §4 runs, not after. The 5 open issues survive the push
untouched, but all of them describe the old Streamlit app, so they need triage rather than
inheritance.

**One clone URL is load-bearing.** `AMAREL_ENDPOINT.md`'s install step was the only `git clone` in
tracked files; it pointed at `drawadiagram/rePEAT` and would have sent the next endpoint build to the
wrong repo.

## 3. What was redacted, and the placeholders

`KhareLab/PEAT` is a public lab repo, so account and host identifiers were replaced before the push.
Counts are from `git grep` on 2026-10-09. Backlog **A24** carries the reasoning.

| Class | Placeholder | Count | Files |
| --- | --- | --- | --- |
| Linode address, dotted | `<linode-ip>` | 8 | `CLAUDE.md`, `AMAREL_ENDPOINT.md`, `LINODE_DEPLOY.md` |
| Linode address, reverse DNS | `<linode-rdns>` | 5 | `AMAREL_ENDPOINT.md`, `LINODE_DEPLOY.md` |
| Linode IPv6 block | `<linode-ip6>` | 2 | `AMAREL_ENDPOINT.md`, `LINODE_DEPLOY.md` |
| A collaborator's netid | `abc123` | 30 | `graph/nodes/protocol.py`, `.env.example`, 8 × `tests/test_protocol_*.py` |
| The operator's netid | `<netid>` | 9 | `AMAREL_ENDPOINT.md`, `LINODE_DEPLOY.md`, `BACKLOG.md` |
| Allocation paths | `/projects/f_proj00_1` | 18 | `.env.example`, 3 × `tests/test_protocol_*.py` |
| Campus NAT address, reverse name, block | `<campus-nat-addr>`, `<campus-nat-rdns>`, `<campus-block>` | 9 | `AMAREL_ENDPOINT.md`, `LINODE_DEPLOY.md` |

The key to every placeholder is in `AMAREL_ENDPOINT.md`'s **Placeholders** section, so a deployment
session can substitute the real values without reading this file. Four notes on the choices:

- **`<linode-ip>` was already the convention** — it was in use in 11 places across
  `AMAREL_ENDPOINT.md` and `scripts/amarel_endpoint.sh`, and `AMAREL_ENDPOINT.md`'s address table is
  the row that defines it. The eight literals were stragglers, not a new scheme.
- **The replacement netid has to validate.** `NETID_RE` in `protocol/inputs.py` requires 2–16 letters
  and digits, because the value becomes a `/scratch/<netid>` path component, and
  `tests/test_protocol_inputs.py` asserts on trimming that exact string. `abc123` satisfies it.
- **The allocation examples are only ever "for shape"** — `.env.example` says so itself — so a generic
  project id serves identically. `tests/test_protocol_specs.py` asserts a rendered path containing it,
  so fixture and assertion moved together.
- **Allocation and campaign ids keep a valid shape instead of becoming `<angle>` placeholders**,
  because they appear inside real paths in test fixtures and an unsubstitutable token there would
  make the fixtures lie about what the renderer produces. The address placeholders have no such
  constraint: nothing executes them.

Redaction applied inside `plans/*.md` too. Historical references to the project's *name* are fine and
were left alone; account and host data is a different thing, and the surrounding record of what
happened is intact either way.

## 4. The ladder

Run from a session with both repos available. Rungs are in order; each is cheap to verify and the
destructive one is last.

| # | Step | Expected | Result |
| --- | --- | --- | --- |
| 0 | `gh api repos/KhareLab/PEAT -q .permissions` | `push: true`; `admin` may still be false | 2026-10-09: `{admin: false, maintain: false, pull: true, push: true, triage: true}` — as designed for; the ladder needs none of the three |
| 0b | `gh api repos/KhareLab/PEAT/branches` | the five branches, `main` at `d2a01f93` | 2026-10-09: all five, `main` at `d2a01f93`, `pushed_at` 2026-07-30. The lease SHA in rung 6 is still current |
| 0c | **`git log --oneline -1 main` in this repo** | the redaction and rename commit, **not** `5161159` | 2026-10-09: `d02ee19`. `peat-migration-prep` fast-forwarded into `main`; a scan of the merged tree is 0 findings, exit 0 |
| 1 | Tell `Runsey/PEAT`'s owner the force-push is coming | acknowledged | **NOT DONE.** Rung 6 went ahead first, so this is notice *after* the fact. Owed to `Runsey/PEAT` — nothing is lost, since the content is recoverable from `archive/main`, but the courtesy was skipped |
| 2 | Archive the five refs (below) | five `archive/*` branches created | 2026-10-09: all five created from a scratch clone of PEAT. Originals left in place, so the branch list is now ten |
| 3 | `gh api repos/KhareLab/PEAT/branches` again | ten branches; **`archive/main` == `d2a01f93`** | 2026-10-09: ten branches. **`archive/main` = `d2a01f939d99c16dec89303784d1ff42a8d056cf`** — gate passed; the other four match their originals |
| 4 | `git log archive/main` on a fresh clone | the Streamlit history, ending at `d2a01f93` | 2026-10-09: **44 commits**, tip `d2a01f93` "remove too-general claude skills", back through the LangGraph refactor and the Globus work. The Streamlit history is intact |
| 5 | Push the tag `peat-v1-streamlit` | tag resolves to `d2a01f93` | 2026-10-09: tag object `25183a35` dereferences to `d2a01f93`, message "PEAT before the rewrite: the Streamlit app" |
| 6 | Force-push `main` with the lease | accepted; PEAT's `main` is this tree | 2026-10-09: **accepted** — `+ d2a01f9...d02ee19 main -> main (forced update)`, lease pinned to the full SHA. `default_branch` is still `main`, untouched |
| 7 | Fresh clone of `main`, `./scripts/setup.sh && --check` | both pass | 2026-10-09: a bare fresh clone fails `--check` by name (`.venv is missing`); with `refcodes/` copied in per **B1** the full `setup.sh` passes — `designagent`, `radical.orbit`, `opentelemetry.sdk` import, rhapsody resolves to the checkout, the graph builds |
| 8 | `.venv/bin/python -m pytest -q` in that clone | 417 passed, no network | 2026-10-09: **415 passed, 2 skipped**, 24 deselected, no network. Both skips name their reason (`the skill checkout is not beside this repo`), which is environmental to a scratch clone. 417 is the count with that checkout present |
| 9 | `./scripts/dev.sh up --no-mpnn`, read the browser | tab, header, login card and empty state say PEAT | 2026-10-09, mechanically rather than by eye: built `dist/index.html` is `<title>PEAT</title>`, the bundle has the three frontend literals and **zero** of the old name, 45 jsdom tests pass, and the backend from the published tree answers `/openapi.json` with `info.title = PEAT`. The visual read of all four surfaces is still the by-hand tier (`frontend/e2e/BROWSER_TESTS.md`) |
| 10 | Triage the 5 open issues | each closed or re-filed against the new code | **Left for a person.** Five issues, all describing the old Streamlit app; closing or re-filing them is a judgement about the lab's work |
| 11 | `git push origin main` to rePEAT | its HEAD is the redacted tree, not `5161159` | 2026-10-09: done *early*, as a backup before anything destructive — `5161159..d02ee19 main -> main`. rePEAT's HEAD is the redacted tree |
| 12 | `gh repo archive drawadiagram/rePEAT` | read-only; **only after rung 9** (§5) | 2026-10-09: `archived=true`, still public, HEAD `13a51c8` — the redacted tree *and* this record. Done last, after rung 9, so the fallback existed until it was not needed |
| 13 | `git remote set-url origin …/KhareLab/PEAT.git` | `git push` from this checkout reaches PEAT | 2026-10-09: `origin` → `KhareLab/PEAT.git`, `main` tracks `origin/main` and is in sync. The `peat` remote added for rung 6 was removed again |

Rung 2, from a clean scratch clone — **never from a working tree**, so a stray local ref cannot be
pushed by accident:

```bash
git clone https://github.com/KhareLab/PEAT.git <scratch>/peat-archive
cd <scratch>/peat-archive
for b in main ceiron-updates client-side-env fix/rag_nodoi skill_loader; do
  git push origin "refs/remotes/origin/$b:refs/heads/archive/$b"
done
git tag -a peat-v1-streamlit d2a01f93 -m "PEAT before the rewrite: the Streamlit app"
git push origin peat-v1-streamlit
```

The originals are **left in place**. Rung 2 is therefore wholly non-destructive and independently
checkable before rung 6 touches anything, and the branches stay where a reviewer expects to find them
until the hand review happens. Deleting them later needs an admin (**B11**). The tag is a second,
independent pointer that reads as frozen rather than as an active line of work.

**Rung 0c is not a formality.** Rung 6 pushes `main:main`, and the redaction and the rename landed on
a branch (`peat-migration-prep`), so until that branch is merged, local `main` is still `5161159` —
the tree with the host address, both netids and "Protein Design Agent" in it. Running the ladder
before the merge would publish, to a public lab repo, precisely what §3 was for. Merge first, then
re-read `git log -1 main` and confirm the scan is still clean:

```bash
python3 -I ~/.claude/skills/repo-sensitive-scan/scripts/scan_sensitive.py -C . --upstream none
```

Rung 6, from this repo:

```bash
git remote add peat https://github.com/KhareLab/PEAT.git
git push --force-with-lease=main:d2a01f93 peat main:main
```

`--force-with-lease` pinned to the SHA recorded at rung 0b is the rail: if anyone pushed to PEAT's
`main` in between, the push is **refused** rather than silently overwriting it. A bare `--force` has
no such check and must not be substituted.

## 5. After

**`drawadiagram/rePEAT` becomes an archive** (decided 2026-10-09). Its tracker needs nothing: 0 open
issues and 2 merged PRs, so there is nothing to migrate. We hold `admin` on it, so
`gh repo archive drawadiagram/rePEAT` is available. Three things follow from the order, and the first
two are easy to get wrong:

- **Archive it last, after rung 9 passes.** Until PEAT's `main` is verified, rePEAT is the only live
  copy of this work, and an archived repo is read-only — so archiving first removes the fallback and
  the ability to push a fix.
- **Push the prep commit to rePEAT before archiving.** Its `main` is still `5161159`, the tree with
  the host address and both netids in it. Archiving as-is freezes *that* as the permanent public face
  of the repo. One push makes the archive's HEAD the redacted tree. History still carries the values
  either way, which is the accepted decision in **A24** — this is about what a visitor sees first, not
  about recall. Its two stale branches (`deck-three-dimensions`, `linode-deploy-plan`) freeze too;
  that is fine for an archive.
- **Retarget `origin` here to PEAT.** Not optional once rePEAT is read-only: every later push from
  this checkout would fail against an archived remote. (Taken as the reading of "no need to change its
  tracker" — the *issue tracker* needs nothing; the git remote still has to point somewhere live.)

- **Ask an org owner** for the items in **B11**: branch protection on the new `main`, and deletion of
  the five originals once the hand review is done.
- The deferred `designagent` → `peat` package and env-prefix rename is **B12**. It is not cosmetic
  leftovers: `orbit_client_name` is read by the live Linode environment and asserted in
  `tests/test_auth.py`, and the prefix is what every deployed `backend.env` is written against.
