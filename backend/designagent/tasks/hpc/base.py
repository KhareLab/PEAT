"""The remote-workflow interface.

Shared shape for Orbit and Globus Compute. Both give us "submit now, finish
later", but they differ in ways the agent must not have to care about:

  - Orbit pushes status events over a websocket; Globus only resolves a future.
  - Orbit's PSI/J plugin tails logs by byte offset; Globus has no log stream.
  - Orbit can cancel a running task; Globus can only cancel before it starts.

Hence `Capabilities`, and a `logs()` that returns an empty chunk rather than
raising where tailing is unsupported.
"""

from __future__ import annotations

import asyncio
import logging
from abc import abstractmethod
from typing import Any

from ..base import (
    TaskHandle,
    TaskInterface,
    TaskState,
)

log = logging.getLogger(__name__)


class RemoteWorkflowInterface(TaskInterface):
    """A task interface backed by a remote execution endpoint."""

    name = "hpc"

    def __init__(self, *, poll_interval: float = 2.0):
        self.poll_interval = poll_interval
        self._handles: dict[str, TaskHandle] = {}

    # --- lifecycle ----------------------------------------------------
    async def connect(self) -> None:
        """Establish the connection/session. Called once at startup."""
        return None

    @property
    @abstractmethod
    def connected(self) -> bool:
        """Whether submissions can currently be placed."""

    # --- helpers for subclasses ---------------------------------------
    def _track(self, handle: TaskHandle) -> TaskHandle:
        self._handles[handle.id] = handle
        return handle

    def get(self, task_id: str) -> TaskHandle | None:
        return self._handles.get(task_id)

    def _settle(
        self,
        handle: TaskHandle,
        state: TaskState,
        result: Any = None,
        error: str = "",
    ) -> None:
        """Resolve a handle's future exactly once from a status update."""
        if handle.future.done():
            return
        handle.state = state
        if error:
            handle.error = error
        if state.terminal:
            handle.mark_finished()
        if state is TaskState.DONE:
            handle.future.set_result(result)
        elif state is TaskState.FAILED:
            handle.future.set_exception(RuntimeError(error or "remote task failed"))
        elif state is TaskState.CANCELED:
            handle.future.cancel()


async def drain_logs(
    interface: TaskInterface, handle: TaskHandle, on_chunk, interval: float = 2.0
) -> None:
    """Poll an interface's logs until the task finishes.

    Offset-based tailing is the only mechanism both Orbit's PSI/J plugin and a
    file-tailing fallback share, so streaming is expressed as polling here.
    """
    if not interface.capabilities.supports_log_stream:
        return
    try:
        while not handle.future.done():
            chunk = await interface.logs(handle, handle.log_offset)
            if chunk.text:
                handle.log_offset = chunk.offset
                handle.log_tail = (handle.log_tail + chunk.text)[-8000:]
                await on_chunk(handle, chunk.text)
            await asyncio.sleep(interval)
        # One final read, to catch output written just before exit.
        chunk = await interface.logs(handle, handle.log_offset)
        if chunk.text:
            handle.log_offset = chunk.offset
            handle.log_tail = (handle.log_tail + chunk.text)[-8000:]
            await on_chunk(handle, chunk.text)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.warning("log drain for %s stopped: %s", handle.id, exc)
