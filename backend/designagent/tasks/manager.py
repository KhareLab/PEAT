"""The task manager.

One place that:
  - owns the three interfaces and routes a TaskSpec to the right one
  - keeps every handle so the UI can show in-flight work after a reconnect
  - emits lifecycle events (submitted / state / log / finished) to subscribers
  - records each task in the Design History

Nodes call `run_many` when they want results, but submission and event emission
happen the moment a task is placed, so the chat shows progress while the node
is still waiting.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

from ..context import current_turn
from ..lake.store import DesignHistory
from .base import TaskHandle, TaskInterface, TaskSpec, TaskState, new_task_id
from .hpc.base import drain_logs
from .local import LocalTaskInterface, QueryTaskInterface
from .registry import CATALOG

log = logging.getLogger(__name__)

EventSink = Callable[[dict], Awaitable[None]]


class TaskManager:
    """Routes task specs to interfaces and tracks their handles."""

    def __init__(
        self,
        *,
        local: TaskInterface | None = None,
        query: TaskInterface | None = None,
        hpc: TaskInterface | None = None,
        history: DesignHistory | None = None,
    ):
        self.interfaces: dict[str, TaskInterface] = {
            "local": local or LocalTaskInterface(),
            "query": query or QueryTaskInterface(),
        }
        if hpc is not None:
            self.interfaces["hpc"] = hpc
        self.history = history
        self._handles: dict[str, TaskHandle] = {}
        # The interface object that took each handle. Under logins two users'
        # `hpc` handles belong to two different interfaces, so the name on the
        # handle is not enough to cancel or tail one.
        self._owners: dict[str, TaskInterface] = {}
        self._sinks: dict[str, list[EventSink]] = {}
        self._log_drains: list[asyncio.Task] = []

    # --- event plumbing -----------------------------------------------
    def subscribe(self, session_id: str, sink: EventSink) -> None:
        self._sinks.setdefault(session_id, []).append(sink)

    def unsubscribe(self, session_id: str, sink: EventSink) -> None:
        sinks = self._sinks.get(session_id)
        if not sinks:
            return
        if sink in sinks:
            sinks.remove(sink)
        if not sinks:
            self._sinks.pop(session_id, None)

    async def _emit(self, session_id: str, event: dict) -> None:
        for sink in list(self._sinks.get(session_id, [])):
            try:
                await sink(event)
            except Exception as exc:  # a dead browser must not fail a task
                log.debug("task event sink failed: %s", exc)

    # --- routing ------------------------------------------------------
    def _hpc(self) -> TaskInterface | None:
        """This turn's `hpc` interface.

        With nobody signed in, the process-wide one, as always. With a user, their
        own (`runtime.OrbitRegistry`), or the process-wide one only when the turn
        context says so — an admin whose credentials are the environment's. A
        plain user with no endpoint of their own gets none, and their `hpc` work
        runs its local equivalent and says so, exactly as with no endpoint at all.
        """
        turn = current_turn.get()
        if turn is None or turn.use_shared_hpc:
            return self.interfaces.get("hpc")
        return turn.hpc

    def interface_for(self, spec: TaskSpec) -> tuple[str, TaskInterface]:
        """Pick an interface, falling back when the preferred one is absent."""
        task_def = CATALOG.get(spec.name)
        preferred = spec.params.pop("_interface", None) or (
            task_def.interface if task_def else "local"
        )
        if preferred == "hpc":
            hpc = self._hpc()
            if hpc is not None:
                return "hpc", hpc
        elif preferred in self.interfaces:
            return preferred, self.interfaces[preferred]
        if preferred == "hpc":
            # No endpoint configured: run the app-local equivalent instead.
            log.info("no HPC interface; running %s locally", spec.name)
            return "local", self.interfaces["local"]
        return "local", self.interfaces["local"]

    @property
    def hpc_available(self) -> bool:
        iface = self._hpc()
        return iface is not None and getattr(iface, "connected", True)

    def attach_hpc(self, interface: TaskInterface) -> None:
        """Register a remote interface on a running manager.

        Used when HPC credentials arrive after startup: routing reads
        `self.interfaces` on every submission, so nothing else has to be rebuilt.
        Closing the interface it replaces is the caller's job.
        """
        self.interfaces["hpc"] = interface

    def detach_hpc(self) -> None:
        self.interfaces.pop("hpc", None)

    # --- submission ---------------------------------------------------
    async def submit(
        self, spec: TaskSpec, *, session_id: str = "", campaign_id: str = ""
    ) -> TaskHandle:
        name, interface = self.interface_for(spec)
        try:
            from ..graph.approval import submission_permission

            permission = submission_permission.get()
            if name == "hpc" and permission is not None and not permission(spec):
                raise PermissionError("HPC submission requires approval of the current workflow")
            handle = await interface.submit(spec)
        except Exception as exc:
            log.warning("submitting %s failed: %s", spec.name, exc)
            # Surface the failure as a settled handle so callers need no
            # separate error path.
            loop = asyncio.get_running_loop()
            future: asyncio.Future = loop.create_future()
            future.set_exception(exc)
            handle = TaskHandle(
                id=new_task_id(f"{spec.name}-rejected"),
                interface=name,
                spec=spec,
                future=future,
                state=TaskState.FAILED,
                error=str(exc),
            )

        self._handles[handle.id] = handle
        self._owners[handle.id] = interface
        handle.meta.setdefault("session_id", session_id)
        handle.meta.setdefault("campaign_id", campaign_id)
        # Read from the context the node wrapper set rather than threading
        # `node=` through every run_many call site. Empty outside a graph run,
        # which is how the task tests call this. Imported here, not at module
        # level: `graph.deps` imports this module, and a local import keeps that
        # one-way even if trace.py ever grows a package import of its own.
        from ..graph.trace import current_node

        handle.meta.setdefault("node", current_node.get())

        if self.history and campaign_id:
            try:
                self.history.record_task_submitted(
                    campaign_id, handle.id, spec.name, name, spec.params
                )
            except Exception as exc:
                log.warning("recording task submission failed: %s", exc)

        await self._emit(
            session_id, {"type": "task", "event": "submitted", **handle.snapshot()}
        )

        # Tail logs where the interface supports it.
        if interface.capabilities.supports_log_stream:
            self._log_drains.append(
                asyncio.ensure_future(
                    drain_logs(
                        interface,
                        handle,
                        lambda h, text: self._emit(
                            session_id,
                            {"type": "task", "event": "log", "id": h.id, "text": text},
                        ),
                        interval=getattr(interface, "poll_interval", 2.0),
                    )
                )
            )
        return handle

    async def submit_many(
        self, specs: Iterable[TaskSpec], *, session_id: str = "", campaign_id: str = ""
    ) -> list[TaskHandle]:
        """Submit concurrently; every spec gets a handle even if some fail."""
        return list(
            await asyncio.gather(
                *(
                    self.submit(spec, session_id=session_id, campaign_id=campaign_id)
                    for spec in specs
                )
            )
        )

    # --- awaiting -----------------------------------------------------
    async def gather(
        self,
        handles: Iterable[TaskHandle],
        *,
        session_id: str = "",
        campaign_id: str = "",
        timeout: float | None = None,
    ) -> list[dict]:
        """Await handles and return one record per task.

        Never raises for a failed task: the record carries `ok=False` and the
        error, because one failed tool call should not abort the agent loop.
        """
        handles = list(handles)
        if not handles:
            return []

        async def _one(handle: TaskHandle) -> dict:
            try:
                result = await asyncio.wait_for(
                    asyncio.shield(handle.future), timeout
                ) if timeout else await handle.future
            except asyncio.TimeoutError:
                handle.state = TaskState.FAILED
                handle.error = f"timed out after {timeout}s"
                record = self._record(handle, None, handle.error)
            except asyncio.CancelledError:
                handle.state = TaskState.CANCELED
                handle.error = "canceled"
                record = self._record(handle, None, handle.error)
            except Exception as exc:
                handle.state = TaskState.FAILED
                handle.error = str(exc)
                record = self._record(handle, None, str(exc))
            else:
                handle.state = TaskState.DONE
                record = self._record(handle, result, "")
                # Staged files arrive as bytes; they become blob paths here, so
                # nothing downstream -- state, the graph, `_slim` -- ever sees a
                # megabyte inline.
                self._materialize_artifacts(handle, record)

            # One place, after every branch: `elapsed` must stop here or it keeps
            # counting for as long as the session is open.
            handle.mark_finished()
            await self._emit(
                session_id,
                {"type": "task", "event": "finished", **handle.snapshot()},
            )
            if self.history and campaign_id:
                self._persist(campaign_id, handle, record)
            return record

        return list(await asyncio.gather(*(_one(h) for h in handles)))

    def _materialize_artifacts(self, handle: TaskHandle, record: dict) -> None:
        """Write a job's staged files to blobs, replacing the bytes with paths.

        Interfaces cannot do this themselves -- they have no history -- and it
        must happen before `_persist`, which is the same division of labour the
        `structure`/`text` blob rule already follows.
        """
        result = record.get("result")
        if not isinstance(result, dict):
            return
        files = result.get("artifacts")
        if not isinstance(files, dict) or not files:
            return
        if self.history is None:
            # Nothing to write them to; say so rather than passing bytes on.
            result["artifacts"] = {}
            result["artifacts_error"] = "no history is attached to store artifacts"
            return
        paths: dict[str, str] = {}
        for name, data in files.items():
            suffix = Path(name).suffix or ".bin"
            prefix = f"{handle.spec.name}-{Path(name).stem or 'artifact'}"
            try:
                paths[name] = self.history.write_blob(data, suffix=suffix, prefix=prefix)
            except Exception as exc:
                log.warning("storing artifact %s for %s failed: %s", name, handle.id, exc)
                result["artifacts_error"] = f"{name}: {exc}"
        result["artifacts"] = paths

    def _record(self, handle: TaskHandle, result: Any, error: str) -> dict:
        return {
            "id": handle.id,
            "task": handle.spec.name,
            "interface": handle.interface,
            "params": handle.spec.params,
            "ok": not error,
            "state": handle.state.value,
            "result": result,
            "error": error,
            "elapsed": round(handle.elapsed, 2),
        }

    def _persist(self, campaign_id: str, handle: TaskHandle, record: dict) -> None:
        """Tier 1/2 write for a finished task. Never raises into the loop."""
        try:
            result = record["result"]
            blob = None
            # Structures are bulky; keep them out of the graph properties.
            if isinstance(result, dict) and isinstance(result.get("structure"), str):
                blob = (result["structure"], ".pdb")
            elif isinstance(result, dict) and isinstance(result.get("text"), str):
                blob = (result["text"], ".pdb" if result.get("format") == "pdb" else ".cif")
            slim = _slim(result)
            self.history.record_task_result(
                campaign_id,
                handle.id,
                name=handle.spec.name,
                interface=handle.interface,
                state=record["state"],
                result=slim,
                error=record["error"],
                blob=blob,
            )
        except Exception as exc:
            log.warning("recording task result failed: %s", exc)

    async def run_many(
        self,
        specs: Iterable[TaskSpec],
        *,
        session_id: str = "",
        campaign_id: str = "",
        timeout: float | None = None,
    ) -> list[dict]:
        """Submit and await in one call (the common case inside a node)."""
        handles = await self.submit_many(
            specs, session_id=session_id, campaign_id=campaign_id
        )
        return await self.gather(
            handles, session_id=session_id, campaign_id=campaign_id, timeout=timeout
        )

    # --- introspection ------------------------------------------------
    def get(self, task_id: str) -> TaskHandle | None:
        return self._handles.get(task_id)

    def interface_of(self, task_id: str) -> TaskInterface | None:
        return self._owners.get(task_id)

    def snapshot(self, session_id: str | None = None) -> list[dict]:
        handles = self._handles.values()
        if session_id:
            handles = [h for h in handles if h.meta.get("session_id") == session_id]
        return [h.snapshot() for h in handles]

    async def cancel(self, task_id: str) -> bool:
        handle = self._handles.get(task_id)
        if handle is None:
            return False
        interface = self._owners.get(task_id) or self.interfaces.get(handle.interface)
        if interface is None:
            return False
        ok = await interface.cancel(handle)
        if ok:
            await self._emit(
                handle.meta.get("session_id", ""),
                {"type": "task", "event": "canceled", **handle.snapshot()},
            )
        return ok

    async def close(self) -> None:
        for drain in self._log_drains:
            drain.cancel()
        self._log_drains.clear()
        for interface in self.interfaces.values():
            try:
                await interface.close()
            except Exception as exc:
                log.debug("closing interface failed: %s", exc)


def _slim(result: Any, limit: int = 20000) -> Any:
    """Drop bulky inline payloads before they reach the graph properties."""
    if not isinstance(result, dict):
        return result
    out = {}
    for key, value in result.items():
        if key in ("structure", "text") and isinstance(value, str):
            out[f"{key}_bytes"] = len(value)
        elif isinstance(value, str) and len(value) > limit:
            out[key] = value[:limit]
        else:
            out[key] = value
    return out
