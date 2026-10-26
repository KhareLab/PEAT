"""The agent's shared state.

TypedDict rather than pydantic: LangGraph checkpoints it to SQLite and partial
dict updates from nodes are the natural write unit.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import add_messages

Intent = Literal["chat", "initialize", "design", "summarize", "visualize", "protocol"]
WorkStatus = Literal["pending", "running", "done", "failed", "canceled"]


# --- pieces of state -------------------------------------------------------


class LiteratureRef(TypedDict, total=False):
    id: str  # e.g. "PMC123" / "12345678"
    source: str  # europepmc | pubmed
    title: str
    year: str
    doi: str
    abstract: str
    relevance: str  # why the initializer kept it


class ReferenceDesign(TypedDict, total=False):
    """The starting point a redesign is measured against."""

    name: str
    pdb_id: str
    uniprot_id: str
    sequence: str
    length: int
    chains: list[dict[str, Any]]  # [{chain_id, entity, length}]
    ligands: list[dict[str, Any]]  # [{comp_id, name}]
    structure_path: str  # blob path of the downloaded coordinates
    structure_format: Literal["pdb", "mmcif"]
    organism: str
    function: str
    literature: list[LiteratureRef]
    notes: str
    # UniProt functional features: [{type, start, end, description}]. The
    # visualization path reads these for active, binding and metal sites.
    features: list[dict[str, Any]]
    method: str  # experimental method, from the PDB entry
    resolution: float | None
    # Set only when the PDB construct and the UniProt canonical disagree, so
    # the difference is recorded rather than silently resolved.
    canonical_sequence: str


class KeyMetric(TypedDict, total=False):
    """What "better" means for this campaign."""

    name: str  # plddt | rmsd_to_reference | mpnn_score | ...
    direction: Literal["max", "min"]
    target: float | None
    description: str


class WorkItem(TypedDict, total=False):
    id: str
    interface: Literal["local", "query", "hpc"]
    task: str  # registry name, e.g. "fold_sequence"
    params: dict[str, Any]
    status: WorkStatus
    handle_id: str | None
    depends_on: list[str]
    error: str | None


class Design(TypedDict, total=False):
    design_id: str
    sequence: str
    parent_id: str | None
    mutations: list[str]  # ["A42V", ...] relative to the reference
    metrics: dict[str, float]
    structure_path: str
    provenance: dict[str, Any]  # {task, handle_id, round}


class Highlight(TypedDict, total=False):
    chain: str
    residues: list[int]
    color: str  # #rrggbb
    label: str
    representation: str


class MolVisualization(TypedDict, total=False):
    """A declarative view spec; the frontend turns it into Mol* calls."""

    title: str
    structures: list[dict[str, Any]]  # [{source: "pdb"|"artifact", value, format}]
    highlights: list[Highlight]
    representation: str  # cartoon | ball-and-stick | gaussian-surface
    focus: dict[str, Any] | None
    artifact_id: str | None
    caption: str


class ArtifactRef(TypedDict, total=False):
    id: str
    kind: Literal["markdown", "docx", "molstar", "table", "json"]
    title: str
    url: str
    created_at: str


class ProtocolState(TypedDict, total=False):
    """A multi-turn enzyme-redesign campaign, between its stages.

    One stage runs per turn. The three places the skill says to STOP and wait
    for the user are expressed as turn boundaries: a stage that needs an answer
    sets `awaiting` and returns, and the next message answers it. That is why
    this has to survive the checkpoint intact — and why `coordinator` routes
    here on `awaiting` *before* classifying intent, since "yes, 310 and 364"
    classifies as `chat`.

    Numbers only. The trimmed structure, the FASTAs and the CSVs go to
    `history.write_blob` and travel as paths, like everything else
    (`test_structures_are_not_carried_in_state`). Residue positions and job ids
    are small and are the whole point of the record.

    Every position here is in the **trimmed** chain's numbering; `offset` says
    what it was shifted by. See `protocol/trim.py`.
    """

    stage: str  # the stage that runs next; see graph/nodes/protocol.py STAGES
    awaiting: str  # what the user has to answer before `stage` may run
    name: str
    uniprot: str
    netid: str
    domains: str  # the trimmed domain string
    offset: int  # signal-peptide length, 0 if the chain was already mature
    cat_res: list[int]
    tag: str  # "" for cpos, "liu_" for conservation_liu
    method: str
    levels: list[int]
    model_stem: str  # names almost every file in the campaign
    structure_path: str  # blob path of the trimmed PDB
    notebook_id: str  # the NOTEBOOK.md artifact, appended to every stage
    # Scheduler ids of jobs submitted and not yet accounted for. In-memory
    # handles do not survive a restart, so these are what `sacct` is asked
    # about, and AlphaFold3 progress is read from the filesystem rather than
    # from a handle. See graph/nodes/protocol.py.
    native_ids: list[str]
    designs: list[str]  # AlphaFold3 job names, one per selected design
    installed: bool  # whether $PROJ exists; every compute stage needs it first


# --- reducers --------------------------------------------------------------


def merge_artifacts(
    left: list[ArtifactRef] | None, right: list[ArtifactRef] | None
) -> list[ArtifactRef]:
    """Append, keeping the last write for any repeated id and preserving order."""
    out: list[ArtifactRef] = []
    seen: dict[str, int] = {}
    for item in [*(left or []), *(right or [])]:
        aid = item.get("id")
        if aid and aid in seen:
            out[seen[aid]] = item
        else:
            if aid:
                seen[aid] = len(out)
            out.append(item)
    return out


def merge_worklist(
    left: list[WorkItem] | None, right: list[WorkItem] | None
) -> list[WorkItem]:
    """Upsert work items by id so the analyst can update status in place."""
    out: list[WorkItem] = list(left or [])
    index = {w["id"]: i for i, w in enumerate(out) if w.get("id")}
    for item in right or []:
        wid = item.get("id")
        if wid and wid in index:
            out[index[wid]] = {**out[index[wid]], **item}
        else:
            if wid:
                index[wid] = len(out)
            out.append(item)
    return out


def replace(_left: Any, right: Any) -> Any:
    """Last writer wins (the default, stated explicitly for clarity)."""
    return right


def merge_warnings(left: list[str] | None, right: list[str] | None) -> list[str]:
    """Accumulate non-fatal problems, keeping order and dropping repeats."""
    out: list[str] = []
    for item in [*(left or []), *(right or [])]:
        if item and item not in out:
            out.append(item)
    return out[-20:]



# --- the state -------------------------------------------------------------


class DesignState(TypedDict, total=False):
    messages: Annotated[list, add_messages]

    reference_design: Annotated[ReferenceDesign, replace]
    key_metric: Annotated[KeyMetric, replace]
    worklist: Annotated[list[WorkItem], merge_worklist]
    lead_design: Annotated[Design, replace]
    ensemble: Annotated[list[Design], replace]
    molecular_visualization: Annotated[MolVisualization, replace]
    artifacts: Annotated[list[ArtifactRef], merge_artifacts]
    design_summary: Annotated[str, replace]
    # Which node and which function authored the last assistant message, e.g.
    # "interpreter:_rule_based_summary" or "coordinator:llm". The reply reads the
    # same whichever path produced it, so without this there is no way to tell
    # from the outside whether a configured key was used, or to find the code
    # that wrote a given sentence. Grep the value.
    reply_source: Annotated[str, replace]

    # control
    intent: Annotated[Intent, replace]
    round: Annotated[int, replace]
    session_id: Annotated[str, replace]
    goal: Annotated[str, replace]  # the design goal in natural language
    status: Annotated[str, replace]  # short human-readable progress line
    # Non-fatal problems worth telling the user about (failed lake writes,
    # unavailable services). Survives the turn, unlike `status`.
    warnings: Annotated[list[str], merge_warnings]
    # Identifiers the coordinator pulled out of the prompt, so the initializer
    # does not have to re-parse it.
    target_hints: Annotated[dict[str, str], replace]
    # Mutations the user asked for by name, e.g. ["A42V"].
    requested_mutations: Annotated[list[str], replace]
    # An enzyme-redesign campaign in progress, one stage per turn. `replace`
    # like everything else, so a stage returns the whole dict rather than a
    # delta — see `ProtocolState` for why it has to survive the checkpoint.
    protocol: Annotated[ProtocolState, replace]
    hpc_pending: Annotated[dict[str, Any], replace]
    hpc_approval: Annotated[dict[str, Any], replace]
    # Raw task records passed from the orchestrator to the analyst for one
    # round. Cleared once the analyst has consumed them.
    pending_results: Annotated[dict[str, Any], replace]
    # This turn's node path. `replace`, not an accumulating reducer, even though
    # it grows: an accumulator cannot be reset, because `[*saved, *[]]` keeps the
    # saved entries, and this must start empty every turn. The wrapper in
    # `build.py` appends to what it reads instead, which is correct only because
    # nodes run one at a time here — a fan-out node would need the reducer back,
    # plus a reset sentinel. See graph/trace.py.
    trace: Annotated[list[dict], replace]


def last_user_text(state: DesignState) -> str:
    """The most recent human turn's text, however the message is shaped.

    Messages arrive either as LangChain objects or as plain dicts (the API posts
    dicts), and content is a string or a list of content blocks. Nodes other
    than the coordinator need this too: a view request's own words are the only
    place the user says what to show.
    """
    for message in reversed(state.get("messages") or []):
        role = getattr(message, "type", None) or (
            message.get("role") if isinstance(message, dict) else None
        )
        if role in ("human", "user"):
            content = getattr(message, "content", None)
            if content is None and isinstance(message, dict):
                content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return " ".join(
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict)
                )
    return ""


def new_state(session_id: str) -> DesignState:
    return DesignState(
        messages=[],
        reference_design={},
        key_metric={},
        worklist=[],
        lead_design={},
        ensemble=[],
        molecular_visualization={},
        artifacts=[],
        design_summary="",
        reply_source="",
        intent="chat",
        round=0,
        session_id=session_id,
        goal="",
        status="",
        target_hints={},
        requested_mutations=[],
        hpc_pending={},
        hpc_approval={},
        pending_results={},
        warnings=[],
        trace=[],
    )
