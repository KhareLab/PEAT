"""Graph assembly.

Nodes run as plain LangGraph nodes by default, and *tasks* are what get routed
through flowgentic to the rhapsody process pool (see runtime.py).

Why not wrap nodes as flowgentic EXECUTION_BLOCKs: that runs the node body on
asyncflow's own event loop, outside LangGraph's runnable context, so
`get_stream_writer()` raises "Called get_config outside of a runnable context"
and every status update and token is silently dropped. Streaming progress to the
chat matters more than having asyncflow schedule the nodes, and the wrapping
otherwise buys nothing here: its retry/timeout has to be disabled for nodes that
legitimately wait on an HPC job.

`wrap_nodes=True` restores the block wrapping for anyone who wants asyncflow to
own node execution and can live without custom-event streaming.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from . import trace as trace_mod
from .approval import guard_node
from .deps import Deps
from .nodes.analyst import make_analyst
from .nodes.coordinator import make_coordinator
from .nodes.initializer import make_initializer
from .nodes.interpreter import make_interpreter
from .nodes.orchestrator import make_orchestrator
from .nodes.protocol import make_protocol
from .state import DesignState

log = logging.getLogger(__name__)

NODES = ("coordinator", "initializer", "orchestrator", "analyst", "interpreter")


def node_retry_config():
    """A RetryConfig suited to long-running agent nodes, or None."""
    try:
        from flowgentic.langGraph.fault_tolerance import RetryConfig
    except ImportError:
        return None
    return RetryConfig(
        timeout_sec=None,   # a node may legitimately wait on an HPC job
        max_attempts=1,     # nodes write to the lake; re-running duplicates work
        retryable_exceptions=(ConnectionError, OSError),
    )


def wrap_node(integration: Any, fn: Callable) -> Callable:
    """Wrap a node as a flowgentic execution block, if possible."""
    if integration is None:
        return fn
    try:
        from flowgentic.langGraph.execution_wrappers import AsyncFlowType

        return integration.execution_wrappers.asyncflow(
            fn,
            flow_type=AsyncFlowType.EXECUTION_BLOCK,
            retry=node_retry_config(),
        )
    except Exception as exc:
        # A flowgentic change should degrade to plain LangGraph, not break boot.
        log.warning("flowgentic node wrapping unavailable (%s); running unwrapped", exc)
        return fn


def traced(name: str, fn: Callable) -> Callable:
    """Time a node and append its record to the turn's trace.

    The record goes into the node's own state update rather than a module-level
    accumulator, because several chat sessions share this process and a global
    would interleave their turns (see graph/trace.py). `current_node` is set for
    the duration so that code called *by* the node — `TaskManager.submit` — can
    record who submitted a task without a signature change.
    """

    async def run(state: DesignState) -> Any:
        token = trace_mod.current_node.set(name)
        started = time.perf_counter()
        try:
            result = await fn(state)
        finally:
            trace_mod.current_node.reset(token)
        ms = int((time.perf_counter() - started) * 1000)

        if isinstance(result, Command):
            update = dict(result.update or {})
            record = trace_mod.entry(name, ms, update, result.goto)
            update["trace"] = trace_mod.extend(state.get("trace"), record)
            # Rebuilt rather than mutated, and every field carried over: a
            # Command this wrapper silently dropped a field from would be a
            # routing bug, not a tracing one.
            return Command(
                goto=result.goto,
                update=update,
                graph=result.graph,
                resume=result.resume,
            )
        if isinstance(result, dict):
            # No node returns a bare dict today; tolerated so an added node
            # cannot silently fall out of the trace.
            record = trace_mod.entry(name, ms, result, None)
            return {**result, "trace": trace_mod.extend(state.get("trace"), record)}
        log.warning("node %s returned %s; not traced", name, type(result).__name__)
        return result

    return run


def build_graph(
    deps: Deps,
    *,
    integration: Any = None,
    checkpointer: Any = None,
    wrap_nodes: bool = False,
):
    """Compile the agent graph.

    Routing is expressed with `Command(goto=...)` from inside the nodes, so the
    only static edge is START -> coordinator; `add_node` declares the possible
    destinations for each node so LangGraph can validate them.
    """
    if wrap_nodes and integration is not None:
        log.warning(
            "wrap_nodes=True: nodes run on asyncflow's loop, so status/token "
            "streaming to the chat will be dropped"
        )
    else:
        integration = None  # nodes stay plain; tasks still use flowgentic

    builder = StateGraph(DesignState)

    factories = {
        "coordinator": make_coordinator,
        "initializer": make_initializer,
        "orchestrator": make_orchestrator,
        "analyst": make_analyst,
        "interpreter": make_interpreter,
        "protocol": make_protocol,
    }
    destinations = {
        "coordinator": (
            "initializer", "orchestrator", "analyst", "interpreter", "protocol", END,
        ),
        "initializer": ("orchestrator", END),
        "orchestrator": ("analyst", "interpreter", END),
        "analyst": ("orchestrator", "interpreter", END),
        "interpreter": (END,),
        # The protocol answers the user every turn: one stage runs, then the
        # turn ends and the next message carries the campaign forward.
        "protocol": (END,),
    }

    for name, factory in factories.items():
        builder.add_node(
            name,
            # Traced inside the flowgentic wrapper, so the timing is the node
            # body's own and not the scheduler's.
            wrap_node(integration, traced(name, guard_node(name, factory(deps), deps))),
            destinations=destinations[name],
        )

    builder.add_edge(START, "coordinator")
    return builder.compile(checkpointer=checkpointer)
