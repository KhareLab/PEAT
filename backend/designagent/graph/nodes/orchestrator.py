"""Redesign Orchestrator: turns the goal into a key metric and a worklist.

It chooses the metric that defines success, then picks tasks from the available
Task Interfaces and dispatches them. Routing to HPC happens here, because only
this node knows both the task and whether an endpoint is connected.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Literal

from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ...llm import complete_json
from ...tasks.base import TaskSpec
from ...tasks.jobspec import batch_timeout, job_params
from ...tasks.registry import CATALOG, describe
from ...tools.esmfold import ESMATLAS_MAX_LEN, fold_job_spec
from ...tools.proteinmpnn import mpnn_job_spec, variants_from_mpnn_fasta
from ..deps import Deps, llm_caveat, status
from ..state import DesignState

log = logging.getLogger(__name__)

# Metrics the loop knows how to compute, with the direction that means "better".
METRICS = {
    "plddt": ("max", "Predicted structure confidence (ESMFold pLDDT, 0-100)."),
    "rmsd_to_reference": ("min", "Backbone deviation from the reference structure."),
    "mpnn_score": ("min", "ProteinMPNN negative log-likelihood of the sequence."),
    "sequence_identity": ("max", "Fraction of positions identical to the reference."),
    "mean_hydropathy": ("min", "Mean Kyte-Doolittle hydropathy (lower is more soluble)."),
    "net_charge": ("max", "Net charge at neutral pH."),
}

GOAL_KEYWORDS = {
    "plddt": ("stable", "stability", "thermostab", "fold", "confidence", "structure", "rigid"),
    "mean_hydropathy": ("soluble", "solubility", "aggregat", "express"),
    "rmsd_to_reference": ("conserve", "preserve", "keep the fold", "same shape", "scaffold"),
    "sequence_identity": ("conservative", "minimal change", "few mutations"),
    "net_charge": ("charge", "electrostatic", "ph "),
}

class PlannedMetric(BaseModel):
    model_config = ConfigDict(strict=True)

    name: str
    direction: Literal["max", "min"] | None = None
    target: float | None = Field(default=None, allow_inf_nan=False)
    description: str | None = None

    @model_validator(mode="after")
    def supported_metric(self) -> PlannedMetric:
        if self.name not in METRICS:
            raise ValueError("unsupported metric")
        if self.direction is not None and self.direction != METRICS[self.name][0]:
            raise ValueError("metric direction disagrees with its scientific definition")
        return self


class PlannedTask(BaseModel):
    model_config = ConfigDict(strict=True)

    task: str
    why: str | None = None

    @field_validator("task")
    @classmethod
    def selectable_task(cls, value: str) -> str:
        task = CATALOG.get(value)
        if task is None or not task.planner_selectable:
            raise ValueError("task is not available to the planner")
        return value


class RoundPlan(BaseModel):
    model_config = ConfigDict(strict=True)

    key_metric: PlannedMetric | None = None
    tasks: list[PlannedTask] | None = None
    n_variants: int = Field(default=6, ge=2, le=12)
    notes: str | None = None


PLAN_SYSTEM = """You plan one round of protein redesign.

Pick the single metric that best measures the user's goal, from this list:
""" + "\n".join(f"  {name} ({d}): {desc}" for name, (d, desc) in METRICS.items()) + """

Then choose which tasks to run this round from the catalog given below. Prefer:
  - propose_variants when new candidate sequences are needed
  - apply_mutations when the user named specific mutations
  - fold_sequence to get a structure and pLDDT for each candidate
  - run_chemgraph only for small-molecule/ligand chemistry questions

Return JSON:
{"key_metric": {"name": ..., "direction": "max"|"min", "target": number or null,
                "description": "..."},
 "tasks": [{"task": "<catalog name>", "why": "..."}],
 "n_variants": integer 2-12,
 "notes": "one sentence on the plan"}"""


def choose_metric_rules(goal: str, state: DesignState) -> dict[str, Any]:
    """Keyword-based metric choice, used when no LLM is configured."""
    low = (goal or "").lower()
    for metric, words in GOAL_KEYWORDS.items():
        if any(word in low for word in words):
            direction, description = METRICS[metric]
            return {
                "name": metric,
                "direction": direction,
                "target": None,
                "description": description,
            }
    direction, description = METRICS["plddt"]
    return {
        "name": "plddt",
        "direction": direction,
        "target": None,
        "description": description,
    }


def make_orchestrator(deps: Deps):
    async def orchestrator(state: DesignState) -> Command:
        session_id = state.get("session_id", "")
        campaign_id = deps.campaign_id(state)
        reference = state.get("reference_design") or {}
        goal = state.get("goal", "")
        round_no = int(state.get("round", 0)) + 1

        parent = state.get("lead_design") or {}
        parent_sequence = parent.get("sequence") or reference.get("sequence") or ""
        if not parent_sequence:
            return Command(
                goto="__end__",
                update={
                    "messages": [
                        {
                            "role": "assistant",
                            "content": "I have no sequence to redesign yet.",
                        }
                    ],
                    "reply_source": "orchestrator:no_sequence",
                },
            )

        status(f"Planning round {round_no}…", node="orchestrator")

        # --- decide the metric and the plan ---
        plan: dict[str, Any] | None = None
        caveats: list[str] = []
        if deps.settings.llm_available:
            plan = await complete_json(
                PLAN_SYSTEM + "\n\nCatalog:\n" + describe({"local", "query", "hpc"}),
                _plan_context(state, round_no, parent_sequence),
                settings=deps.settings,
                on_fallback=llm_caveat(deps, caveats),
                schema=RoundPlan,
                max_tokens=900,
            )

        key_metric = state.get("key_metric") or {}
        if plan and isinstance(plan.get("key_metric"), dict):
            proposed = plan["key_metric"]
            name = str(proposed.get("name", "")).strip()
            if name in METRICS:
                key_metric = {
                    "name": name,
                    "direction": proposed.get("direction") or METRICS[name][0],
                    "target": proposed.get("target"),
                    "description": proposed.get("description") or METRICS[name][1],
                }
        if not key_metric.get("name"):
            key_metric = choose_metric_rules(goal, state)

        n_variants = plan["n_variants"] if plan else 6
        requested = state.get("requested_mutations") or []

        # --- build the worklist ---
        work: list[dict[str, Any]] = []
        specs: list[TaskSpec] = []

        if requested:
            work.append(
                _item("apply_mutations", "local", {
                    "sequence": parent_sequence,
                    "mutations": requested,
                })
            )
        else:
            hints = reference.get("literature_mutations") or []
            params = {
                "sequence": parent_sequence,
                "n": n_variants,
                "suggested_mutations": hints,
                "seed": round_no,
                "structure_path": reference.get("structure_path", ""),
            }
            job = None
            if deps.tasks.hpc_available:
                # Without a `job_spec` the remote interface submits
                # `to_psij_spec({})`, which is `/bin/true` — a job that succeeds
                # and does nothing.
                job = _mpnn_job(deps, reference, n_variants, caveats)
            if job is not None:
                params.update(job_params(deps.settings, job))
                work.append(_item("proteinmpnn", "hpc", params))
            else:
                work.append(_item("propose_variants", "local", params))

        # ChemGraph only when the goal is actually about chemistry.
        if plan and any(
            t.get("task") == "run_chemgraph" for t in (plan.get("tasks") or [])
        ):
            ligands = [lig.get("comp_id") for lig in (reference.get("ligands") or [])]
            work.append(
                _item("run_chemgraph", "local", {
                    "task": (
                        f"For the protein {reference.get('name', '')} with ligand(s) "
                        f"{', '.join(filter(None, ligands)) or 'none'}: {goal}"
                    ),
                })
            )

        for item in work:
            specs.append(
                TaskSpec(
                    name=item["task"],
                    params={**item["params"], "_interface": item["interface"]},
                    label=item["task"].replace("_", " "),
                    kind="job" if item["interface"] == "hpc" else "function",
                )
            )

        status(f"Generating candidates ({n_variants} requested)…", node="orchestrator")
        records = await deps.tasks.run_many(
            specs,
            session_id=session_id,
            campaign_id=campaign_id,
            timeout=batch_timeout(deps.settings, specs),
        )

        # --- fold every candidate produced ---
        # A job's result is {job_id, state, exit_code, stdout, artifacts}: the
        # FASTA has to become variants before `_collect_variants` can see them.
        _adapt_mpnn_records(
            deps, records, parent_sequence, caveats, campaign_id, round_no, n_variants
        )
        variants = _collect_variants(records)
        if not variants:
            fallback = await _heuristic_fallback(
                deps,
                parent_sequence,
                reference,
                n_variants,
                round_no,
                session_id,
                campaign_id,
                records,
                caveats,
            )
            if fallback:
                work.append(fallback["item"])
                variants = fallback["variants"]
        if not variants:
            note = _failure_note(records)
            return Command(
                goto="interpreter",
                update={
                    "key_metric": key_metric,
                    "round": round_no,
                    "worklist": [
                        {**item, "status": "failed", "error": note} for item in work
                    ],
                    "status": note,
                    # Without this every caveat gathered above is dropped on
                    # exactly the turn that owes the user an explanation.
                    **({"warnings": caveats} if caveats else {}),
                },
            )

        fold_specs: list[TaskSpec] = []
        fold_backend = _fold_backend(deps, variants)
        for i, variant in enumerate(variants):
            design_id = f"{campaign_id}-r{round_no}-{i + 1}"
            variant["design_id"] = design_id
            params = {
                "sequence": variant["sequence"],
                "design_id": design_id,
                "backend": fold_backend,
                "_interface": "hpc" if fold_backend == "hpc" else "local",
            }
            if fold_backend == "hpc":
                params.update(
                    job_params(deps.settings, fold_job_spec(variant["sequence"], name=design_id))
                )
            fold_specs.append(
                TaskSpec(
                    name="fold_sequence",
                    params=params,
                    label=f"fold {design_id}",
                    kind="job" if fold_backend == "hpc" else "function",
                )
            )

        status(f"Folding {len(fold_specs)} candidate(s)…", node="orchestrator")
        fold_records = await deps.tasks.run_many(
            fold_specs,
            session_id=session_id,
            campaign_id=campaign_id,
            timeout=batch_timeout(deps.settings, fold_specs),
        )

        # Coordinates go to a blob here, not into state: a checkpoint carrying
        # six PDB files is hundreds of KB per turn and grows with the ensemble.
        fold_summaries: list[dict[str, Any]] = []
        for record in fold_records:
            result = record.get("result") if isinstance(record.get("result"), dict) else {}
            summary: dict[str, Any] = {
                "design_id": (
                    result.get("design_id")
                    or (record.get("params") or {}).get("design_id")
                ),
                "ok": bool(record.get("ok")) and not result.get("error"),
                "error": record.get("error") or result.get("error", ""),
                "metrics": result.get("metrics") or {},
                "backend": result.get("backend", ""),
            }
            structure = result.get("structure")
            if summary["ok"] and isinstance(structure, str) and structure:
                summary["structure_path"] = deps.history.write_blob(
                    structure,
                    suffix=".pdb" if result.get("format", "pdb") == "pdb" else ".cif",
                    prefix=summary["design_id"] or "fold",
                )
                summary["structure_format"] = result.get("format", "pdb")
            fold_summaries.append(summary)

        worklist = [{**item, "status": "done"} for item in work]
        worklist += [
            {
                "id": f"fold-{r['id']}",
                "interface": r["interface"],
                "task": r["task"],
                "params": {"design_id": r["params"].get("design_id")},
                "status": "done" if r["ok"] else "failed",
                "handle_id": r["id"],
                "error": r.get("error") or "",
            }
            for r in fold_records
        ]

        return Command(
            goto="analyst",
            update={
                "key_metric": key_metric,
                "round": round_no,
                "worklist": worklist,
                # Handed to the analyst as the round's raw material. Slim by
                # construction: structures are referenced by blob path.
                "pending_results": {
                    "variants": variants,
                    "folds": fold_summaries,
                    "parent_sequence": parent_sequence,
                    "parent_id": parent.get("design_id"),
                },
                "status": f"Round {round_no}: scoring {len(variants)} candidate(s)",
                **({"warnings": caveats} if caveats else {}),
            },
        )

    return orchestrator


# --- helpers ---------------------------------------------------------------


def _mpnn_job(
    deps: Deps, reference: dict, n_variants: int, caveats: list[str]
) -> dict[str, Any] | None:
    """Build the ProteinMPNN job spec, or None to fall back to the heuristic.

    The structure travels with the job as a declared input, so it is read here
    through `deps.history` -- a blob path means nothing at the far end, and a
    node must not touch the filesystem itself.
    """
    path = reference.get("structure_path", "")
    if not path or deps.history is None:
        caveats.append(
            "No reference structure was available to send to ProteinMPNN, "
            "so this round used the heuristic proposer."
        )
        return None
    raw = deps.history.read_blob(path)
    if not raw:
        caveats.append(
            "The reference structure could not be read, so this round used "
            "the heuristic proposer."
        )
        return None
    try:
        return mpnn_job_spec(
            raw.decode(errors="replace"),
            num_sequences=n_variants,
            sampling_temp=deps.settings.mpnn_sampling_temp,
            command=deps.settings.mpnn_command,
            prologue=deps.settings.mpnn_prologue,
        )
    except ValueError as exc:
        caveats.append(f"ProteinMPNN could not be run ({exc}); used the heuristic proposer.")
        return None


def _adapt_mpnn_records(
    deps: Deps,
    records: list[dict],
    parent_sequence: str,
    caveats: list[str],
    campaign_id: str,
    round_no: int,
    requested: int,
) -> None:
    """Turn each ProteinMPNN job's staged FASTA into variants, in place.

    `_collect_variants` stays generic: it reads `result["variants"]`, and this
    is what puts them there. The FASTA itself goes to a blob, because a round's
    worth of sequences has no business in a checkpoint.
    """
    for record in records:
        if record.get("task") != "proteinmpnn" or record.get("interface") != "hpc":
            continue
        result = record.get("result")
        if not isinstance(result, dict):
            continue
        fasta, fasta_path = _staged_fasta(deps, result)
        if not fasta:
            caveats.append(
                "ProteinMPNN returned no sequence file"
                + (f" ({result['artifacts_error']})" if result.get("artifacts_error") else "")
                + "; falling back to the heuristic proposer."
            )
            continue
        adapted = variants_from_mpnn_fasta(
            fasta, parent_sequence=parent_sequence, requested=requested
        )
        if adapted.get("error"):
            caveats.append(f"ProteinMPNN output could not be read: {adapted['error']}")
            continue
        result["variants"] = adapted["variants"]
        result["method"] = "proteinmpnn"
        if result.get("artifacts_truncated"):
            caveats.append(
                "ProteinMPNN's output was truncated in transit, so fewer "
                "candidates than requested were read."
            )
        if adapted.get("short"):
            caveats.append(f"ProteinMPNN returned {adapted['short']}.")
        model = (adapted.get("provenance") or {}).get("model_name")
        if model:
            result["model_name"] = model
        else:
            caveats.append(
                "The ProteinMPNN output declared no model name, so these "
                "samples cannot be attributed to specific weights."
            )
        # The manager already stored it; writing it again under a prettier
        # prefix would put identical bytes on disk twice.
        if fasta_path:
            result["fasta_path"] = fasta_path
        elif deps.history is not None:
            result["fasta_path"] = deps.history.write_blob(
                fasta, suffix=".fa", prefix=f"mpnn-{campaign_id}-r{round_no}"
            )
        # Keep only the reference: a round of sequences must not ride in state.
        result.pop("artifacts", None)


def _staged_fasta(deps: Deps, result: dict) -> tuple[str, str]:
    """Read the one FASTA a ProteinMPNN job staged back, as (text, blob path).

    The manager turns staged files into blob paths, so these are normally
    paths and the path is worth keeping; a raw `bytes` value only appears when
    no history was attached to store it.
    """
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, dict):
        return "", ""
    for name, value in artifacts.items():
        if not name.endswith((".fa", ".fasta")):
            continue
        if isinstance(value, bytes):
            return value.decode(errors="replace"), ""
        if isinstance(value, str) and deps.history is not None:
            raw = deps.history.read_blob(value)
            if raw:
                return raw.decode(errors="replace"), value
    return "", ""


async def _heuristic_fallback(
    deps: Deps,
    parent_sequence: str,
    reference: dict,
    n_variants: int,
    round_no: int,
    session_id: str,
    campaign_id: str,
    records: list[dict],
    caveats: list[str],
) -> dict[str, Any] | None:
    """Run the heuristic proposer after a failed ProteinMPNN round.

    A round the user waited on must not end as "No candidate designs were
    produced" when there is still something honest to offer. The variants label
    themselves `heuristic`, and a caveat says what happened.
    """
    if not any(r.get("task") == "proteinmpnn" for r in records):
        return None
    # Not `_failure_note`: it is worded for a round that is about to end, and
    # quoting it here tells the user nothing was produced on a turn that did
    # produce something. The specific reason is already a caveat of its own.
    for record in records:
        if record.get("task") == "proteinmpnn" and record.get("error"):
            caveats.append(f"The ProteinMPNN job failed: {record['error']}")
            break
    caveats.append(
        "This round used the heuristic proposer instead of ProteinMPNN, so its "
        "designs are single substitutions from a fixed table rather than model "
        "samples."
    )
    # A fresh spec: `interface_for` pops `_interface` from the one above.
    item = _item("propose_variants", "local", {
        "sequence": parent_sequence,
        "n": n_variants,
        "suggested_mutations": reference.get("literature_mutations") or [],
        "seed": round_no,
    })
    retry = await deps.tasks.run_many(
        [TaskSpec(
            name="propose_variants",
            params={**item["params"], "_interface": "local"},
            label="propose variants",
            kind="function",
        )],
        session_id=session_id,
        campaign_id=campaign_id,
        timeout=deps.settings.task_timeout_sec,
    )
    variants = _collect_variants(retry)
    if not variants:
        return None
    return {"item": {**item, "status": "done"}, "variants": variants}


def _fold_backend(deps: Deps, variants: list[dict]) -> str:
    """Pick a folding backend that can actually handle these sequences."""
    configured = deps.settings.fold_backend
    longest = max((len(v.get("sequence", "")) for v in variants), default=0)
    if configured == "esmatlas" and longest > ESMATLAS_MAX_LEN:
        if deps.tasks.hpc_available:
            log.info("sequences are %d aa; routing folding to HPC", longest)
            return "hpc"
        log.info("sequences are %d aa; the public fold API will reject them", longest)
    return configured


def _item(task: str, interface: str, params: dict) -> dict[str, Any]:
    return {
        "id": f"{task}-{uuid.uuid4().hex[:8]}",
        "task": task,
        "interface": interface,
        "params": params,
        "status": "pending",
        "handle_id": None,
        "depends_on": [],
    }


def _collect_variants(records: list[dict]) -> list[dict]:
    """Pull candidate sequences out of whatever the generators returned."""
    out: list[dict] = []
    seen: set[str] = set()
    for record in records:
        if not record.get("ok"):
            continue
        result = record.get("result")
        if not isinstance(result, dict):
            continue
        for variant in result.get("variants") or []:
            sequence = (variant or {}).get("sequence")
            if not sequence or sequence in seen:
                continue
            seen.add(sequence)
            out.append(dict(variant))
    return out


def _failure_note(records: list[dict]) -> str:
    errors = []
    for record in records:
        if not record.get("ok"):
            errors.append(f"{record['task']}: {record.get('error', 'failed')}")
        else:
            result = record.get("result")
            if isinstance(result, dict) and result.get("error"):
                errors.append(f"{record['task']}: {result['error']}")
    return (
        "No candidate designs were produced. " + "; ".join(errors[:3])
        if errors
        else "No candidate designs were produced."
    )


def _plan_context(state: DesignState, round_no: int, sequence: str) -> str:
    import json

    reference = state.get("reference_design") or {}
    return json.dumps(
        {
            "goal": state.get("goal", ""),
            "round": round_no,
            "reference": {
                "pdb_id": reference.get("pdb_id"),
                "name": reference.get("name"),
                "length": reference.get("length"),
                "organism": reference.get("organism"),
                "ligands": [lig.get("comp_id") for lig in (reference.get("ligands") or [])],
                "functional_features": [
                    {"type": f.get("type"), "start": f.get("start")}
                    for f in (reference.get("features") or [])[:15]
                ],
                "literature_mutations": (reference.get("literature_mutations") or [])[:15],
            },
            "parent_sequence_length": len(sequence),
            "current_key_metric": state.get("key_metric") or {},
            "best_so_far": [
                {"design_id": d.get("design_id"), "metrics": d.get("metrics")}
                for d in (state.get("ensemble") or [])[:5]
            ],
            "requested_mutations": state.get("requested_mutations") or [],
        },
        default=str,
    )
