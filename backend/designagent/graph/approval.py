"""Turn-boundary consent for bounded remote workflows, separate from LLM routing."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from langgraph.types import Command
from pydantic import SecretStr

from ..context import current_turn
from ..protocol.inputs import tag_for_method
from .state import last_user_text

# None is an API-independent task caller; every graph node installs a guard.
submission_permission: ContextVar[Callable[[Any], bool] | None] = ContextVar(
    "submission_permission",
    default=None,
)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _scope(deps, state: dict, node: str, stage: str = "") -> dict:
    settings = deps.settings
    turn = current_turn.get()
    values = settings.model_dump()
    compute = {
        key: value
        for key, value in values.items()
        if not isinstance(value, SecretStr)
        and (
            key.startswith(("orbit_", "globus_", "mpnn_", "protocol_", "fold_"))
            or key in ("max_rounds", "model", "task_timeout_sec")
        )
    }
    return {
        "node": node,
        "stage": stage,
        "session": state.get("session_id", ""),
        "user": turn.user_id if turn else "",
        "settings": compute,
        "endpoint": (
            getattr(deps.tasks._hpc(), "endpoint_name", "")
            or getattr(deps.tasks._hpc(), "_endpoint_id", "")
        ),
        "target": _digest(state.get("reference_design") or {}),
        "lead": _digest(state.get("lead_design") or {}),
        "metric": state.get("key_metric") or {},
        "goal": state.get("goal", ""),
        "mutations": state.get("requested_mutations") or [],
        "protocol": state.get("protocol") or {} if node == "protocol" else {},
    }


def _reply(text: str, **update) -> Command:
    return Command(
        goto="__end__",
        update={
            "messages": [{"role": "assistant", "content": text}],
            "reply_source": "approval:waiting",
            "status": "waiting",
            **update,
        },
    )


def request_approval(deps, state: dict, node: str, stage: str = "") -> Command | None:
    scope = _scope(deps, state, node, stage)
    pending = {"node": node, "stage": stage, "digest": _digest(scope)}
    if state.get("hpc_approval") == pending:
        return None
    settings = deps.settings
    ref = state.get("reference_design") or {}
    protocol = state.get("protocol") or {}
    endpoint = scope["endpoint"] or settings.orbit_endpoint or settings.globus_endpoint_id
    site = (
        f"Endpoint: {endpoint or '(connected endpoint)'}. "
        f"Account: {settings.orbit_account or '(site default)'}. "
        f"Queue: {settings.orbit_queue or '(site default)'}."
    )
    if node == "orchestrator":
        target = (
            ref.get("pdb_id") or ref.get("uniprot_id") or ref.get("name") or "the loaded target"
        )
        details = (
            f"Redesign {target}"
            f" for: {state.get('goal') or 'the current objective'}. "
            f"Requested mutations: {', '.join(state.get('requested_mutations') or []) or 'none'}. "
            f"This allows up to {max(1, settings.max_rounds)} rounds, each with one ProteinMPNN "
            "generation job and at most 12 candidate folding jobs if HPC is selected. "
            "The planner chooses the candidate count and a supported scoring metric within "
            "that scope; structure confidence is not a measured stability result. "
            f"Job walltime: {settings.orbit_job_duration_sec}s; GPUs per job: "
            f"{settings.orbit_job_gpus}; MPNN sampling temperature: {settings.mpnn_sampling_temp}."
        )
    else:
        details = (
            f"Protocol stage: {stage}. Target: {protocol.get('name', '')} "
            f"({protocol.get('uniprot', '')}). Domains: {protocol.get('domains', '')}. "
            f"Conservation: {protocol.get('method', '')}; levels: "
            f"{protocol.get('levels') or [30, 50, 70]}; catalytic positions: "
            f"{protocol.get('cat_res') or []}. Selected designs: {protocol.get('designs') or []}. "
            "Approval covers this stage's compute and file transfers only."
        )
    return _reply(
        f"HPC approval required. {details}\n\n{site}\n\n"
        "No job has been submitted for this proposal. Reply `approve hpc` to allow it, "
        "or `cancel hpc` to cancel this proposal.",
        hpc_pending=pending,
        hpc_approval={},
    )


def _limits(state: dict, node: str, stage: str) -> dict[str, int]:
    if node == "orchestrator":
        return {"proteinmpnn": 1, "fold_sequence": 12}
    protocol = state.get("protocol") or {}
    if stage == "mpnn":
        tag = tag_for_method(protocol.get("method") or "cpos")
        return {
            **{
                f"proteinmpnn-cpos{tag}{level}{suffix}": 1
                for level in protocol.get("levels") or [30, 50, 70]
                for suffix in ("", "_halo")
            },
            "mpnn-fetch": 1,
        }
    if stage == "af3_submit":
        return {f"alphafold3-{name}": 1 for name in protocol.get("designs") or []}
    return {
        "structure": {"protocol-install": 1},
        "conservation": {"hhblits": 1, "conservation-fetch": 1, "fixed-positions-push": 1},
        "score": {"analysis-config": 1, "analysis": 1, "analysis-fetch": 1},
        "af3_collect": {"af3-collect": 1},
        "report": {"job-stats": 1},
    }.get(stage, {})


@contextmanager
def permit_scope(deps, state: dict, node: str, stage: str = ""):
    expected = _digest(_scope(deps, state, node, stage))
    approval = {"node": node, "stage": stage, "digest": expected}
    limits = _limits(state, node, stage)
    submitted: Counter = Counter()

    def permitted(spec) -> bool:
        if state.get("hpc_approval") != approval:
            return False
        if _digest(_scope(deps, state, node, stage)) != expected:
            return False
        if submitted[spec.name] >= limits.get(spec.name, 0):
            return False
        submitted[spec.name] += 1
        return True

    token = submission_permission.set(permitted)
    try:
        yield
    finally:
        submission_permission.reset(token)


def guard_node(name: str, fn: Callable, deps) -> Callable:
    async def guarded(state: dict):
        token = submission_permission.set(lambda spec: False)
        try:
            if name == "coordinator":
                state = {**state, "hpc_approval": {}}
                action = last_user_text(state).strip().lower()
                pending = state.get("hpc_pending") or {}
                if action == "cancel hpc":
                    return _reply(
                        "HPC proposal canceled. No new job was submitted.",
                        hpc_pending={},
                        hpc_approval={},
                    )
                if action == "approve hpc":
                    if not pending or not deps.tasks.hpc_available:
                        return _reply(
                            "There is no available HPC proposal to approve.",
                            hpc_pending={},
                            hpc_approval={},
                        )
                    node, stage = pending["node"], pending.get("stage", "")
                    if pending["digest"] != _digest(_scope(deps, state, node, stage)):
                        return request_approval(deps, state, node, stage)
                    return Command(goto=node, update={"hpc_approval": pending, "hpc_pending": {}})
                result = await fn(state)
                return Command(
                    goto=result.goto,
                    graph=result.graph,
                    resume=result.resume,
                    update={**(result.update or {}), "hpc_approval": {}, "hpc_pending": {}},
                )
            if name == "orchestrator" and deps.tasks.hpc_available:
                waiting = request_approval(deps, state, name)
                if waiting:
                    return waiting
                with permit_scope(deps, state, name):
                    result = await fn(state)
            else:
                result = await fn(state)
            approval = state.get("hpc_approval") or {}
            if name in ("orchestrator", "analyst") and approval.get("node") == "orchestrator":
                # Only trusted results may evolve an approved workflow's lead and metric.
                if approval.get("digest") == _digest(_scope(deps, state, "orchestrator")):
                    updated = {**state, **(result.update or {})}
                    result.update["hpc_approval"] = {
                        **approval,
                        "digest": _digest(_scope(deps, updated, "orchestrator")),
                    }
            return result
        finally:
            submission_permission.reset(token)

    return guarded
