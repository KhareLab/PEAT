"""The enzyme-redesign protocol: one stage per turn, over many turns.

A campaign takes days. HHblits is twenty minutes, AlphaFold3 is an hour of GPU
per design, and the skill names three points where the run must **stop and wait
for the user**: the FoldSeek upload, the catalytic residues, and the selected
designs. None of that fits in a chat turn, so the turn boundary *is* the
mechanism: a stage that needs an answer sets `protocol.awaiting` and returns,
and the next message answers it.

Two tables, no if-chain. `STAGES` maps a stage name to what it runs; `ANSWERS`
maps an `awaiting` key to how the user's reply is read. Each entry is
independently testable, which is the point -- the alternative grows into one
function nobody can exercise a branch of.

**Everything site-bound goes through `hpc`.** The compute specs set `directory`
to a `$PROJ` subdirectory and declare no staging, so their files persist for the
next stage; the transfer specs declare `inputs`/`outputs` and run on the `local`
PSI/J executor. `protocol/specs.py` has the whole convention. With no endpoint
attached, `TaskManager.interface_for` would quietly route a job spec to the
local interface, which has no such task -- so every submitting stage checks
`hpc_available` first and refuses by name.

**Progress is recovered from the filesystem, never from a handle.** Handles and
pollers are in-memory (`tasks/hpc/base.py`), so a backend restart loses every
one of them while the jobs keep running. `af3_collect` therefore looks for no
handle at all: it lists the output tree and reads the small summary files, which
makes it idempotent and re-runnable on any later turn. The data outlives the
handle, so recovery is a re-fetch rather than a re-attach.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from langgraph.types import Command

from ...protocol import af3 as af3_mod
from ...protocol import notebook as nb
from ...protocol import notes, specs
from ...protocol.fixed_positions import add_fixed_positions, designable_positions
from ...protocol.inputs import InvalidInput, ProtocolInputs, tag_for_method, validate_domains
from ...protocol.site import SiteLayout
from ...protocol.trim import trim_signal_peptide
from ...tasks.base import TaskSpec
from ...tasks.jobspec import batch_timeout, job_params
from ...tools.proteinmpnn import variants_from_mpnn_fasta
from ..approval import permit_scope, request_approval, submission_permission
from ..deps import Deps, status
from ..state import DesignState, last_user_text

log = logging.getLogger(__name__)

#: The notebook artifact's id is per session and fixed, so appending to it
#: replaces the file in place and `merge_artifacts` keeps one reference.
NOTEBOOK_ID = "protocol-notebook"

#: Which level's pair the analysis uses when the user does not say. The skill's
#: default, and it warns that each extra set is `N_SELECT` more AlphaFold3 jobs.
DEFAULT_ANALYSIS_LEVEL = 50

#: The conservation levels a campaign runs unless told otherwise.
DEFAULT_LEVELS = (30, 50, 70)


@dataclass
class Outcome:
    """What a stage did, and what the user is told about it."""

    reply: str
    source: str
    stage: str = ""  # the stage to run next; "" keeps the current one
    awaiting: str = ""  # non-empty: stop here until the user answers
    patch: dict[str, Any] = field(default_factory=dict)
    notebook: str = ""  # a block to append to NOTEBOOK.md
    warnings: tuple[str, ...] = ()


@dataclass
class Ctx:
    """What every stage needs, assembled once by the node body."""

    deps: Deps
    state: DesignState
    pstate: dict[str, Any]
    site: SiteLayout
    session_id: str
    campaign_id: str
    text: str

    @property
    def inputs(self) -> ProtocolInputs:
        """The campaign's validated inputs, rebuilt from the checkpoint.

        Rebuilt rather than carried: `ProtocolInputs` is frozen and validating
        is cheap, and going through `parse` again means a value that somehow
        reached the checkpoint unvalidated cannot reach a job spec.
        """
        p = self.pstate
        return ProtocolInputs.parse(
            name=p.get("name", ""),
            uniprot=p.get("uniprot", ""),
            domains=p.get("domains", ""),
            netid=p.get("netid", ""),
            method=p.get("method", ""),
            cat_res=p.get("cat_res") or (),
            levels=p.get("levels") or DEFAULT_LEVELS,
        )

    @property
    def model_stem(self) -> str:
        return self.pstate.get("model_stem", "")

    async def jobs(self, items: list[tuple[str, dict]]) -> tuple[list[dict], list[str]]:
        """Submit protocol job specs through `hpc`, wait, and return records.

        Also returns the scheduler ids, which only the handles carry: a record
        has no `native_id`, and it is the one link between a submission here and
        a row in `sacct` -- the lab notebook's job table has no other source now
        that the skill's `slurm.%N.%j.out` filenames are gone.
        """
        task_specs = [
            TaskSpec(
                name=label,
                params={
                    **job_params(self.deps.settings, spec),
                    "_interface": "hpc",
                },
                kind="job",
                label=label,
            )
            for label, spec in items
        ]
        handles = await self.deps.tasks.submit_many(
            task_specs, session_id=self.session_id, campaign_id=self.campaign_id
        )
        native = [
            str(h.meta.get("native_id")) for h in handles if h.meta.get("native_id")
        ]
        records = await self.deps.tasks.gather(
            handles,
            session_id=self.session_id,
            campaign_id=self.campaign_id,
            timeout=batch_timeout(self.deps.settings, task_specs),
        )
        return records, native


# --- reading what came back ----------------------------------------------


def artifacts_of(records: list[dict]) -> dict[str, str]:
    """Every staged file across a batch, as `{returned name: blob path}`.

    `TaskManager._materialize_artifacts` has already turned the bytes into blob
    paths, so nothing bulky is in a record by the time a stage sees it.
    """
    out: dict[str, str] = {}
    for record in records:
        result = record.get("result")
        if not isinstance(result, dict):
            continue
        for name, path in (result.get("artifacts") or {}).items():
            if isinstance(path, str):
                out[name] = path
    return out


def read_artifact(paths: dict[str, str], name: str) -> str:
    """One staged file's text, by the name it came back under."""
    path = paths.get(name)
    if not path:
        raise InvalidInput(f"the job did not return {name}")
    return Path(path).read_text(errors="replace")


def errors_of(records: list[dict]) -> list[str]:
    return [str(r.get("error") or "failed") for r in records if not r.get("ok")]


# --- stages ---------------------------------------------------------------


async def stage_intake(ctx: Ctx) -> Outcome:
    """Collect the campaign's inputs, then ask the one question with no default."""
    parsed = parse_intake(ctx.text)
    missing = [
        key for key in ("name", "uniprot", "domains", "netid") if not parsed.get(key)
    ]
    if missing:
        return Outcome(
            reply=(
                "I need the campaign's inputs before anything else. Reply with:\n\n"
                "```\n"
                "name=AlyFRB\n"
                "uniprot=A0A173MSR7\n"
                "domains=1-IDR-10-11-FN3-117-118-L-143-144-CD-479-480-L-493-494-CD-773-774-IDR-785\n"
                "netid=abc123\n"
                "```\n\n"
                f"Missing: {', '.join(missing)}. The domain string must be "
                "contiguous `start-label-end` triples covering the whole chain, "
                "with at least one `CD` segment — that is what ProteinMPNN is "
                "allowed to redesign."
            ),
            source="protocol:stage_intake:ask_inputs",
            awaiting="inputs",
        )

    # Validated here so a bad domain string is refused before a notebook exists.
    validate_domains(parsed["domains"])
    return Outcome(
        reply=(
            f"Target **{parsed['name']}** ({parsed['uniprot']}). Which conservation "
            "method should feed ProteinMPNN?\n\n"
            "- **cpos** — the top-N% most conserved positions by top-residue "
            "frequency, whether or not the query carries that residue.\n"
            "- **conservation_liu** — a position is fixed only if the query "
            "residue is the plurality residue in its alignment column *and* its "
            "frequency is at or above 30/50/70%.\n\n"
            "Both are computed; the choice decides which set is handed to the "
            "redesign. There is no default — reply `cpos` or `conservation_liu`."
        ),
        source="protocol:stage_intake:ask_method",
        awaiting="method",
        patch=parsed,
    )


async def stage_structure(ctx: Ctx) -> Outcome:
    """Step 1-2: fetch the AFDB model, strip the signal peptide, install `$PROJ`."""
    refusal = unavailable(ctx)
    if refusal:
        return refusal

    uniprot = ctx.pstate["uniprot"]
    status(f"Downloading the AlphaFold model for {uniprot}…", node="protocol")
    records = await ctx.deps.tasks.run_many(
        [
            TaskSpec(
                name="afdb_structure",
                params={"uniprot_id": uniprot, "fmt": "pdb"},
                label="AFDB model",
            )
        ],
        session_id=ctx.session_id,
        campaign_id=ctx.campaign_id,
    )
    result = records[0].get("result") if records else None
    if not isinstance(result, dict) or result.get("error") or not result.get("text"):
        reason = (result or {}).get("error") or "the download failed"
        return Outcome(
            reply=f"I could not get an AlphaFold model for {uniprot}: {reason}",
            source="protocol:stage_structure:no_model",
        )

    staged = ProtocolInputs.parse(
        name=ctx.pstate["name"],
        uniprot=uniprot,
        domains=ctx.pstate["domains"],
        netid=ctx.pstate["netid"],
        method=ctx.pstate["method"],
    )
    trimmed = trim_signal_peptide(result["text"], staged)
    stem = trimmed.stem
    blob = ctx.deps.history.write_blob(trimmed.pdb, suffix=".pdb", prefix=stem)

    files = protocol_files(ctx, trimmed.pdb, stem)
    status("Creating the project directory on the cluster…", node="protocol")
    records, native = await ctx.jobs(
        [("protocol-install", specs.install_job_spec(ctx.site, trimmed.inputs, files))]
    )
    failures = errors_of(records)
    if failures:
        return Outcome(
            reply=(
                "The project directory could not be created on the cluster, so "
                f"nothing else can run: {notes.failure_note('install', failures)}"
            ),
            source="protocol:stage_structure:install_failed",
        )

    plddt = trimmed.plddt or {}
    offset_note = (
        f"Offset {trimmed.offset} (SP 1-{trimmed.offset}). "
        + "; ".join(str(shift) for shift in trimmed.mapping)
        if trimmed.trimmed
        else ""
    )
    line = (
        f"Loaded {result['stem']} ({result['format']}, model v{result.get('version', '?')}), "
        f"{trimmed.n_residues} residues"
        + (
            f", signal peptide 1-{trimmed.offset} stripped and renumbered from 1"
            if trimmed.trimmed
            else ", no signal peptide to strip"
        )
        + f". pLDDT mean {plddt.get('plddt', '?')}, min {plddt.get('plddt_min', '?')}."
    )

    return Outcome(
        reply=(
            f"{line}\n\nNext I need the catalytic residues, in the trimmed "
            f"numbering (1-{trimmed.inputs.chain_length}). The protocol will not "
            "guess them: upload the model to https://search.foldseek.com against "
            "PDB100 and read them off the top hit's paper, or give me the paper. "
            "Reply with the positions, e.g. `310,364`."
        ),
        source="protocol:stage_structure:summary_line",
        awaiting="cat_res",
        patch={
            "model_stem": stem,
            "structure_path": blob,
            "domains": trimmed.inputs.domains,
            "offset": trimmed.offset,
            "installed": True,
        },
        notebook=notes.entry(
            "1-2",
            "Structure and domains",
            goal="Get the AFDB model, strip the signal peptide, renumber from 1.",
            commands=[f"curl -o {result['stem']}.pdb {result['url']}"],
            result=line,
            files=[f"{stem}.pdb", f"{ctx.site.proj(trimmed.inputs)}/"],
            notes=offset_note or "No signal peptide in the domain string.",
            jobs=[],
        ),
    )


async def stage_conservation(ctx: Ctx) -> Outcome:
    """Step 6-7: HHblits, then the fixed-position sets the redesign needs."""
    refusal = unavailable(ctx)
    if refusal:
        return refusal
    inputs = ctx.inputs
    stem = ctx.model_stem

    status("Running the HHblits conservation search (15-20 minutes)…", node="protocol")
    records, native = await ctx.jobs(
        [
            (
                "hhblits",
                specs.hhblits_job_spec(ctx.site, inputs, model_stem=stem),
            )
        ]
    )
    failures = errors_of(records)
    if failures:
        return Outcome(
            reply=f"The conservation search failed: {notes.failure_note('hhblits', failures)}",
            source="protocol:stage_conservation:failed",
            patch={"native_ids": native},
        )

    status("Collecting the conserved-position sets…", node="protocol")
    output = f"{ctx.site.sub(inputs, 'conservation')}/output"
    fetch, _ = await ctx.jobs(
        [
            (
                "conservation-fetch",
                specs.fetch_job_spec(
                    [
                        ("conservation", f"{output}/*_cpos_{inputs.tag}*.jsonl"),
                        ("conservation", f"{output}/*_interface_residues.csv"),
                    ],
                    name="conservation-fetch",
                ),
            )
        ]
    )
    paths = artifacts_of(fetch)
    if not paths:
        return Outcome(
            reply=(
                "The conservation search finished but returned no position sets. "
                f"Look in `{output}` on the cluster."
            ),
            source="protocol:stage_conservation:no_output",
            patch={"native_ids": native},
        )

    # Build each level's ProteinMPNN fixed-position file here: pure python, and
    # it is the one computation that decides which residues survive a redesign.
    pushes: dict[str, str] = {}
    counts: list[str] = []
    for level in inputs.levels:
        name = specs.conservation_jsonl_name(inputs, level, stem)
        candidates = [key for key in paths if key.endswith(name)]
        if not candidates:
            continue
        text = read_artifact(paths, candidates[0])
        pushes[specs.fixed_positions_name(inputs, level)] = add_fixed_positions(
            text, inputs.segments
        )
        designable = designable_positions(inputs.segments, _positions(text))
        counts.append(f"cpos{inputs.tag}{level}: {len(designable)} designable")

    if not pushes:
        return Outcome(
            reply=(
                "The conservation search returned files, but none for the levels "
                f"and method chosen ({inputs.tag or 'cpos'}). Expected "
                f"`{specs.conservation_jsonl_name(inputs, inputs.levels[0], stem)}`."
            ),
            source="protocol:stage_conservation:wrong_sets",
            patch={"native_ids": native},
        )

    await ctx.jobs(
        [
            (
                "fixed-positions-push",
                specs.push_job_spec(
                    ctx.site.sub(inputs, "mpnn"), pushes, name="fixed-positions-push"
                ),
            )
        ]
    )

    line = f"Conservation done. {'; '.join(counts)}."
    return Outcome(
        reply=f"{line} Redesigning next, SolubleMPNN and HaloMPNN at each level.",
        source="protocol:stage_conservation:summary",
        stage="mpnn",
        patch={"native_ids": native},
        notebook=notes.entry(
            "6-7",
            "Conservation (HHblits) and fixed positions",
            goal="Conserved positions at each level, plus the 10 A catalytic shell.",
            result=line,
            files=sorted(pushes),
            notes=(
                f"cat_cutoff 10.0 A; catalytic residues "
                f"{', '.join(str(p) for p in inputs.cat_res)}."
            ),
            jobs=[],
        ),
    )


async def stage_mpnn(ctx: Ctx) -> Outcome:
    """Steps 8-9: SolubleMPNN and HaloMPNN at every chosen level."""
    refusal = unavailable(ctx)
    if refusal:
        return refusal
    inputs = ctx.inputs
    stem = ctx.model_stem
    params = specs.MpnnParams()

    items = []
    for level in inputs.levels:
        for halo in (True, False):
            items.append(
                (
                    f"proteinmpnn-{specs.mpnn_out_dir(inputs, level, halo=halo)}",
                    specs.mpnn_job_spec(
                        ctx.site, inputs, model_stem=stem, level=level, halo=halo, params=params
                    ),
                )
            )
    status(f"Running {len(items)} ProteinMPNN jobs…", node="protocol")
    records, native = await ctx.jobs(items)
    failures = errors_of(records)

    fetch_items = []
    for level in inputs.levels:
        for halo in (True, False):
            out = specs.mpnn_out_dir(inputs, level, halo=halo)
            fetch_items.append(
                (out, f"{ctx.site.sub(inputs, 'mpnn')}/{out}/seqs/{stem}.fa")
            )
    fetched, _ = await ctx.jobs(
        [("mpnn-fetch", specs.fetch_job_spec(fetch_items, name="mpnn-fetch"))]
    )
    paths = artifacts_of(fetched)

    summary: list[str] = []
    warnings: list[str] = list(failures)
    for prefix, _ in fetch_items:
        key = f"{prefix}/{stem}.fa"
        if key not in paths:
            warnings.append(f"{prefix} returned no FASTA")
            continue
        parsed = variants_from_mpnn_fasta(read_artifact(paths, key))
        n = parsed.get("n", 0)
        # The skill's own check. A short count means the run was cut off, and
        # the designs that did land are not a complete sample.
        short = (
            ""
            if n + 1 == params.expected_sequences
            else f" (expected {params.expected_sequences - 1})"
        )
        summary.append(f"{prefix}: {n} designs{short}")
        if short:
            warnings.append(f"{prefix} produced {n} designs, not {params.expected_sequences - 1}")

    if not summary:
        return Outcome(
            reply=(
                "No ProteinMPNN output came back. "
                f"{notes.failure_note('proteinmpnn', warnings)}"
            ),
            source="protocol:stage_mpnn:no_designs",
            patch={"native_ids": native},
            warnings=tuple(warnings),
        )

    line = "Redesign done. " + "; ".join(summary) + "."
    return Outcome(
        reply=f"{line} Scoring and selecting next.",
        source="protocol:stage_mpnn:summary",
        stage="score",
        patch={"native_ids": native},
        warnings=tuple(warnings),
        notebook=notes.entry(
            "8-9",
            "ProteinMPNN / HaloMPNN redesign",
            goal="Sample redesigns of the catalytic domains at each level.",
            result=line,
            notes=(
                f"num_seq_per_target {params.num_seq_per_target}, "
                f"sampling_temp \"{params.temps}\", seed {params.seed}, "
                f"omit_AAs {params.omit_aas}"
            ),
            jobs=[],
        ),
    )


async def stage_score(ctx: Ctx) -> Outcome:
    """Step 10: the scoring notebook, then the selection checkpoint."""
    refusal = unavailable(ctx)
    if refusal:
        return refusal
    inputs = ctx.inputs
    level = DEFAULT_ANALYSIS_LEVEL if DEFAULT_ANALYSIS_LEVEL in inputs.levels else inputs.levels[0]

    cell = nb.configuration_cell(
        inputs,
        proj=ctx.site.proj(inputs),
        model_stem=ctx.model_stem,
        levels=(level,),
        analysis_level=level,
    )
    notebook_text = read_protocol_file("amarel/analysis/analyze_stabilization.ipynb")
    if not notebook_text:
        return Outcome(
            reply=(
                "The scoring notebook is not available to this server, so Step 10 "
                "cannot run. It ships with the skill, in "
                "`scripts/amarel/analysis/analyze_stabilization.ipynb`."
            ),
            source="protocol:stage_score:no_notebook",
        )

    status("Scoring and selecting designs…", node="protocol")
    await ctx.jobs(
        [
            (
                "analysis-config",
                specs.push_job_spec(
                    ctx.site.sub(inputs, "analysis"),
                    {"analyze_stabilization.ipynb": nb.patch_notebook(notebook_text, cell)},
                    name="analysis-config",
                ),
            )
        ]
    )
    records, native = await ctx.jobs(
        [("analysis", specs.analysis_job_spec(ctx.site, inputs))]
    )
    failures = errors_of(records)
    if failures:
        return Outcome(
            reply=f"The scoring notebook failed: {notes.failure_note('analysis', failures)}",
            source="protocol:stage_score:failed",
            patch={"native_ids": native},
        )

    results = f"{ctx.site.sub(inputs, 'analysis')}/{nb.results_dir(inputs)}"
    fetched, _ = await ctx.jobs(
        [
            (
                "analysis-fetch",
                specs.fetch_job_spec(
                    [("analysis", f"{results}/{name}") for name in nb.analysis_outputs(inputs)],
                    name="analysis-fetch",
                ),
            )
        ]
    )
    paths = artifacts_of(fetched)
    key = f"analysis/{nb.selected_fasta_name(inputs)}"
    if key not in paths:
        return Outcome(
            reply=(
                "The notebook ran but the selected designs did not come back. "
                f"Look in `{results}` on the cluster."
            ),
            source="protocol:stage_score:no_selection",
            patch={"native_ids": native},
        )

    records_fa = nb.read_fasta(read_artifact(paths, key))
    designs = af3_mod.design_names([h for h, _ in records_fa], ctx.model_stem)
    listing = "\n".join(f"- `{h}`" for h, _ in records_fa[:12])
    more = f"\n- …and {len(records_fa) - 12} more" if len(records_fa) > 12 else ""

    return Outcome(
        reply=(
            f"{len(records_fa)} designs selected at cpos{inputs.tag}{level}:\n\n"
            f"{listing}{more}\n\n"
            f"AlphaFold3 is about an hour of GPU each, so that is "
            f"{len(records_fa)} GPU-hours. Reply `go` to submit them all, or name "
            "the ones you want."
        ),
        source="protocol:stage_score:selection",
        awaiting="designs",
        patch={"native_ids": native, "designs": designs},
        notebook=notes.entry(
            "10",
            "Scoring and selection",
            goal="Rank and diversity-select the redesigns.",
            result=f"{len(records_fa)} designs selected at cpos{inputs.tag}{level}.",
            files=[f"{results}/{name}" for name in nb.analysis_outputs(inputs)],
            jobs=[],
        ),
    )


async def stage_af3_submit(ctx: Ctx) -> Outcome:
    """Step 11a: one AlphaFold3 job per design, submitted and left to run."""
    refusal = unavailable(ctx)
    if refusal:
        return refusal
    inputs = ctx.inputs
    designs = list(ctx.pstate.get("designs") or [])
    if not designs:
        return Outcome(
            reply="There are no selected designs to fold.",
            source="protocol:stage_af3_submit:no_designs",
        )

    scratch = ctx.site.af3_scratch(inputs)
    status(f"Submitting {len(designs)} AlphaFold3 jobs…", node="protocol")
    items = [
        (f"alphafold3-{name}", specs.af3_job_spec(ctx.site, inputs, design=name))
        for name in designs
    ]
    records, native = await ctx.jobs(items)
    failures = errors_of(records)

    line = (
        f"Submitted {len(designs) - len(failures)} of {len(designs)} AlphaFold3 jobs "
        f"to `{ctx.site.gpu_queue}`."
    )
    return Outcome(
        reply=(
            f"{line} Each is about an hour. Ask me again whenever you like and I "
            "will read the output directory — I do not need to be holding the "
            "jobs to report on them."
        ),
        source="protocol:stage_af3_submit:submitted",
        stage="af3_collect",
        patch={"native_ids": native},
        warnings=tuple(failures),
        notebook=notes.entry(
            "11",
            "AlphaFold3 on the selected designs",
            goal="Validate each selected design.",
            result=line,
            files=[f"{scratch}/af_output/"],
            jobs=[],
        ),
    )


async def stage_af3_collect(ctx: Ctx) -> Outcome:
    """Step 11b: read the output tree. Idempotent, and restart-proof.

    No handle is consulted. A backend restart loses every in-flight handle while
    the jobs keep running, so the only honest progress report is the one the
    filesystem gives.
    """
    refusal = unavailable(ctx)
    if refusal:
        return refusal
    inputs = ctx.inputs
    designs = list(ctx.pstate.get("designs") or [])
    scratch = ctx.site.af3_scratch(inputs)

    status("Reading the AlphaFold3 output directory…", node="protocol")
    fetched, _ = await ctx.jobs(
        [
            (
                "af3-collect",
                specs.fetch_job_spec(af3_mod.output_globs(scratch), name="af3-collect"),
            )
        ]
    )
    got = af3_mod.collect_summaries(
        {name: read_artifact(artifacts_of(fetched), name) for name in artifacts_of(fetched)}
    )
    done, total = got["n_done"], len(designs) or got["n_done"]

    if done < total:
        return Outcome(
            reply=(
                f"{done} of {total} designs have finished folding. Ask again later "
                "and I will re-read the directory."
            ),
            source="protocol:stage_af3_collect:in_progress",
            warnings=tuple(got["problems"]),
        )

    rows = sorted(
        got["designs"].items(),
        key=lambda kv: kv[1].get("ranking_score", kv[1].get("ptm", 0.0)),
        reverse=True,
    )
    table = "\n".join(
        f"- `{name}` — ranking {scores.get('ranking_score', '?')}, "
        f"pTM {scores.get('ptm', '?')}"
        for name, scores in rows[:10]
    )
    line = f"All {done} designs folded. Best: `{got['best']}`."
    return Outcome(
        reply=f"{line}\n\n{table}",
        source="protocol:stage_af3_collect:complete",
        stage="report",
        warnings=tuple(got["problems"]),
        notebook=notes.entry(
            "11",
            "AlphaFold3 results",
            result=line,
            files=[f"{scratch}/af_output/"],
            notes="\n".join(
                f"{name}: " + ", ".join(f"{k}={v}" for k, v in scores.items())
                for name, scores in rows
            ),
            jobs=[],
        ),
    )


async def stage_report(ctx: Ctx) -> Outcome:
    """The last entry: the job table from `sacct`, and nothing left to run."""
    inputs = ctx.inputs
    native = list(dict.fromkeys(ctx.pstate.get("native_ids") or []))
    jobs: list[dict[str, str]] = []
    if native and ctx.deps.tasks.hpc_available:
        script = read_protocol_file("job_stats.sh")
        if script:
            fetched, _ = await ctx.jobs(
                [("job-stats", specs.job_stats_spec(native, script))]
            )
            paths = artifacts_of(fetched)
            if "job_stats.md" in paths:
                jobs = notes.jobs_from_stats(read_artifact(paths, "job_stats.md"))

    return Outcome(
        reply=(
            f"The {inputs.name} campaign is complete. The lab notebook has every "
            f"stage, and the job table covers {len(jobs)} job(s)."
        ),
        source="protocol:stage_report:complete",
        stage="done",
        notebook=notes.entry(
            "12",
            "Campaign complete",
            result=f"{len(jobs)} jobs recorded.",
            jobs=jobs,
        ),
    )


async def stage_done(ctx: Ctx) -> Outcome:
    return Outcome(
        reply=(
            f"The {ctx.pstate.get('name', 'current')} campaign has finished. Start "
            "another by naming a new target."
        ),
        source="protocol:stage_done:finished",
    )


# --- answers to the checkpoints ------------------------------------------


def answer_inputs(ctx: Ctx) -> Outcome:
    """Re-run intake against the reply."""
    return Outcome(reply="", source="", stage="intake")


def answer_method(ctx: Ctx) -> Outcome:
    """The conservation method. The skill forbids a default, so a near-miss re-asks."""
    text = ctx.text.strip().lower()
    for candidate in ("conservation_liu", "cpos"):
        if candidate in text:
            tag = tag_for_method(candidate)
            return Outcome(
                reply=f"Using `{candidate}`. Fetching the structure.",
                source="protocol:answer_method:chosen",
                stage="structure",
                patch={"method": candidate, "tag": tag},
            )
    raise InvalidInput(
        "I need `cpos` or `conservation_liu`, exactly. The protocol has no "
        "default here because the choice decides which residues the redesign "
        "may change."
    )


def answer_cat_res(ctx: Ctx) -> Outcome:
    """The catalytic residues, in trimmed numbering."""
    inputs = ProtocolInputs.parse(
        name=ctx.pstate["name"],
        uniprot=ctx.pstate["uniprot"],
        domains=ctx.pstate["domains"],
        netid=ctx.pstate["netid"],
        method=ctx.pstate["method"],
    )
    found = re.findall(r"\d+", ctx.text)
    if not found:
        raise InvalidInput(
            "I need the catalytic residue positions as numbers, e.g. `310,364`."
        )
    confirmed = inputs.with_cat_res(found)
    return Outcome(
        reply=(
            f"Catalytic residues {', '.join(str(p) for p in confirmed.cat_res)} "
            "confirmed. Running the conservation search; it takes 15-20 minutes."
        ),
        source="protocol:answer_cat_res:confirmed",
        stage="conservation",
        patch={"cat_res": list(confirmed.cat_res)},
    )


def answer_designs(ctx: Ctx) -> Outcome:
    """Which selected designs to fold."""
    text = ctx.text.strip()
    available = list(ctx.pstate.get("designs") or [])
    if not available:
        raise InvalidInput("There are no selected designs on record to fold.")
    if re.search(r"\b(go|all|yes|proceed|submit)\b", text, re.I):
        chosen = available
    else:
        chosen = [name for name in available if name in text]
    if not chosen:
        raise InvalidInput(
            "I did not recognise any of those design names. Reply `go` to fold "
            "all of them, or paste the names from the list."
        )
    return Outcome(
        reply=f"Folding {len(chosen)} design(s).",
        source="protocol:answer_designs:confirmed",
        stage="af3_submit",
        patch={"designs": chosen},
    )


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


# --- helpers -------------------------------------------------------------


def parse_intake(text: str) -> dict[str, str]:
    """Pull `key=value` campaign inputs out of a message.

    Explicit keys rather than guessing: a misread domain string or accession
    would be discovered twenty minutes into an HHblits run, and a campaign is
    named after whatever it is given.
    """
    out: dict[str, str] = {}
    for key in ("name", "uniprot", "domains", "netid"):
        match = re.search(rf"\b{key}\s*[=:]\s*([^\s,;]+)", text, re.I)
        if match:
            out[key] = match.group(1).strip()
    return out


def unavailable(ctx: Ctx) -> Outcome | None:
    """Refuse by name rather than let a job spec be routed to the local pool.

    With no endpoint attached `interface_for` falls back to `local` with only a
    `log.info`, and the local interface has no task by these names -- so without
    this check a stage would fail with something unrelated to the real problem.
    """
    missing = ctx.site.missing()
    if missing:
        return Outcome(
            reply=(
                "The protocol needs the cluster's paths configured before it can "
                f"submit anything. Unset: {', '.join(missing)}. These are "
                "environment-only settings; `plans/AMAREL_ENDPOINT.md` says how "
                "to obtain them."
            ),
            source="protocol:unavailable:site_unconfigured",
        )
    if not ctx.deps.tasks.hpc_available:
        return Outcome(
            reply=(
                "No HPC endpoint is attached, so no job was submitted. Every step "
                "of this protocol runs where the databases, weights and "
                "environments are — there is no local fallback for it."
            ),
            source="protocol:unavailable:no_endpoint",
        )
    return None


def protocol_files(ctx: Ctx, trimmed_pdb: str, stem: str) -> dict[str, str]:
    """What the install stage carries into `$PROJ`: the scripts and the backbone."""
    files = {
        f"conservation/{stem}.pdb": trimmed_pdb,
        f"mpnn/pdb/{stem}.pdb": trimmed_pdb,
    }
    for name in (
        "amarel/conservation/hhblits_search.py",
        "amarel/conservation/PDB_Tools_V3.py",
        "amarel/analysis/mpnn_analysis.py",
        "amarel/analysis/score_sequences.py",
    ):
        text = read_protocol_file(name)
        if text:
            files[_install_path(name)] = text
    return files


def _install_path(name: str) -> str:
    """`amarel/conservation/x.py` -> `conservation/x.py`, the skill's own layout."""
    return name.split("/", 1)[1] if name.startswith("amarel/") else name


def read_protocol_file(name: str) -> str:
    """Read one of the skill's scripts, if the checkout is reachable.

    The scripts live in the skill repository, not here: copying them in would
    fork them, and `plans/BACKLOG.md` already records one place where this
    repository's port and the skill's original disagree. A missing file is
    reported by the stage that needed it rather than guessed at.
    """
    from ...config import get_settings

    root = getattr(get_settings(), "protocol_scripts_dir", "")
    if not root:
        return ""
    path = Path(root) / name
    try:
        return path.read_text()
    except OSError:
        log.info("protocol script not readable: %s", path)
        return ""


def _positions(jsonl_text: str) -> list[int]:
    """The conserved positions in a level's jsonl, for the designable count."""
    from ...protocol.fixed_positions import parse_fixed_positions_jsonl

    try:
        table = parse_fixed_positions_jsonl(jsonl_text)
    except InvalidInput:
        return []
    return next(iter(table.values()), [])


# --- the node ------------------------------------------------------------


def make_protocol(deps: Deps):
    async def protocol(state: DesignState) -> Command:
        pstate = dict(state.get("protocol") or {})
        ctx = Ctx(
            deps=deps,
            state=state,
            pstate=pstate,
            site=SiteLayout.from_settings(deps.settings),
            session_id=state.get("session_id", ""),
            campaign_id=deps.campaign_id(state),
            text=last_user_text(state),
        )

        awaiting = pstate.get("awaiting", "")
        try:
            outcome = None
            if awaiting:
                outcome = ANSWERS[awaiting](ctx)
                pstate.update(outcome.patch)
                pstate["awaiting"] = ""
                if outcome.stage:
                    pstate["stage"] = outcome.stage
                stage = outcome.stage
            else:
                stage = pstate.get("stage") or "intake"
            if stage:
                prepared = {**state, "protocol": pstate}
                needs_approval = (
                    submission_permission.get() is not None
                    and stage not in ("intake", "done")
                    and unavailable(ctx) is None
                )
                if needs_approval:
                    waiting = request_approval(deps, prepared, "protocol", stage)
                    if waiting:
                        waiting.update["protocol"] = pstate
                        return waiting
                    with permit_scope(deps, prepared, "protocol", stage):
                        followup = await STAGES[stage](ctx)
                else:
                    followup = await STAGES[stage](ctx)
                outcome = _merge(outcome, followup) if outcome else followup
        except InvalidInput as exc:
            # A refusal is the answer: keep the checkpoint open and say why.
            return Command(
                goto="__end__",
                update={
                    "protocol": pstate,
                    "messages": [{"role": "assistant", "content": str(exc)}],
                    "reply_source": "protocol:invalid_input",
                    "status": "waiting",
                },
            )
        except KeyError as exc:
            log.exception("unknown protocol stage or answer: %s", exc)
            return Command(
                goto="__end__",
                update={
                    "messages": [
                        {
                            "role": "assistant",
                            "content": "That campaign is in a state I do not recognise.",
                        }
                    ],
                    "reply_source": "protocol:unknown_stage",
                },
            )

        pstate.update(outcome.patch)
        if outcome.stage:
            pstate["stage"] = outcome.stage
        pstate["awaiting"] = outcome.awaiting

        update: dict[str, Any] = {
            "protocol": pstate,
            "intent": "protocol",
            "status": outcome.reply.split("\n", 1)[0][:120],
            "messages": [{"role": "assistant", "content": outcome.reply}],
            "reply_source": outcome.source,
        }
        if outcome.warnings:
            update["warnings"] = list(outcome.warnings)
        if outcome.notebook:
            ref = _append_notebook(deps, ctx, outcome.notebook)
            if ref:
                update["artifacts"] = [ref]
                pstate["notebook_id"] = ref["id"]
        return Command(goto="__end__", update=update)

    return protocol


def _merge(first: Outcome, second: Outcome) -> Outcome:
    """Fold an answer's outcome into the stage it triggered."""
    reply = "\n\n".join(part for part in (first.reply, second.reply) if part)
    return Outcome(
        reply=reply,
        source=second.source or first.source,
        stage=second.stage,
        awaiting=second.awaiting,
        patch={**first.patch, **second.patch},
        notebook=second.notebook,
        warnings=tuple(first.warnings) + tuple(second.warnings),
    )


def _append_notebook(deps: Deps, ctx: Ctx, block: str) -> dict | None:
    """Append a block to NOTEBOOK.md, in place, under a stable artifact id."""
    artifact_id = f"{NOTEBOOK_ID}-{ctx.session_id or 'default'}"
    try:
        existing = deps.artifacts.read_bytes(artifact_id)
        text = existing.decode("utf-8", errors="replace") if existing else ""
        if not text:
            text = notes.header(
                ctx.inputs,
                ctx.site,
                method=ctx.pstate.get("method", ""),
                offset_note="",
            )
        return deps.artifacts.add(
            "markdown",
            f"{ctx.pstate.get('name', 'campaign')} lab notebook",
            content=notes.append_entry(text, block),
            session_id=ctx.session_id,
            artifact_id=artifact_id,
        )
    except Exception:  # noqa: BLE001 - a notebook write must not lose a stage
        log.exception("could not append to the protocol notebook")
        return None
