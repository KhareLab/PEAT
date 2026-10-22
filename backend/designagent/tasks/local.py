"""Local task interfaces.

LocalTaskInterface routes CPU work to the rhapsody process pool through
flowgentic, so a long fold or scoring pass never blocks the event loop that is
streaming chat.

QueryTaskInterface runs structured-data retrieval on the main loop instead:
these tasks are IO-bound, and httpx clients do not survive pickling.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from ..context import current_turn
from .base import (
    Capabilities,
    TaskHandle,
    TaskInterface,
    TaskSpec,
)
from .registry import CATALOG

log = logging.getLogger(__name__)


def llm_credentials(name: str) -> dict[str, Any] | None:
    """The turn's LLM credentials for a body that calls the model itself.

    Read at submission, in the turn's context, and passed to the body as an
    argument: a pool worker's own settings were fixed at fork and are the
    operator's. Deliberately *not* added to `spec.params` — those are recorded
    in the lake (`record_task_submitted`), returned in task records and shown
    by the task routes, and a key must reach none of them.
    """
    task_def = CATALOG.get(name)
    turn = current_turn.get()
    if task_def is None or not task_def.needs_llm or turn is None:
        return None
    settings = turn.settings
    return {
        "api_key": settings.llm_key,
        "model": settings.model,
        "max_tokens": settings.max_tokens,
    }


class LocalTaskInterface(TaskInterface):
    """App-local compute, executed on the asyncflow/rhapsody process pool."""

    name = "local"
    capabilities = Capabilities(
        supports_cancel=True,
        supports_log_stream=False,
        supports_push_events=False,
        supports_staging=False,
        supports_function_tasks=True,  # running a task body is the whole job
    )

    def __init__(self, wrap: Callable[[Callable], Callable] | None = None):
        """`wrap` turns a plain async function into a flowgentic task.

        Left as None in tests, so bodies run in-process without a pool.
        """
        self._wrap = wrap
        self._wrapped: dict[str, Callable] = {}

    def _resolve(self, name: str) -> Callable:
        if name in self._wrapped:
            return self._wrapped[name]
        task_def = CATALOG.get(name)
        if task_def is None:
            raise KeyError(f"unknown task: {name}")
        body = task_def.body
        fn = self._wrap(body) if self._wrap else body
        self._wrapped[name] = fn
        return fn

    async def submit(self, spec: TaskSpec) -> TaskHandle:
        fn = self._resolve(spec.name)
        # create_task so submission returns immediately: the flowgentic wrapper
        # for FUNCTION_TASK is a coroutine function, not a future factory.
        future = asyncio.ensure_future(_call(fn, spec, llm_credentials(spec.name)))
        return self._handle(spec, future)


class QueryTaskInterface(TaskInterface):
    """Structured data retrieval (REST APIs), on the main event loop."""

    name = "query"
    capabilities = Capabilities(
        supports_cancel=True,
        supports_log_stream=False,
        supports_push_events=False,
        supports_staging=False,
        supports_function_tasks=True,
    )

    async def submit(self, spec: TaskSpec) -> TaskHandle:
        task_def = CATALOG.get(spec.name)
        if task_def is None:
            raise KeyError(f"unknown task: {spec.name}")
        future = asyncio.ensure_future(
            _call(task_def.body, spec, llm_credentials(spec.name))
        )
        return self._handle(spec, future)


async def _call(fn: Callable, spec: TaskSpec, llm: dict[str, Any] | None = None) -> Any:
    """Invoke a task body with its params, tolerating sync bodies."""
    kwargs = dict(spec.params)
    if llm is not None:
        kwargs["_llm"] = llm
    result = fn(**kwargs)
    if asyncio.iscoroutine(result) or isinstance(result, asyncio.Future):
        return await result
    return result
