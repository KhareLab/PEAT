"""The agent loop: routing, state updates, and the full redesign path.

All of these run with no LLM key and no network: tool bodies are stubbed in the
registry, which is also how the task layer is meant to be substituted.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from designagent.graph.build import build_graph
from designagent.graph.nodes.coordinator import classify_rules, describe_state
from designagent.graph.state import merge_artifacts, merge_worklist, new_state
from designagent.tasks.registry import CATALOG, TaskDef
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import SecretStr

from tests.conftest import REF_SEQ


@pytest.fixture
def model_reply(monkeypatch, deps):
    """Drive the real completion/validation path with provider text, offline."""
    from designagent import llm

    replies = {"classification": {}, "plan": {}}

    class Model:
        async def ainvoke(self, messages):
            system = messages[0]["content"]
            if "You route a protein-design" in system:
                content = json.dumps(replies["classification"])
            elif "You plan one round" in system:
                content = json.dumps(replies["plan"])
            else:
                content = "Model answer about this session."
            return SimpleNamespace(content=content)

    deps.settings = deps.settings.model_copy(update={"anthropic_api_key": SecretStr("fake-key")})
    monkeypatch.setattr(llm, "build_llm", lambda *args, **kwargs: Model())
    return replies


@pytest.fixture
def app(deps):
    return build_graph(deps, checkpointer=InMemorySaver())


def _config(thread="t1"):
    return {"configurable": {"thread_id": thread}}


async def _send(app, text, *, session="s1", thread="t1"):
    """One turn, built exactly as `app.py` builds it.

    This must not thread the previous turn's state back in: doing so hid a bug
    where the real payload blanked every `replace`-reduced channel, so a session
    lost its reference design on turn 2. The `app` fixture has a checkpointer —
    continuity is its job, not the caller's.
    """
    payload = {
        "messages": [{"role": "user", "content": text}],
        "session_id": session,
        "pending_results": {},
        "status": "",
        "trace": [],
    }
    return await app.ainvoke(payload, config=_config(thread))


# --- reducers --------------------------------------------------------------


def test_merge_artifacts_dedupes_by_id_keeping_last():
    left = [{"id": "a", "title": "one"}, {"id": "b", "title": "two"}]
    right = [{"id": "a", "title": "one-updated"}, {"id": "c", "title": "three"}]
    out = merge_artifacts(left, right)
    assert [a["id"] for a in out] == ["a", "b", "c"]
    assert out[0]["title"] == "one-updated"


def test_merge_worklist_upserts_status():
    left = [{"id": "w1", "status": "pending"}]
    right = [{"id": "w1", "status": "done"}, {"id": "w2", "status": "pending"}]
    out = merge_worklist(left, right)
    assert out[0]["status"] == "done"
    assert len(out) == 2


# --- coordinator classification -------------------------------------------


#: Phrasings and the intent they must produce with nothing loaded yet. Named so
#: the `llm` tier can replay the same table through CLASSIFY_SYSTEM and report
#: where the two classifiers disagree — see tests/test_llm_live.py.
CASES_WITHOUT_REFERENCE = [
    ("hello there", "chat"),
    ("what is the lead design?", "chat"),
    ("load 1UBQ", "initialize"),
    ("redesign 1UBQ for thermostability", "initialize"),
    ("write me a report", "summarize"),
    ("which design scored best?", "chat"),
    ("how does pLDDT work?", "chat"),
    ("tell me about the ensemble", "chat"),
    ("summarize the session", "summarize"),
]

#: The same, with a reference design in state.
CASES_WITH_REFERENCE = [
    ("make it more stable", "design"),
    ("improve solubility", "design"),
    ("propose some variants", "design"),
    ("what mutations did you try?", "chat"),
    ("is the lead design better?", "chat"),
    ("can you redesign it for stability", "design"),
    ("show me the structure", "visualize"),
    ("highlight the active site", "visualize"),
    # Phrasings that used to fall through to chat.
    ("label the active site residues in the visualization", "visualize"),
    ("add labels to the catalytic residues", "visualize"),
    ("zoom in on residue 147", "visualize"),
    ("rotate it and show the surface", "visualize"),
    ("run another round", "design"),
    ("iterate on the lead", "design"),
    # ...without swallowing the design request that mentions a residue.
    ("mutate residue 42 to valine", "design"),
    ("what happened in that run?", "chat"),
]


@pytest.mark.parametrize("text,expected", CASES_WITHOUT_REFERENCE)
def test_classify_rules_without_reference(text, expected):
    assert classify_rules(text, new_state("s"))["intent"] == expected


@pytest.mark.parametrize("text,expected", CASES_WITH_REFERENCE)
def test_classify_rules_questions_versus_requests(text, expected):
    state = {**new_state("s"), "reference_design": {"sequence": REF_SEQ}}
    assert classify_rules(text, state)["intent"] == expected


def test_classify_rules_with_reference_loaded():
    state = {**new_state("s"), "reference_design": {"sequence": REF_SEQ}}
    assert classify_rules("make it more stable", state)["intent"] == "design"
    assert classify_rules("show me the structure", state)["intent"] == "visualize"
    assert classify_rules("apply A42V", state)["mutations"] == ["A42V"]


def test_describe_state_without_reference_asks_for_a_target():
    text = describe_state(new_state("s"))
    assert "No reference design" in text


# --- chat path ------------------------------------------------------------


async def test_plain_chat_ends_without_running_tasks(app, stub_tools):
    out = await _send(app, "hello")
    assert out["intent"] == "chat"
    assert stub_tools == []  # nothing was looked up
    assert out["messages"][-1].content


# --- initialization path --------------------------------------------------


async def test_initialize_builds_reference_design(app, stub_tools, deps):
    out = await _send(app, "load PDB 1UBQ")
    ref = out["reference_design"]
    assert ref["pdb_id"] == "1UBQ"
    assert ref["sequence"] == REF_SEQ
    assert ref["uniprot_id"] == "P0CG48"
    assert ref["structure_path"].endswith(".pdb")
    assert ref["literature"][0]["title"] == "Stabilizing ubiquitin"
    assert "pdb_lookup" in stub_tools and "literature_lookup" in stub_tools
    # tier 1 recorded the campaign and its reference
    assert deps.history.graph.campaigns()[0]["id"] == "s1"


async def test_unidentifiable_target_reports_instead_of_crashing(app, stub_tools, monkeypatch):
    async def nothing(**kw):
        return {"error": "no match"}

    for name in ("pdb_lookup", "uniprot_lookup"):
        CATALOG[name] = TaskDef(name, nothing, "query", "stub")

    out = await _send(app, "redesign the flux capacitor protein")
    assert "could not identify" in out["messages"][-1].content.lower()
    assert not out.get("reference_design", {}).get("sequence")


async def test_state_survives_into_the_next_turn(app, stub_tools):
    """app.py used to spread a fresh new_state() into every turn, blanking this.

    Every `replace`-reduced channel was overwritten with its empty default, so
    the second prompt of a session saw no reference design: it answered "No
    reference design is loaded yet" and re-ran the whole initializer.
    """
    await _send(app, "load PDB 1UBQ")
    out = await _send(app, "what is loaded?")

    assert out["reference_design"]["pdb_id"] == "1UBQ"
    assert "No reference design" not in out["messages"][-1].content
    assert stub_tools.count("pdb_lookup") == 1  # the initializer did not re-run


# --- full design loop -----------------------------------------------------


async def test_design_loop_produces_lead_ensemble_and_artifacts(app, stub_tools, deps):
    out = await _send(app, "redesign 1UBQ to improve thermostability")

    assert out["key_metric"]["name"] == "plddt"
    assert out["key_metric"]["direction"] == "max"

    ensemble = out["ensemble"]
    assert len(ensemble) >= 2
    # ranked best-first on the key metric
    values = [d["metrics"]["plddt"] for d in ensemble if "plddt" in d.get("metrics", {})]
    assert values == sorted(values, reverse=True)

    lead = out["lead_design"]
    assert lead["design_id"] == ensemble[0]["design_id"]
    assert lead["mutations"]  # it differs from the reference

    # the visualization and the summary artifacts were registered
    kinds = {a["kind"] for a in out["artifacts"]}
    assert {"molstar", "markdown", "docx", "table"} <= kinds
    assert out["design_summary"]

    # every design reached tier 1 and tier 2
    designs = deps.history.graph.designs_in_campaign("s1")
    assert len(designs) == len(ensemble)
    stored = deps.history.scores.scores_for_design(lead["design_id"])
    assert stored["plddt"] == lead["metrics"]["plddt"]

    # tier 3 golden set was staged
    assert deps.history.golden.list_sets()[0]["n_rows"] >= 1


async def test_explicit_mutations_are_applied(app, stub_tools):
    out = await _send(app, "redesign 1UBQ applying T55V")
    assert out["lead_design"]["mutations"] == ["T55V"]


@pytest.mark.parametrize("decision", [
    {"intent": "chat", "goal": 42},
    {"intent": "chat", "pdb_id": {"id": "1UBQ"}},
    {"intent": "design", "mutations": "T55V"},
    {"intent": "design", "mutations": ["not-a-mutation"]},
    {"intent": "initialize", "uniprot_id": "../../private"},
    {"intent": "execute_shell"},
])
async def test_invalid_model_decision_falls_back_visibly(deps, model_reply, decision):
    from designagent.graph.nodes.coordinator import make_coordinator

    model_reply["classification"] = decision
    out = await make_coordinator(deps)({
        **new_state("s"), "messages": [{"role": "user", "content": "hello"}],
    })

    assert out.goto == "__end__"
    assert out.update["intent"] == "chat"
    assert out.update["requested_mutations"] == []
    assert out.update["target_hints"] == {}
    assert any("expected schema" in warning for warning in out.update["warnings"])
    assert "expected schema" in out.update["messages"][0]["content"]


@pytest.mark.parametrize("plan", [
    {"tasks": ["run_chemgraph"]},
    {"tasks": [{"task": "execute_shell"}]},
    {"tasks": [{"task": "proteinmpnn"}]},
    {"key_metric": {"name": "invented_measurement"}},
    {"key_metric": {"name": "plddt", "direction": "min"}},
    {"key_metric": {"name": "plddt", "target": float("inf")}},
    {"n_variants": True},
    {"n_variants": 1000000},
])
async def test_invalid_model_plan_runs_safe_fallback(deps, stub_tools, model_reply, plan):
    from designagent.graph.nodes.orchestrator import make_orchestrator

    model_reply["plan"] = plan
    out = await make_orchestrator(deps)({
        **new_state("s"), "goal": "improve solubility",
        "reference_design": {"sequence": REF_SEQ},
    })

    assert out.goto == "analyst"
    assert out.update["key_metric"]["name"] == "mean_hydropathy"
    assert out.update["key_metric"]["direction"] == "min"
    assert out.update["pending_results"]["variants"]
    assert {item["task"] for item in out.update["worklist"]} <= {
        "propose_variants", "fold_sequence",
    }
    assert any("expected schema" in warning for warning in out.update["warnings"])


async def test_valid_model_decision_and_plan_reach_requested_tool(
    app, deps, stub_tools, model_reply,
):
    deps.settings = deps.settings.model_copy(update={"max_rounds": 1})
    model_reply["classification"] = {
        "intent": "design", "goal": "redesign 1UBQ applying T55V",
        "pdb_id": "1ubq", "uniprot_id": None, "mutations": ["t55v"],
    }
    model_reply["plan"] = {
        "key_metric": {"name": "sequence_identity", "direction": "max", "target": 0.9},
        "tasks": [{"task": "apply_mutations", "why": "The user named a mutation."}],
        "n_variants": 2,
    }

    out = await _send(app, "redesign 1UBQ applying T55V")

    assert out["target_hints"] == {"pdb_id": "1UBQ"}
    assert out["lead_design"]["mutations"] == ["T55V"]
    assert out["key_metric"]["name"] == "sequence_identity"
    assert out["reply_source"] == "interpreter:llm"
    assert not any("expected schema" in warning for warning in out.get("warnings", []))


async def test_new_design_request_clears_previous_mutations_and_hints(app, stub_tools):
    first = await _send(app, "redesign 1UBQ applying T55V")
    assert first["requested_mutations"] == ["T55V"]
    assert first["target_hints"] == {"pdb_id": "1UBQ"}

    second = await _send(app, "improve solubility")

    assert second["requested_mutations"] == []
    assert second["target_hints"] == {}
    records = second["pending_results"]
    assert not records  # the analyst consumed a complete round
    assert second["ensemble"]
    assert second["round"] > first["round"]


@pytest.mark.parametrize("target", ["6B5X", "P12345"])
async def test_conflicting_target_starts_no_tasks(app, deps, stub_tools, fake_hpc, target):
    loaded = await _send(app, "load 1UBQ")
    before = len(deps.tasks.snapshot())
    out = await _send(app, f"redesign {target} applying T55V")

    assert out["reply_source"] == "coordinator:target_conflict"
    assert "Start a new session" in out["messages"][-1].content
    assert out["reference_design"] == loaded["reference_design"]
    assert not out["requested_mutations"]
    assert len(deps.tasks.snapshot()) == before
    assert not fake_hpc.submitted


@pytest.mark.parametrize("target", ["1UBQ", "P0CG48"])
async def test_matching_loaded_target_can_still_be_redesigned(app, stub_tools, target):
    await _send(app, "load 1UBQ")
    out = await _send(app, f"redesign {target} to improve solubility")

    assert out["reference_design"]["pdb_id"] == "1UBQ"
    assert out["ensemble"]
    assert out["reply_source"] != "coordinator:target_conflict"


@pytest.mark.parametrize("failure_at", ["construction", "invocation"])
async def test_provider_failure_is_visible_without_persisting_its_body(
    app, deps, monkeypatch, caplog, failure_at,
):
    from designagent import llm

    deps.settings = deps.settings.model_copy(update={"anthropic_api_key": SecretStr("fake-key")})
    private = "synthetic-private-provider-request-body"

    def unavailable(*args, **kwargs):
        raise RuntimeError(f"authentication_error: invalid x-api-key fake-key; {private}")

    class Model:
        async def ainvoke(self, *args, **kwargs):
            unavailable()

    builder = unavailable if failure_at == "construction" else lambda *a, **kw: Model()
    monkeypatch.setattr(llm, "build_llm", builder)
    out = await _send(app, "hello")
    saved = await app.aget_state(_config())

    assert out["reply_source"] == "coordinator:describe_state"
    assert out["messages"][-1].content
    assert any("the API key was rejected" in warning for warning in out["warnings"])
    assert "the API key was rejected" in out["messages"][-1].content
    public = repr(out) + repr(saved) + caplog.text + deps.last_llm_error
    assert private not in public
    assert "fake-key" not in public


async def test_round_budget_is_respected(app, stub_tools, deps):
    """max_rounds=2, and the stub always improves, so it must stop at 2."""
    out = await _send(app, "redesign 1UBQ for stability")
    assert out["round"] <= deps.settings.max_rounds


async def test_summary_artifact_contains_the_numbers(app, stub_tools, deps):
    out = await _send(app, "redesign 1UBQ for stability")
    markdown = next(a for a in out["artifacts"] if a["kind"] == "markdown")
    text = deps.artifacts.read_bytes(markdown["id"]).decode()
    assert "Lead design" in text
    assert out["lead_design"]["design_id"] in text
    assert "plddt" in text.lower()


async def test_docx_artifact_is_a_real_docx(app, stub_tools, deps):
    out = await _send(app, "redesign 1UBQ for stability")
    docx = next(a for a in out["artifacts"] if a["kind"] == "docx")
    data = deps.artifacts.read_bytes(docx["id"])
    assert data[:2] == b"PK"  # a zip container
    from io import BytesIO

    from docx import Document

    doc = Document(BytesIO(data))
    assert any("Design summary" in p.text for p in doc.paragraphs)


# --- visualization path ---------------------------------------------------


async def test_visualize_request_builds_a_spec(app, stub_tools, deps):
    await _send(app, "load 1UBQ")
    out = await _send(app, "show me the structure")
    spec = out["molecular_visualization"]
    assert spec["structures"]
    assert spec["representation"] in ("cartoon", "ball-and-stick", "gaussian-surface")
    assert spec["artifact_id"]
    stored = deps.artifacts.read_json(spec["artifact_id"])
    assert stored["title"]


async def test_visualization_highlights_functional_residues(app, stub_tools):
    await _send(app, "load 1UBQ")
    out = await _send(app, "show the active site")
    labels = {h["label"] for h in out["molecular_visualization"]["highlights"]}
    assert "Functional residues" in labels


async def test_a_label_request_changes_the_view(app, stub_tools):
    """The phrasing from the bug report: it used to classify as chat."""
    await _send(app, "load PDB 1UBQ")
    out = await _send(app, "label the active site residues in the visualization")
    assert out["intent"] == "visualize"
    assert out["molecular_visualization"]["highlights"]


# --- failure handling -----------------------------------------------------


async def test_fold_failure_does_not_abort_the_loop(app, stub_tools):
    async def failing_fold(**kw):
        return {"error": "fold service unavailable"}

    CATALOG["fold_sequence"] = TaskDef(
        "fold_sequence", failing_fold, "local", "stub", "structure"
    )
    out = await _send(app, "redesign 1UBQ for stability")
    # The loop still finishes and explains itself.
    assert out["messages"][-1].content
    assert out["design_summary"]


async def test_task_exception_is_recorded_as_failed(app, stub_tools, deps):
    async def exploding(**kw):
        raise RuntimeError("boom")

    CATALOG["fold_sequence"] = TaskDef(
        "fold_sequence", exploding, "local", "stub", "structure"
    )
    await _send(app, "redesign 1UBQ for stability")
    states = {t["state"] for t in deps.history.graph.tasks_for_campaign("s1")}
    assert "FAILED" in states


async def test_lake_write_failure_does_not_lose_the_round(app, stub_tools, deps, monkeypatch):
    """Storage is not allowed to destroy work the user already paid for."""

    def boom(*args, **kwargs):
        raise RuntimeError("attempt to write a readonly database")

    monkeypatch.setattr(deps.history, "record_design", boom)
    monkeypatch.setattr(deps.history, "rank_round", boom)
    monkeypatch.setattr(deps.history, "record_analysis", boom)

    out = await _send(app, "redesign 1UBQ for stability")

    # The designs survived, ranked in memory, and the failure is surfaced.
    assert out["ensemble"]
    assert out["lead_design"]["design_id"]
    values = [d["metrics"]["plddt"] for d in out["ensemble"] if "plddt" in d.get("metrics", {})]
    assert values == sorted(values, reverse=True)
    assert any("Design History write failed" in w for w in out["warnings"])
    # and the user is actually told, not just the log
    assert "Caveats from this run" in out["messages"][-1].content
    assert out["design_summary"]


async def test_structures_are_not_carried_in_state(app, stub_tools, deps):
    """Coordinates belong in blobs; checkpoints must not balloon with PDB text."""
    import json

    out = await _send(app, "redesign 1UBQ for stability")

    # Designs reference a blob path, and the file really holds the coordinates.
    lead = out["lead_design"]
    assert lead["structure_path"]
    assert deps.history.read_blob(lead["structure_path"]).startswith(b"ATOM")

    # No PDB text anywhere in the serialized state.
    blob = json.dumps(
        {k: v for k, v in out.items() if k != "messages"}, default=str
    )
    assert "ATOM  " not in blob
    assert out.get("pending_results") in (None, {})


async def test_a_rejected_key_says_so_instead_of_silently_using_rules(
    app, stub_tools, deps, monkeypatch
):
    """A configured key that fails must not look like ordinary offline mode.

    Without this, the only difference between "no key" and "the key you just
    pasted is wrong" is a line in the server log.
    """
    from designagent import llm as llm_module

    deps.settings = deps.settings.model_copy(
        update={"anthropic_api_key": SecretStr("sk-ant-wrong-0123456789")}
    )

    async def rejected(*args, **kwargs):
        raise RuntimeError("authentication_error: invalid x-api-key")

    monkeypatch.setattr(llm_module, "build_llm", lambda *a, **k: _Unusable(rejected))

    out = await _send(app, "redesign 1UBQ for stability")

    assert any("rule-based path" in w for w in out["warnings"])
    assert "Caveats from this run" in out["messages"][-1].content
    assert "the API key was rejected" in deps.last_llm_error
    # The run still produced its work: degradation, not failure.
    assert out["design_summary"]
    assert out["reply_source"] == "interpreter:_rule_based_summary"


class _Unusable:
    """A chat model stand-in whose every call raises."""

    def __init__(self, ainvoke):
        self.ainvoke = ainvoke


# --- the turn's trace ------------------------------------------------------


async def test_a_turn_records_the_path_it_took(app, stub_tools):
    """The question this answers: which node produced the text I just read?"""
    out = await _send(app, "redesign 1UBQ for stability")

    path = [e["node"] for e in out["trace"]]
    assert path[0] == "coordinator"
    assert path[-1] == "interpreter"
    assert "orchestrator" in path and "analyst" in path

    # Every entry carries its own timing and where it routed next.
    assert all(isinstance(e["ms"], int) for e in out["trace"])
    assert out["trace"][0]["goto"] in ("initializer", "orchestrator")
    assert out["trace"][-1]["goto"] == "end"

    # And the reply is attributed to the function that wrote it.
    author = [e.get("reply_source") for e in out["trace"] if e.get("reply_source")]
    assert author[-1] == "interpreter:_rule_based_summary"
    assert out["reply_source"] == "interpreter:_rule_based_summary"


async def test_the_trace_is_turn_scoped(app, stub_tools):
    """A session's checkpoint holds this turn's path, not every turn's."""
    await _send(app, "load PDB 1UBQ")
    # Deliberately not "what is loaded?", which misroutes to design — see
    # "a question containing a past participle" in plans/BACKLOG.md.
    out = await _send(app, "what is the lead design?")

    # `_send` mirrors app.py, which resets `trace` the way it resets
    # pending_results — so turn 2 must not carry turn 1's initializer.
    assert [e["node"] for e in out["trace"]] == ["coordinator"]
    assert out["trace"][0]["reply_source"] == "coordinator:describe_state"


async def test_a_trace_entry_holds_counters_not_payloads(app, stub_tools):
    """A trace that quoted what it describes would double every checkpoint."""
    out = await _send(app, "redesign 1UBQ for stability")

    allowed = {
        "node", "ms", "goto", "intent", "round", "reply_source", "status",
        "n_messages", "n_warnings", "n_artifacts", "n_ensemble", "n_worklist",
    }
    for entry in out["trace"]:
        assert set(entry) <= allowed, set(entry) - allowed
        assert "ATOM  " not in repr(entry)


async def test_a_task_records_which_node_submitted_it(app, stub_tools, deps):
    """Previously inferable only from the task's name, and ambiguous."""
    await _send(app, "redesign 1UBQ for stability")

    by_node: dict[str, set[str]] = {}
    for task in deps.tasks.snapshot():
        by_node.setdefault(task["node"], set()).add(task["task"])

    assert by_node["initializer"] >= {"pdb_lookup", "pdb_structure"}
    assert "propose_variants" in by_node["orchestrator"]
    assert "" not in by_node  # nothing escaped the node context


# --- the ProteinMPNN path, via a fake endpoint ------------------------------
#
# The only place the whole chain is provable without a broker: routing to `hpc`,
# the staged FASTA, the adapter, the metric, and the ranking.


def _mpnn_stdout(n: int = 3, *, model: str = "v_48_020") -> str:
    """What a finished ProteinMPNN job stages back, framed as the protocol does."""
    import base64
    import gzip
    import hashlib

    header = f"model_name={model}, git_hash=deadbeef, " if model else ""
    lines = [
        f">1ubq, score=1.0383, global_score=1.0383, designed_chains=['A'], "
        f"{header}seed=37",
        REF_SEQ,
    ]
    # Each sample mutates a different position, so mutations are multi-position
    # in aggregate and never collide with the heuristic's single substitutions.
    for i in range(1, n + 1):
        seq = list(REF_SEQ)
        seq[i] = "A" if seq[i] != "A" else "G"
        seq[i + 20] = "W"
        lines.append(
            f">T=0.1, sample={i}, score={0.8 + i / 100:.4f}, "
            f"global_score=0.9, seq_recovery=0.64"
        )
        lines.append("".join(seq))
    fasta = "\n".join(lines) + "\n"

    raw = fasta.encode()
    name = base64.urlsafe_b64encode(b"seqs/in.fa").decode().rstrip("=")
    body = base64.b64encode(gzip.compress(raw)).decode()
    return "\n".join([
        f"<<<ORBIT_ARTIFACT name={name} bytes={len(raw)} "
        f"sha256={hashlib.sha256(raw).hexdigest()}>>>",
        body,
        "<<<ORBIT_ARTIFACT_END>>>",
        f"<<<ORBIT_MANIFEST files=1 bytes={len(raw)} skipped=0>>>",
    ]) + "\n"


@pytest.fixture
def fake_hpc(deps):
    """Attach an `hpc` interface that returns a canned job result.

    It goes through `OrbitInterface._demultiplex` so the framing is exercised
    rather than bypassed; only the broker is replaced.
    """
    import asyncio

    from designagent.tasks.base import Capabilities, TaskState
    from designagent.tasks.hpc.orbit import OrbitInterface

    class FakeOrbit(OrbitInterface):
        name = "hpc"
        # No log stream: there is no file to tail, and the drain would only
        # poll a method that cannot answer.
        capabilities = Capabilities(
            supports_cancel=True, supports_push_events=False, supports_staging=True
        )

        def __init__(self, stdout: str):
            super().__init__()
            self.stdout = stdout
            self.submitted: list[dict] = []

        @property
        def connected(self) -> bool:
            return True

        async def submit(self, spec):
            self.submitted.append(spec.params.get("job_spec") or {})
            future: asyncio.Future = asyncio.get_running_loop().create_future()
            handle = self._handle(spec, future, task_id=f"fake-{len(self.submitted)}")
            handle.state = TaskState.QUEUED
            data = self._demultiplex(
                {"state": "DONE", "exit_code": 0, "stdout": self.stdout, "stderr": ""},
                False,
            )
            self._finish_job(handle, data, TaskState.DONE)
            return handle

        async def close(self) -> None:
            return None

    iface = FakeOrbit(_mpnn_stdout())
    deps.tasks.attach_hpc(iface)
    return iface


async def test_a_real_mpnn_job_produces_ranked_designs(app, stub_tools, deps, fake_hpc):
    waiting = await _send(app, "redesign 1UBQ to improve thermostability")
    assert waiting["hpc_pending"]
    assert not fake_hpc.submitted
    out = await _send(app, "approve hpc")

    assert out["ensemble"], "the round produced no designs"
    sources = {d.get("provenance", {}).get("source") for d in out["ensemble"]}
    assert "proteinmpnn" in sources, sources
    # The structure travelled with the job rather than as a local path.
    assert "in.pdb" in fake_hpc.submitted[0]["inputs"]
    assert fake_hpc.submitted[0]["outputs"] == ["seqs/*.fa"]
    # mpnn_score reached a metric, which it could not before.
    assert any("mpnn_score" in d.get("metrics", {}) for d in out["ensemble"])
    # ProteinMPNN mutates several positions; the heuristic only ever one.
    assert any(len(d.get("mutations") or []) > 1 for d in out["ensemble"])


async def test_the_mpnn_task_is_recorded_as_hpc_in_the_ledger(app, stub_tools, deps, fake_hpc):
    """The row this whole change exists to make true."""
    await _send(app, "redesign 1UBQ to improve thermostability")
    await _send(app, "approve hpc")
    tasks = deps.history.graph.tasks_for_campaign("s1")
    mpnn = [t for t in tasks if t["name"] == "proteinmpnn"]
    assert mpnn, [t["name"] for t in tasks]
    assert mpnn[0]["interface"] == "hpc"
    assert mpnn[0]["state"] == "DONE"


async def test_the_staged_fasta_is_not_carried_in_state(app, stub_tools, deps, fake_hpc):
    """Same rule as coordinates: a round of sequences travels by blob path."""
    import json

    await _send(app, "redesign 1UBQ to improve thermostability")
    out = await _send(app, "approve hpc")
    blob = json.dumps(out, default=str)
    assert ">T=" not in blob, "FASTA records leaked into state"
    assert "ORBIT_ARTIFACT" not in blob, "framing leaked into state"


async def test_unreadable_mpnn_output_falls_back_and_says_so(app, stub_tools, deps, fake_hpc):
    """A round the user waited on must not die silently."""
    fake_hpc.stdout = "ProteinMPNN: CUDA out of memory\n"
    await _send(app, "redesign 1UBQ to improve thermostability")
    out = await _send(app, "approve hpc")

    assert out["ensemble"], "the round should survive on the heuristic"
    sources = {d.get("provenance", {}).get("source") for d in out["ensemble"]}
    assert sources == {"heuristic"} or "heuristic" in sources
    assert any("ProteinMPNN" in w for w in out["warnings"]), out["warnings"]
    reply = out["messages"][-1].content
    assert "No candidate designs were produced" not in reply
    assert "Caveats from this run" in reply


async def test_output_with_no_model_name_is_flagged(app, stub_tools, deps, fake_hpc):
    """Provenance is read, not assumed, so a run that cannot say is reported."""
    fake_hpc.stdout = _mpnn_stdout(model="")
    await _send(app, "redesign 1UBQ to improve thermostability")
    out = await _send(app, "approve hpc")
    assert any("declared no model name" in w for w in out["warnings"]), out["warnings"]
