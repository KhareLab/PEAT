"""The Task Interface contract.

Task duration is indeterminate, so every interface submits asynchronously and
returns a handle immediately. The handle carries a future the caller may await
whenever it likes; nothing in the graph blocks on submission.

Backends differ in what they can do (Globus Compute cannot stream logs and
cannot cancel a started task), so capabilities are declared rather than assumed.
"""

from __future__ import annotations

import asyncio
import enum
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


class TaskState(str, enum.Enum):
    PENDING = "PENDING"           # accepted, not yet placed
    PROVISIONING = "PROVISIONING"  # endpoint/allocation warming up
    QUEUED = "QUEUED"             # waiting in a batch queue
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    UNKNOWN = "UNKNOWN"

    @property
    def terminal(self) -> bool:
        return self in (TaskState.DONE, TaskState.FAILED, TaskState.CANCELED)


# Orbit and PSI/J use their own vocabularies; normalize to ours.
_STATE_ALIASES = {
    "COMPLETED": TaskState.DONE,
    "DONE": TaskState.DONE,
    "SUCCESS": TaskState.DONE,
    "FAILED": TaskState.FAILED,
    "ERROR": TaskState.FAILED,
    "CANCELED": TaskState.CANCELED,
    "CANCELLED": TaskState.CANCELED,
    "RUNNING": TaskState.RUNNING,
    "ACTIVE": TaskState.RUNNING,
    "QUEUED": TaskState.QUEUED,
    "PENDING": TaskState.PENDING,
    "NEW": TaskState.PENDING,
    "HELD": TaskState.QUEUED,
    "PROVISIONING": TaskState.PROVISIONING,
}


def normalize_state(raw: Any) -> TaskState:
    if isinstance(raw, TaskState):
        return raw
    return _STATE_ALIASES.get(str(raw or "").strip().upper(), TaskState.UNKNOWN)


@dataclass(frozen=True)
class Capabilities:
    supports_cancel: bool = False
    supports_log_stream: bool = False
    supports_push_events: bool = False
    supports_staging: bool = False
    # Whether `kind="function"`/`"executable"` work can run at all. False on an
    # Orbit endpoint that serves no rhapsody plugin, which is the *correct*
    # state for a login-node gateway: Orbit gates rhapsody to the `compute` and
    # `standalone` host roles on purpose, and the veto is not overridable by
    # naming the plugin. Unlike the fields above this one is not fixed per
    # interface class -- `OrbitInterface` resolves it from the live endpoint.
    supports_function_tasks: bool = False


@dataclass
class TaskSpec:
    """What to run. `name` keys into an interface's registry."""

    name: str
    params: dict[str, Any] = field(default_factory=dict)
    kind: str = "function"  # function | executable | job | query
    resources: dict[str, Any] = field(default_factory=dict)
    backend: str | None = None  # named asyncflow backend, or remote endpoint
    label: str = ""  # shown in the UI

    @property
    def display(self) -> str:
        return self.label or self.name


@dataclass
class LogChunk:
    text: str = ""
    offset: int = 0  # byte offset to resume from


@dataclass
class TaskHandle:
    """A submitted task. `future` resolves to the task's result."""

    id: str
    interface: str
    spec: TaskSpec
    future: asyncio.Future
    state: TaskState = TaskState.PENDING
    submitted_at: float = field(default_factory=time.monotonic)
    created_wall: float = field(default_factory=time.time)
    meta: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    # Byte offsets already streamed, for interfaces that tail logs.
    log_offset: int = 0
    log_tail: str = ""
    # Set when the task settles, so `elapsed` stops being a stopwatch. Without
    # it a finished task reported age-since-submission, which grew for as long
    # as the session stayed open and made its duration meaningless.
    finished_at: float | None = None

    @property
    def elapsed(self) -> float:
        """How long the task ran, or has been running."""
        return (self.finished_at or time.monotonic()) - self.submitted_at

    @property
    def done(self) -> bool:
        return self.future.done()

    def mark_finished(self) -> None:
        """Freeze `elapsed`. Idempotent: a settled task keeps its first time."""
        if self.finished_at is None:
            self.finished_at = time.monotonic()

    def snapshot(self) -> dict[str, Any]:
        """JSON-safe view for the UI."""
        return {
            "id": self.id,
            "interface": self.interface,
            "task": self.spec.name,
            "label": self.spec.display,
            "state": self.state.value,
            "elapsed": round(self.elapsed, 2),
            "error": self.error,
            "log_tail": self.log_tail[-2000:],
            # Which graph node submitted this. Previously inferable only from
            # the task's name, and ambiguous where two call sites share one.
            "node": str(self.meta.get("node", "")),
        }


def new_task_id(name: str) -> str:
    return f"{name}-{uuid.uuid4().hex[:10]}"


class TaskInterface(ABC):
    """Base class for all task interfaces.

    Implementations submit work and return a handle whose future resolves later.
    They must never block the event loop while the task runs.
    """

    name: str = "base"
    capabilities: Capabilities = Capabilities()

    @abstractmethod
    async def submit(self, spec: TaskSpec) -> TaskHandle:
        """Place the task and return immediately."""

    async def status(self, handle: TaskHandle) -> TaskState:
        """Current state. Default infers it from the future."""
        if handle.future.cancelled():
            return TaskState.CANCELED
        if handle.future.done():
            return TaskState.FAILED if handle.future.exception() else TaskState.DONE
        return handle.state

    async def logs(self, handle: TaskHandle, offset: int = 0) -> LogChunk:
        """Incremental logs from `offset`. Empty when unsupported."""
        return LogChunk(offset=offset)

    async def result(self, handle: TaskHandle) -> Any:
        """Await the task's result (raises whatever the task raised)."""
        return await handle.future

    async def cancel(self, handle: TaskHandle) -> bool:
        """Request cancellation. Returns whether it was accepted."""
        if not self.capabilities.supports_cancel:
            return False
        return handle.future.cancel()

    async def close(self) -> None:
        """Release interface-level resources."""
        return None

    # --- helper for implementations -----------------------------------
    def _handle(
        self, spec: TaskSpec, future: asyncio.Future, *, task_id: str | None = None, **meta: Any
    ) -> TaskHandle:
        return TaskHandle(
            id=task_id or new_task_id(spec.name),
            interface=self.name,
            spec=spec,
            future=future,
            state=TaskState.RUNNING,
            meta=meta,
        )


# A task body is an async callable; registries map names to them.
TaskBody = Callable[..., Awaitable[Any]]
