"""Coordinator: the chat interface node.

Catches the prompt, decides what the agent should do next, and answers directly
when no new computation is needed. The LLM is optional: without a key the
classifier falls back to keyword rules, which keeps the whole loop usable.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator

from ...llm import complete, complete_json
from ...tools.pdb import find_pdb_ids
from ...tools.uniprot import find_accessions
from ..deps import Deps, llm_caveat, status
from ..state import DesignState, Intent, last_user_text

log = logging.getLogger(__name__)

CLASSIFY_SYSTEM = """You route a protein-design assistant's next step.

Choose exactly one intent:
  initialize - the user named a protein/PDB/UniProt target to start work on and
               no reference design is loaded yet
  design     - the user wants variants proposed, folded, scored or optimized
  visualize  - the user wants to see a structure or a view changed
  summarize  - the user wants a summary, report or document of the work so far
  protocol   - the user asked for the full enzyme redesign/stabilization
               protocol: the multi-day cluster pipeline (AFDB -> FoldSeek ->
               HHblits conservation -> ProteinMPNN/HaloMPNN -> scoring ->
               AlphaFold3). Not an ordinary design round.
  chat       - anything else: questions about existing results, clarification,
               general conversation

Also extract:
  goal: the design objective in one phrase, if any
  pdb_id / uniprot_id: if explicitly named
  mutations: explicit mutations the user asked for, e.g. ["A42V"]

Return JSON: {"intent": ..., "goal": ..., "pdb_id": ..., "uniprot_id": ..., "mutations": [...]}"""

ANSWER_SYSTEM = """You are a protein engineering assistant. Answer the user using
only the session state provided. Be specific and concise; cite design ids and
metric values where relevant. If the state does not contain the answer, say what
is missing and suggest the next step. Do not invent metrics or structures."""

# Keyword rules, used when no LLM is configured.
#
# Verbs that ask for work to be done. "design" is deliberately absent: it is a
# noun as often as a verb ("what is the lead design?"), so it lives in
# DESIGN_NOUNS where it cannot by itself trigger a redesign.
ACTION_WORDS = (
    "redesign", "optimize", "optimise", "improve", "mutate", "stabilize",
    "stabilise", "engineer", "fold", "predict", "propose", "generate", "sample",
    "increase", "decrease", "raise", "lower", "maximize", "minimise",
    "minimize", "maximise", "make", "build", "create", "load", "fetch",
    # Asking for more of the same work. Bare "run" is deliberately absent: the
    # is_question branch below requires `not wants_action`, so it would turn
    # "what happened in that run?" into a redesign request. "run another round"
    # is caught by `round` in DESIGN_NOUNS instead.
    "rerun", "continue", "iterate",
)
# Note on all three lists: _has_word matches a leading word boundary only, so
# an entry is a prefix. "fold" covers "folding", but "optimize" does not cover
# "optimizing" — the -ing form drops the final e. Add the form that matters.
DESIGN_NOUNS = ("design", "variant", "mutant", "mutation", "redesign", "round")
# What a request to change the view looks like. "residue" is deliberately
# absent: "mutate residue 42" is a design request, not a view change.
VIZ_WORDS = (
    "show", "visualize", "visualise", "render", "view", "display", "highlight",
    "color", "colour", "label", "annotate", "zoom", "focus", "rotate", "spin",
    "hide", "select", "cartoon", "surface", "sticks",
)
SUMMARY_WORDS = (
    "summary", "summarize", "summarise", "report", "write up", "writeup",
    "document", "docx",
)
# Openers that make an utterance a question about existing results rather than
# a request for new work.
INTERROGATIVE = (
    "what", "which", "why", "how", "when", "where", "who", "whose", "is",
    "are", "was", "were", "does", "do", "did", "tell", "explain", "describe",
    "list",
)
MUTATION_RE = re.compile(r"\b([ACDEFGHIKLMNPQRSTVWY]\d{1,4}[ACDEFGHIKLMNPQRSTVWY])\b")


class RoutingDecision(BaseModel):
    model_config = ConfigDict(strict=True)

    intent: Intent
    goal: str | None = None
    pdb_id: str | None = None
    uniprot_id: str | None = None
    mutations: list[str] | None = None

    @field_validator("pdb_id", "uniprot_id")
    @classmethod
    def identifier(cls, value: str | None, info: ValidationInfo) -> str | None:
        if not value:
            return value
        value = value.strip().upper()
        find = find_pdb_ids if info.field_name == "pdb_id" else find_accessions
        if find(value) != [value]:
            raise ValueError("expected a single protein identifier")
        return value

    @field_validator("mutations")
    @classmethod
    def mutation_list(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        if any(MUTATION_RE.fullmatch(m.upper()) is None for m in value):
            raise ValueError("expected mutations such as A42V")
        return [m.upper() for m in value]


def _has_word(text: str, words: tuple[str, ...]) -> bool:
    """Whole-word match, so "design" does not fire on "designed by"."""
    return any(re.search(rf"\b{re.escape(word)}", text) for word in words)


# Asking for the full enzyme-redesign protocol, which is a different thing from
# a design round: it runs on the cluster, takes days, and stops for the user
# three times. Nothing here overlaps ACTION_WORDS, so an ordinary "redesign this
# lipase" still means one round through the orchestrator.
PROTOCOL_WORDS = (
    "enzyme redesign protocol",
    "redesign protocol",
    "stabilization protocol",
    "stabilisation protocol",
    "full protocol",
    "run the protocol",
    "hhblits",
    "halompnn",
    "conservation protocol",
)

# How a user leaves a campaign that is waiting on them. Explicit, because the
# alternative is being unable to ask anything else until the protocol finishes.
ESCAPE_WORDS = (
    "cancel the protocol",
    "stop the protocol",
    "abandon the protocol",
    "forget the protocol",
    "quit the protocol",
    "exit the protocol",
)


def _wants_protocol(text: str, protocol: dict[str, Any]) -> bool:
    """Whether to start or resume the full protocol."""
    low = text.lower()
    if any(phrase in low for phrase in PROTOCOL_WORDS):
        return True
    # An campaign mid-flight but not waiting on an answer: "continue", "carry
    # on", "what is the status" all mean the next stage.
    stage = protocol.get("stage", "")
    if stage and stage != "done":
        return bool(
            re.search(r"\b(continue|carry on|resume|next stage|keep going)\b", low)
        )
    return False


def _wants_out(text: str) -> bool:
    low = text.lower()
    return any(phrase in low for phrase in ESCAPE_WORDS)


def classify_rules(text: str, state: DesignState) -> dict[str, Any]:
    """Deterministic intent classification."""
    low = text.lower().strip()
    pdb_ids = find_pdb_ids(text)
    accessions = find_accessions(text)
    mutations = MUTATION_RE.findall(text.upper())
    has_reference = bool((state.get("reference_design") or {}).get("sequence"))

    first_word = (low.split() or [""])[0].rstrip(",:")
    is_question = low.endswith("?") or first_word in INTERROGATIVE
    wants_action = _has_word(low, ACTION_WORDS)

    if _has_word(low, SUMMARY_WORDS):
        intent = "summarize"
    elif _has_word(low, VIZ_WORDS) and has_reference:
        intent = "visualize"
    elif is_question and not wants_action and not mutations:
        # A question about existing results, e.g. "what is the lead design?"
        intent = "chat"
    elif mutations or wants_action or _has_word(low, DESIGN_NOUNS):
        intent = "initialize" if not has_reference and (pdb_ids or accessions) else "design"
    elif (pdb_ids or accessions) and not has_reference:
        intent = "initialize"
    else:
        intent = "chat"

    return {
        "intent": intent,
        "goal": text if intent in ("design", "initialize") else "",
        "pdb_id": pdb_ids[0] if pdb_ids else "",
        "uniprot_id": accessions[0] if accessions else "",
        "mutations": mutations,
    }


def state_digest(state: DesignState) -> dict[str, Any]:
    """A compact view of state for the LLM, with no bulky fields."""
    reference = state.get("reference_design") or {}
    lead = state.get("lead_design") or {}
    return {
        "goal": state.get("goal", ""),
        "round": state.get("round", 0),
        "reference": {
            "pdb_id": reference.get("pdb_id"),
            "uniprot_id": reference.get("uniprot_id"),
            "name": reference.get("name"),
            "organism": reference.get("organism"),
            "length": reference.get("length"),
            "n_literature": len(reference.get("literature") or []),
        },
        "key_metric": state.get("key_metric") or {},
        "lead_design": {
            "design_id": lead.get("design_id"),
            "mutations": lead.get("mutations", []),
            "metrics": lead.get("metrics", {}),
        },
        "ensemble": [
            {
                "design_id": d.get("design_id"),
                "mutations": d.get("mutations", []),
                "metrics": d.get("metrics", {}),
            }
            for d in (state.get("ensemble") or [])[:10]
        ],
        "design_summary": (state.get("design_summary") or "")[:1500],
        "artifacts": [
            {"kind": a.get("kind"), "title": a.get("title")}
            for a in (state.get("artifacts") or [])
        ],
    }


def describe_state(state: DesignState) -> str:
    """A rule-based answer, used when no LLM is configured."""
    reference = state.get("reference_design") or {}
    lead = state.get("lead_design") or {}
    ensemble = state.get("ensemble") or []
    metric = (state.get("key_metric") or {}).get("name", "")
    parts: list[str] = []

    if reference.get("pdb_id") or reference.get("uniprot_id"):
        ident = reference.get("pdb_id") or reference.get("uniprot_id")
        name = reference.get("name") or ""
        length = reference.get("length")
        parts.append(
            f"Reference design: {ident}"
            + (f" ({name})" if name else "")
            + (f", {length} residues" if length else "")
            + "."
        )
    else:
        parts.append(
            "No reference design is loaded yet. Name a PDB entry, a UniProt "
            "accession, or a protein to start."
        )

    if metric:
        direction = (state.get("key_metric") or {}).get("direction", "max")
        parts.append(f"Key metric: {metric} ({'maximize' if direction == 'max' else 'minimize'}).")

    if lead.get("design_id"):
        metrics = ", ".join(f"{k}={v}" for k, v in (lead.get("metrics") or {}).items())
        muts = ", ".join(lead.get("mutations") or []) or "none"
        parts.append(f"Lead design {lead['design_id']}: mutations {muts}; {metrics}.")

    if ensemble:
        parts.append(f"Ensemble holds {len(ensemble)} design(s).")

    if state.get("design_summary"):
        parts.append(state["design_summary"])

    return " ".join(parts)


def make_coordinator(deps: Deps):
    async def coordinator(state: DesignState) -> Command:
        text = last_user_text(state)
        status("Reading your request…", node="coordinator")

        # A redesign campaign waiting on an answer owns the turn, whatever the
        # message looks like. This has to come before classification: "liu",
        # "310,364" and "go" all classify as `chat`, which would answer from
        # session state and leave the campaign waiting forever. The escape is
        # explicit, so a user is never trapped in a protocol they want out of.
        protocol = state.get("protocol") or {}
        if protocol.get("awaiting") and not _wants_out(text):
            return Command(
                goto="protocol",
                update={"intent": "protocol", "status": "Continuing the protocol…"},
            )
        if _wants_protocol(text, protocol):
            return Command(
                goto="protocol",
                update={"intent": "protocol", "status": "Enzyme redesign protocol"},
            )

        decision: dict[str, Any] | None = None
        caveats: list[str] = []
        if deps.settings.llm_available and text:
            decision = await complete_json(
                CLASSIFY_SYSTEM,
                f"Session state:\n{state_digest(state)}\n\nUser message:\n{text}",
                settings=deps.settings,
                on_fallback=llm_caveat(deps, caveats),
                schema=RoutingDecision,
                max_tokens=600,
            )
        if not decision or "intent" not in decision:
            decision = classify_rules(text, state)

        intent = str(decision.get("intent", "chat")).lower()
        if intent not in (
            "initialize", "design", "visualize", "summarize", "chat", "protocol"
        ):
            intent = "chat"

        has_reference = bool((state.get("reference_design") or {}).get("sequence"))
        # A request with nothing loaded has to bootstrap first, but the two
        # intents want different treatment: a design prompt names its target in
        # prose the initializer can search ("redesign a thermostable lipase"),
        # while a view request names nothing to look up, so searching RCSB for
        # "label the active site residues" only produces a confusing answer.
        if intent == "design" and not has_reference:
            intent = "initialize"
        elif intent == "visualize" and not has_reference:
            intent = (
                "initialize"
                if (decision.get("pdb_id") or decision.get("uniprot_id"))
                else "chat"
            )

        # Carry explicit identifiers so the initializer does not re-parse.
        hints = {}
        for key in ("pdb_id", "uniprot_id"):
            value = (decision.get(key) or "").strip()
            if value:
                hints[key] = value.upper()
        reference = state.get("reference_design") or {}
        if has_reference and intent in ("initialize", "design", "visualize") and any(
            value != (reference.get(key) or "").upper() for key, value in hints.items()
        ):
            ident = reference.get("pdb_id") or reference.get("uniprot_id") or "the loaded protein"
            return Command(goto="__end__", update={
                "intent": "chat", "status": "", "reply_source": "coordinator:target_conflict",
                "messages": [{"role": "assistant", "content": (
                    f"This session is working on {ident}. Start a new session to work on a "
                    "different protein. No new computation was started."
                )}],
                **({"warnings": caveats} if caveats else {}),
            })

        update: dict[str, Any] = {"intent": intent, "target_hints": hints}
        goal = (decision.get("goal") or "").strip()
        if goal:
            update["goal"] = goal
        elif intent in ("design", "initialize") and text and not state.get("goal"):
            update["goal"] = text

        requested = [m for m in (decision.get("mutations") or []) if isinstance(m, str)]
        update["requested_mutations"] = requested

        if intent == "chat":
            answer = None
            if deps.settings.llm_available:
                answer = await complete(
                    ANSWER_SYSTEM,
                    f"Session state:\n{state_digest(state)}\n\nUser message:\n{text}",
                    settings=deps.settings,
                    on_fallback=llm_caveat(deps, caveats),
                    # Prose for the user, so it may stream into the chat. The
                    # classifier above deliberately does not.
                    stream=True,
                    max_tokens=900,
                )
            if answer:
                update["reply_source"] = "coordinator:llm"
            else:
                answer = describe_state(state)
                update["reply_source"] = "coordinator:describe_state"
            if caveats:
                answer += "\n\n**Caveats from this run:**\n" + "\n".join(
                    f"- {warning}" for warning in dict.fromkeys(caveats)
                )
            update["messages"] = [{"role": "assistant", "content": answer}]
            update["status"] = ""
            if caveats:
                update["warnings"] = caveats
            return Command(goto="__end__", update=update)

        goto = {
            "initialize": "initializer",
            "design": "orchestrator",
            "visualize": "analyst",
            "summarize": "interpreter",
            "protocol": "protocol",
        }[intent]
        update["status"] = f"Planning: {intent}"
        if caveats:
            update["warnings"] = caveats
        return Command(goto=goto, update=update)

    return coordinator
