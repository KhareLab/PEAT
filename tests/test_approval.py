"""Consent is an explicit checkpoint turn, never an LLM decision or a task retry."""

from __future__ import annotations

import asyncio

import pytest
from designagent.graph.approval import guard_node
from designagent.graph.build import build_graph
from designagent.graph.nodes.protocol import STAGES, Outcome
from designagent.protocol import specs
from designagent.protocol.inputs import ProtocolInputs
from designagent.tasks.base import TaskInterface, TaskSpec
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from tests.conftest import PDB_TEXT, REF_SEQ


class FakeHpc(TaskInterface):
    name = "hpc"
    connected = True

    def __init__(self):
        self.submitted = []
        self.on_submit = lambda: None

    async def submit(self, spec):
        self.submitted.append(spec.name)
        self.on_submit()
        future = asyncio.get_running_loop().create_future()
        if spec.name == "proteinmpnn":
            fasta = (
                f">target, model_name=test_weights, git_hash=offline\n{REF_SEQ}\n"
                f">T=0.1, sample=1, score=0.9\nA{REF_SEQ[1:]}\n"
                f">T=0.1, sample=2, score=0.8\n{REF_SEQ[:-1]}A\n"
            )
            result = {"artifacts": {"seqs/target.fa": fasta.encode()}}
        elif spec.name == "fold_sequence":
            result = {
                "sequence": spec.params["sequence"],
                "design_id": spec.params["design_id"],
                "structure": PDB_TEXT,
                "format": "pdb",
                "metrics": {"plddt": 90.0},
            }
        else:
            result = {"artifacts": {}}
        future.set_result(result)
        return self._handle(spec, future)


@pytest.fixture
def hpc(deps):
    deps.settings = deps.settings.model_copy(update={"max_rounds": 1, "fold_backend": "hpc"})
    fake = FakeHpc()
    deps.tasks.attach_hpc(fake)
    return fake


def config(session="a"):
    return {"configurable": {"thread_id": session}}


async def send(graph, text, session="a"):
    return await graph.ainvoke(
        {
            "messages": [{"role": "user", "content": text}],
            "session_id": session,
            "trace": [],
            "pending_results": {},
            "status": "",
        },
        config=config(session),
    )


async def test_workflow_consent_covers_generation_and_later_hpc_folds(deps, hpc, stub_tools):
    graph = build_graph(deps, checkpointer=InMemorySaver())
    waiting = await send(graph, "redesign 1UBQ for stability")
    assert hpc.submitted == []
    assert waiting["hpc_pending"]["node"] == "orchestrator"
    assert "at most 12" in waiting["messages"][-1].content

    finished = await send(graph, "approve hpc")
    assert hpc.submitted == ["proteinmpnn", "fold_sequence", "fold_sequence"]
    assert finished["ensemble"]
    assert finished["hpc_pending"] == {}

    await send(graph, "run another round")
    assert len(hpc.submitted) == 3, "a new turn reused earlier consent"


async def test_generic_agreement_and_cancel_do_not_submit(deps, hpc, stub_tools):
    graph = build_graph(deps, checkpointer=InMemorySaver())
    await send(graph, "redesign 1UBQ for stability")
    await send(graph, "yes")
    await send(graph, "approve hpc")
    assert hpc.submitted == []
    await send(graph, "run another round")
    canceled = await send(graph, "cancel hpc")
    assert canceled["hpc_pending"] == {}
    assert hpc.submitted == []


@pytest.mark.parametrize("change", ["settings", "constraints", "target", "lead", "metric"])
async def test_changed_plan_requires_fresh_consent(deps, hpc, stub_tools, change):
    graph = build_graph(deps, checkpointer=InMemorySaver())
    initial = await send(graph, "redesign 1UBQ for stability")
    if change == "settings":
        deps.settings = deps.settings.model_copy(update={"orbit_account": "new-account"})
    elif change == "constraints":
        await graph.aupdate_state(config(), {"requested_mutations": ["T55V"]})
    elif change == "target":
        ref = {**initial["reference_design"], "sequence": "A" + REF_SEQ[1:]}
        await graph.aupdate_state(config(), {"reference_design": ref})
    elif change == "lead":
        await graph.aupdate_state(
            config(),
            {
                "lead_design": {"design_id": "another", "sequence": "A" + REF_SEQ[1:]},
            },
        )
    else:
        await graph.aupdate_state(
            config(),
            {
                "key_metric": {"name": "sequence_identity", "direction": "max"},
            },
        )
    fresh = await send(graph, "approve hpc")
    assert hpc.submitted == []
    assert fresh["hpc_pending"]["digest"] != initial["hpc_pending"]["digest"]


async def test_consent_is_session_owned_and_survives_graph_rebuild(deps, hpc, stub_tools):
    saver = InMemorySaver()
    graph = build_graph(deps, checkpointer=saver)
    await send(graph, "redesign 1UBQ for stability", "alice")
    await send(graph, "approve hpc", "bob")
    assert hpc.submitted == []
    rebuilt = build_graph(deps, checkpointer=saver)
    completed = await send(rebuilt, "approve hpc", "alice")
    assert completed["ensemble"]
    assert hpc.submitted[0] == "proteinmpnn"


async def test_reconfiguration_between_batches_refuses_later_submissions(deps, hpc, stub_tools):
    graph = build_graph(deps, checkpointer=InMemorySaver())
    await send(graph, "redesign 1UBQ for stability")
    hpc.on_submit = lambda: setattr(
        deps,
        "settings",
        deps.settings.model_copy(update={"orbit_account": "changed"}),
    )
    await send(graph, "approve hpc")
    assert hpc.submitted == ["proteinmpnn"]
    folds = [t for t in deps.tasks.snapshot() if t["task"] == "fold_sequence"]
    assert folds and all(t["state"] == "FAILED" for t in folds)
    assert all("requires approval" in t["error"] for t in folds)
    assert len({t["id"] for t in folds}) == len(folds)


async def test_one_consent_allows_trusted_multi_round_progress(deps, hpc, stub_tools):
    deps.settings = deps.settings.model_copy(update={"max_rounds": 2})
    graph = build_graph(deps, checkpointer=InMemorySaver())
    await send(graph, "redesign 1UBQ for stability")
    completed = await send(graph, "approve hpc")
    assert completed["round"] == 2
    assert completed["hpc_pending"] == {}
    assert hpc.submitted.count("proteinmpnn") == 2


async def test_unapproved_graph_callsite_cannot_submit_but_standalone_tasks_can(deps, hpc):
    async def unexpected(state):
        records = await deps.tasks.run_many(
            [
                TaskSpec("proteinmpnn", params={"_interface": "hpc"}),
            ]
        )
        assert records[0]["ok"] is False
        return Command(goto="__end__")

    await guard_node("analyst", unexpected, deps)({})
    assert hpc.submitted == []
    await deps.tasks.run_many([TaskSpec("proteinmpnn", params={"_interface": "hpc"})])
    assert hpc.submitted == ["proteinmpnn"]


@pytest.mark.parametrize(
    "awaiting,text,stage,patch",
    [
        ("method", "cpos", "structure", {"method": "cpos"}),
        ("cat_res", "2,4", "conservation", {"cat_res": [2, 4]}),
        ("designs", "go", "af3_submit", {"designs": ["design1"]}),
    ],
)
async def test_protocol_answers_are_saved_before_stage_approval(
    deps,
    hpc,
    monkeypatch,
    awaiting,
    text,
    stage,
    patch,
):
    deps.settings = deps.settings.model_copy(
        update={
            "protocol_proj_root": "/projects/offline",
            "protocol_conda_aifold": "/envs/aifold",
            "protocol_conda_analysis": "/envs/analysis",
            "protocol_mpnn_path": "/tools/mpnn",
            "protocol_mpnn_weights": "/tools/weights",
            "protocol_uniref_db": "/db/uniref",
            "protocol_af3_modules": "module load af3",
        }
    )
    called = []

    async def stage_body(ctx):
        called.append(stage)
        label = {
            "structure": "protocol-install",
            "conservation": "hhblits",
            "af3_submit": "alphafold3-design1",
        }[stage]
        records, _ = await ctx.jobs([(label, {"executable": "unused"})])
        assert records[0]["ok"]
        return Outcome(reply="Offline stage completed.", source="protocol:offline", stage="done")

    monkeypatch.setitem(STAGES, stage, stage_body)
    graph = build_graph(deps, checkpointer=InMemorySaver())
    protocol = {
        "stage": stage,
        "awaiting": awaiting,
        "name": "offline",
        "uniprot": "P0CG48",
        "domains": "1-CD-76",
        "netid": "abc123",
        "method": "cpos",
        "designs": ["design1"],
    }
    await graph.aupdate_state(config(), {"session_id": "a", "protocol": protocol})
    waiting = await send(graph, text)
    assert hpc.submitted == [] and called == []
    assert waiting["protocol"]["awaiting"] == ""
    assert all(waiting["protocol"][key] == value for key, value in patch.items())
    assert waiting["hpc_pending"]["stage"] == stage
    await send(graph, "approve hpc")
    assert called == [stage]
    assert len(hpc.submitted) == 1


@pytest.mark.parametrize("method", ["cpos", "conservation_liu"])
async def test_protocol_mpnn_consent_allows_actual_stage_labels(deps, hpc, monkeypatch, method):
    from designagent.graph.approval import permit_scope, request_approval
    from designagent.graph.nodes.protocol import Ctx, stage_mpnn
    from designagent.protocol.site import SiteLayout

    deps.settings = deps.settings.model_copy(
        update={
            "protocol_proj_root": "/projects/offline",
            "protocol_conda_aifold": "/envs/aifold",
            "protocol_conda_analysis": "/envs/analysis",
            "protocol_mpnn_path": "/tools/mpnn",
            "protocol_mpnn_weights": "/tools/weights",
            "protocol_uniref_db": "/db/uniref",
        }
    )
    protocol = {
        "name": "offline",
        "uniprot": "P0CG48",
        "domains": "1-CD-76",
        "netid": "abc123",
        "method": method,
        "levels": [30, 50, 70],
        "cat_res": [2],
        "model_stem": "model",
    }
    state = {"session_id": "a", "protocol": protocol}
    proposal = request_approval(deps, state, "protocol", "mpnn")
    state["hpc_approval"] = proposal.update["hpc_pending"]
    ctx = Ctx(deps, state, protocol, SiteLayout.from_settings(deps.settings), "a", "a", "")
    with permit_scope(deps, state, "protocol", "mpnn"):
        await stage_mpnn(ctx)
    inputs = ProtocolInputs.parse(
        **{
            key: protocol[key]
            for key in (
                "name",
                "uniprot",
                "domains",
                "netid",
                "method",
                "levels",
                "cat_res",
            )
        }
    )
    expected = [
        f"proteinmpnn-{specs.mpnn_out_dir(inputs, level, halo=halo)}"
        for level in inputs.levels
        for halo in (True, False)
    ]
    assert hpc.submitted == [*expected, "mpnn-fetch"]
