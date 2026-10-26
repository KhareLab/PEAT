"""Application runtime: builds the whole stack and tears it down cleanly.

Startup order matters:
  1. rhapsody ConcurrentExecutionBackend over a ProcessPoolExecutor
  2. flowgentic LangraphIntegration, which creates the asyncflow WorkflowEngine
  3. task interfaces (local tasks routed through flowgentic to the pool)
  4. optional Orbit interface for remote HPC workflows
  5. the checkpointer, then the compiled graph

Everything is optional downwards: if flowgentic or rhapsody cannot start, the
agent still runs with plain asyncio task execution, because a broken deployment
substrate should not make the chatbot unusable.

Settings can change while the process runs (`PUT /api/settings`). Which parts
have to restart depends on who reads the value: anything a *task body* reads is
baked into the pool workers at fork, so it needs a new pool, and a new pool means
a new flowgentic integration, task wrapper and compiled graph. Orbit credentials
are read only in this process, so they need nothing but a reconnect. See
`needs_rebuild` and `Runtime.reconfigure`.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from pydantic import SecretStr

from .artifacts.store import ArtifactStore
from .config import Settings, adopt, get_settings, install_settings
from .context import TurnContext
from .graph.build import build_graph
from .graph.deps import Deps
from .lake.store import DesignHistory
from .tasks.local import LocalTaskInterface, QueryTaskInterface
from .tasks.manager import TaskManager

log = logging.getLogger(__name__)

# Fields a pool worker reads for itself, through `get_settings()` inside a task
# body: tools/http.py (timeout, user agent), tools/esmfold.py (fold backend) and
# llm.build_llm, reached from tools/molviz_agent.py and tools/chemgraph_agent.py.
# A worker is handed its settings at fork, so changing one of these means a new
# pool — and `wrap_nodes`/`data_dir` reach further still.
POOL_VISIBLE_FIELDS = frozenset(
    {
        "anthropic_api_key",
        "model",
        "max_tokens",
        "fold_backend",
        "http_timeout_sec",
        "user_agent",
        "pool_workers",
    }
)
REBUILD_FIELDS = POOL_VISIBLE_FIELDS | {"data_dir", "wrap_nodes"}


def changed_fields(old: Settings, new: Settings) -> set[str]:
    return {name for name in Settings.model_fields if getattr(old, name) != getattr(new, name)}


def needs_rebuild(old: Settings, new: Settings) -> bool:
    """True when the change cannot be applied without a new process pool."""
    return bool(changed_fields(old, new) & REBUILD_FIELDS)


@dataclass
class Runtime:
    settings: Settings
    deps: Deps
    app: Any                      # the compiled LangGraph
    manager: TaskManager
    history: DesignHistory
    artifacts: ArtifactStore
    integration: Any = None       # flowgentic LangraphIntegration
    backend: Any = None           # rhapsody backend
    orbit: Any = None
    orbit_stack: Any = None       # LocalOrbitStack, when the dev broker is ours
    persistent_checkpoints: bool = True
    # Why the last Orbit attempt failed, for /api/health. The LLM's equivalent
    # lives on Deps, because only nodes can observe it.
    orbit_error: str = ""
    # Logins (plans/LINODE_DEPLOY.md, Phase 2). All three are None with
    # DESIGNAGENT_AUTH_ENABLED off, and every route then behaves as before.
    auth: Any = None              # auth.store.AuthStore
    credentials: Any = None       # auth.credentials.CredentialService
    orbit_registry: Any = None    # OrbitRegistry
    _stack: AsyncExitStack | None = field(default=None, repr=False)
    _notes: list[str] = field(default_factory=list)
    active_chats: set[str] = field(default_factory=set, repr=False)

    @property
    def notes(self) -> list[str]:
        """Human-readable facts about how this process came up."""
        return self.live_notes()

    def live_notes(self) -> list[str]:
        """Startup facts, plus the two lines that can change under us.

        `_notes` holds what is settled once the process is up (pool, checkpointer).
        The LLM and Orbit lines are recomputed, because a key supplied at runtime
        must not leave "No ANTHROPIC_API_KEY" on the screen.
        """
        notes = list(self._notes)
        if not self.settings.llm_available:
            notes.append(
                "No ANTHROPIC_API_KEY: nodes use their deterministic rule-based paths."
            )
        elif self.llm_error:
            notes.append(f"The LLM is configured but degrading: {self.llm_error}")
        if (
            self.settings.orbit_enabled
            or self.settings.orbit_local_stack
            or self.settings.globus_enabled
        ):
            if self.manager.hpc_available:
                notes.append("Orbit connected for remote HPC workflows.")
            else:
                notes.append(
                    f"Orbit unavailable ({self.orbit_error or 'not connected'}); "
                    "HPC tasks will run locally."
                )
        return notes

    @property
    def llm_error(self) -> str:
        """Why the last LLM call degraded, as the nodes saw it."""
        return self.deps.last_llm_error

    async def reconfigure(self, new: Settings) -> list[str]:
        """Apply settings that do not need a new pool. Returns what restarted.

        The caller must have checked `needs_rebuild` first: this path deliberately
        cannot touch the pool, because the workers' settings are fixed at fork.
        """
        changed = changed_fields(self.settings, new)
        restarted: list[str] = []
        # `adopt`, not `install_settings`: the override layer must survive, or
        # `GET /api/settings` would report a user's value as coming from the
        # environment.
        adopt(new)
        self.settings = new
        # Nodes read `deps.settings` on every turn, so the compiled graph picks
        # this up without being rebuilt.
        self.deps.settings = new
        self.deps.clear_llm_errors()
        if any(f.startswith(("orbit_", "globus_")) for f in changed):
            await self._restart_orbit(new)
            restarted.append("orbit")
        return restarted

    async def _restart_orbit(self, settings: Settings) -> None:
        if self.orbit is not None:
            try:
                await self.orbit.close()
            except Exception as exc:  # a dead interface must not block the new one
                log.warning("closing the old Orbit interface failed: %s", exc)
        self.orbit = None
        self.manager.detach_hpc()
        if self.orbit_stack is not None and not settings.orbit_local_stack:
            await _stop_local_stack(self.orbit_stack)
            self.orbit_stack = None
        if settings.orbit_local_stack and self.orbit_stack is None:
            self.orbit_stack, self.orbit_error = await _start_local_stack(settings)
        settings = _with_local_stack(settings, self.orbit_stack)
        interface, error = await _make_orbit(settings)
        self.orbit_error = error
        self.orbit = interface
        if interface is not None:
            self.manager.attach_hpc(interface)

    async def turn_context(self, user: Any) -> TurnContext:
        """Who this turn is for, with their settings and their `hpc` interface."""
        resolved = self.credentials.resolve(user, self.settings)
        hpc, error = None, ""
        if resolved.own_orbit:
            hpc, error = await self.orbit_registry.get(user, resolved.settings)
        problems = [*resolved.errors, *([error] if error else [])]
        return TurnContext(
            user_id=user.id,
            username=user.username,
            role=user.role,
            settings=resolved.settings,
            hpc=hpc,
            use_shared_hpc=resolved.shared_orbit,
            hpc_error="; ".join(problems),
        )

    async def close(self) -> None:
        if self.orbit_registry is not None:
            await self.orbit_registry.close_all()
        await self.manager.close()
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
        if self.orbit_stack is not None:
            await _stop_local_stack(self.orbit_stack)
            self.orbit_stack = None
        if self.auth is not None:
            self.auth.close()
        self.history.close()


class OrbitRegistry:
    """One Orbit interface per signed-in user who brought their own endpoint.

    Keyed by user, and rebuilt when that user's broker, endpoint, cert or token
    changes. Each interface registers under its **own client name**: two clients
    sharing one silently steal each other's replies (backlog A18, observed, not
    yet read in the broker's source) — which two users' interfaces would
    otherwise do the moment both were connected.

    A failed connect is remembered for `retry_after` seconds, so a wrong token
    costs one connect timeout rather than one per turn.
    """

    def __init__(self, retry_after: float = 60.0):
        self.retry_after = retry_after
        self._entries: dict[str, tuple[tuple, Any, str, float]] = {}
        self._lock = asyncio.Lock()

    async def get(self, user: Any, settings: Settings) -> tuple[Any, str]:
        from .auth.credentials import fingerprint

        key = fingerprint(settings)
        async with self._lock:
            entry = self._entries.get(user.id)
            if entry is not None:
                old_key, iface, error, when = entry
                if old_key == key:
                    if iface is not None and getattr(iface, "connected", True):
                        return iface, ""
                    if iface is None and time.monotonic() - when < self.retry_after:
                        return None, error
                await self._close(iface)
            name = f"{settings.orbit_client_name}-{user.username}-{secrets.token_hex(3)}"
            iface, error = await _make_orbit(
                settings.model_copy(update={"orbit_client_name": name})
            )
            self._entries[user.id] = (key, iface, error, time.monotonic())
            return iface, error

    async def drop(self, user_id: str) -> None:
        async with self._lock:
            entry = self._entries.pop(user_id, None)
            if entry is not None:
                await self._close(entry[1])

    async def close_all(self) -> None:
        async with self._lock:
            for _key, iface, _error, _when in self._entries.values():
                await self._close(iface)
            self._entries.clear()

    @staticmethod
    async def _close(iface: Any) -> None:
        if iface is None:
            return
        try:
            await iface.close()
        except Exception as exc:  # a dead interface must not block its replacement
            log.warning("closing a user's Orbit interface failed: %s", exc)


async def _make_backend(settings: Settings):
    """The rhapsody process-pool backend, or None if unavailable."""
    from concurrent.futures import ProcessPoolExecutor

    from rhapsody.backends import ConcurrentExecutionBackend

    executor = ProcessPoolExecutor(
        max_workers=settings.pool_workers,
        # The only channel into a worker. Task bodies call `get_settings()`
        # themselves (tools/http.py, tools/esmfold.py, and llm.build_llm from
        # tools/molviz_agent.py) and cache the result for the worker's lifetime.
        # Handing the instance over at fork is what lets a key supplied at runtime
        # reach them, and it stops a worker's config depending on its own CWD.
        initializer=install_settings,
        initargs=(settings,),
    )
    # Backends initialise asynchronously: `await Backend(...)` is the API.
    return await ConcurrentExecutionBackend(executor, name="compute")


def _task_wrapper(integration: Any):
    """Build the callable that turns a task body into a flowgentic task."""
    from flowgentic.langGraph.execution_wrappers import AsyncFlowType
    from flowgentic.langGraph.fault_tolerance import RetryConfig

    retry = RetryConfig(
        # Folding or an HPC job can take far longer than flowgentic's 30s default.
        timeout_sec=None,
        max_attempts=1,
        retryable_exceptions=(ConnectionError, OSError),
    )

    def wrap(body):
        return integration.execution_wrappers.asyncflow(
            body,
            flow_type=AsyncFlowType.FUNCTION_TASK,
            backend="compute",
            retry=retry,
        )

    return wrap


async def _make_orbit(settings: Settings) -> tuple[Any, str]:
    """Connect the Orbit interface. Returns (interface or None, reason it is None)."""
    if not settings.orbit_enabled:
        return None, ""
    from .tasks.hpc.orbit import OrbitInterface

    interface = OrbitInterface(
        broker_url=settings.orbit_broker_url,
        endpoint=settings.orbit_endpoint,
        token=settings.orbit_token,
        cert=settings.orbit_broker_cert,
        rhapsody_backends=settings.rhapsody_backends,
        name=settings.orbit_client_name,
        poll_interval=settings.orbit_poll_interval,
        connect_timeout=settings.orbit_connect_timeout,
        output_max_bytes=settings.orbit_job_output_max_bytes,
        artifact_max_bytes=settings.orbit_artifact_max_bytes,
    )
    try:
        await interface.connect()
    except Exception as exc:
        log.warning("Orbit connect failed: %s", exc)
        return None, str(exc)
    return interface, ""


async def _make_globus(settings: Settings) -> tuple[Any, str]:
    """Connect the Globus Compute interface, if one is configured.

    Never exercised against a live endpoint (backlog A2), and `globus-compute-sdk`
    is not a dependency — hence the explicit reason rather than a bare failure.
    """
    if not settings.globus_enabled:
        return None, ""
    try:
        from .tasks.hpc.globus import GlobusComputeInterface
    except Exception as exc:
        return None, f"the Globus adapter could not be imported ({exc})"
    interface = GlobusComputeInterface(endpoint_id=settings.globus_endpoint_id)
    try:
        await interface.connect()
    except Exception as exc:
        log.warning("Globus connect failed: %s", exc)
        return None, str(exc)
    return interface, ""


async def _start_local_stack(settings: Settings) -> tuple[Any, str]:
    """Bring up the development broker + endpoint. Returns (stack, error)."""
    try:
        from .tasks.hpc.local_orbit import LocalOrbitStack

        stack = LocalOrbitStack(work_dir=settings.orbit_work_dir)
        await stack.start(timeout=90)
    except Exception as exc:
        log.warning("local Orbit stack failed to start: %s", exc)
        return None, f"the local Orbit stack did not start ({exc})"
    return stack, ""


async def _stop_local_stack(stack: Any) -> None:
    try:
        await stack.stop()
    except Exception as exc:
        log.warning("stopping the local Orbit stack failed: %s", exc)


def _with_local_stack(settings: Settings, stack: Any) -> Settings:
    """Point the Orbit settings at our own broker, leaving the user's alone."""
    if stack is None:
        return settings
    return settings.model_copy(
        update={
            "orbit_enabled": True,
            "orbit_broker_url": stack.broker_url,
            "orbit_broker_cert": str(stack.cert),
            "orbit_endpoint": stack.endpoint_name,
            # The dev broker runs `--no-auth`, so a token would be meaningless.
            "orbit_broker_token": SecretStr(""),
            "orbit_rhapsody_backends": settings.orbit_rhapsody_backends or "concurrent",
        }
    )


async def build_runtime(settings: Settings | None = None) -> Runtime:
    """Bring up the full stack."""
    settings = settings or get_settings()
    settings.ensure_dirs()

    notes: list[str] = []
    stack = AsyncExitStack()

    history = DesignHistory(settings)
    artifacts = ArtifactStore(settings.artifacts_dir)

    # --- compute substrate ---
    backend = None
    integration = None
    wrap = None
    try:
        backend = await _make_backend(settings)
        from flowgentic.langGraph.main import LangraphIntegration

        integration = await stack.enter_async_context(
            LangraphIntegration(backend=backend)
        )
        wrap = _task_wrapper(integration)
        notes.append(
            f"flowgentic + asyncflow on a rhapsody process pool "
            f"({settings.pool_workers} workers)."
        )
    except Exception as exc:
        log.warning("flowgentic/rhapsody unavailable: %s", exc)
        notes.append(
            f"Running without flowgentic ({exc}); local tasks execute in-process."
        )

    # --- task interfaces ---
    orbit_stack = None
    orbit_error = ""
    if settings.orbit_local_stack:
        orbit_stack, orbit_error = await _start_local_stack(settings)
        if orbit_stack is not None:
            notes.append(f"Development Orbit broker at {orbit_stack.broker_url}.")
    orbit_settings = _with_local_stack(settings, orbit_stack)
    orbit, error = await _make_orbit(orbit_settings)
    orbit_error = orbit_error or error
    if orbit is None and settings.globus_enabled:
        orbit, error = await _make_globus(settings)
        orbit_error = orbit_error or error
    manager = TaskManager(
        local=LocalTaskInterface(wrap=wrap),
        query=QueryTaskInterface(),
        hpc=orbit,
        history=history,
    )

    # --- checkpointer ---
    checkpointer = None
    persistent_checkpoints = True
    try:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        checkpointer = await stack.enter_async_context(
            AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_db_path))
        )
        notes.append(f"Conversations persist in {settings.checkpoint_db_path}.")
    except Exception as exc:
        log.warning("sqlite checkpointer unavailable: %s", exc)
        from langgraph.checkpoint.memory import InMemorySaver

        checkpointer = InMemorySaver()
        persistent_checkpoints = False
        notes.append(f"Using in-memory checkpoints ({exc}); history is not persisted.")

    # The LLM and Orbit lines are not appended here: `Runtime.live_notes()`
    # recomputes them, so a credential supplied at runtime is reflected at once.

    # --- logins ---
    auth = credentials = registry = None
    if settings.auth_enabled:
        from .auth.credentials import CredentialService
        from .auth.store import AuthStore

        auth = AuthStore(settings.auth_db_path, session_hours=settings.auth_session_hours)
        credentials = CredentialService(auth, settings)
        registry = OrbitRegistry()
        notes.append(
            f"Logins on: {auth.user_count()} account(s); credentials "
            + (
                "encrypted at rest."
                if credentials.can_store
                else "cannot be stored (no secrets key)."
            )
        )

    deps = Deps(settings=settings, tasks=manager, history=history, artifacts=artifacts)
    graph = build_graph(
        deps,
        integration=integration,
        checkpointer=checkpointer,
        wrap_nodes=settings.wrap_nodes,
    )

    for note in notes:
        log.info("runtime: %s", note)

    return Runtime(
        settings=settings,
        deps=deps,
        app=graph,
        manager=manager,
        history=history,
        artifacts=artifacts,
        integration=integration,
        backend=backend,
        orbit=orbit,
        orbit_stack=orbit_stack,
        persistent_checkpoints=persistent_checkpoints,
        orbit_error=orbit_error,
        auth=auth,
        credentials=credentials,
        orbit_registry=registry,
        _stack=stack,
        _notes=notes,
    )
