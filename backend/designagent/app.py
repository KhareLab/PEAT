"""The web API.

Chat is a POST that returns an SSE stream, carrying four kinds of frame:

  token    - assistant text as it is generated
  status   - a progress line from a node ("Folding 6 candidates…")
  task     - a task's lifecycle (submitted / log / finished / canceled)
  state    - the state keys the UI renders (artifacts, ensemble, lead, viz)
  done     - end of turn

POST rather than GET-with-EventSource because the prompt belongs in a body; the
frontend reads the stream with fetch + ReadableStream.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from hmac import compare_digest
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .auth.credentials import USER_FIELDS, CredentialError, NoSecretsKey, validate
from .auth.store import User
from .config import (
    Settings,
    apply_overrides,
    clear_overrides,
    describe,
    get_settings,
    install_settings,
    overrides,
    secret_values,
)
from .context import current_turn
from .graph.trace import summarize
from .preflight import probe_all
from .runtime import Runtime, build_runtime, needs_rebuild

log = logging.getLogger(__name__)

# State keys worth pushing to the UI after a turn.
UI_STATE_KEYS = (
    "reference_design",
    "key_metric",
    "lead_design",
    "ensemble",
    "molecular_visualization",
    "artifacts",
    "design_summary",
    "worklist",
    "round",
    "intent",
    "status",
    "warnings",
    # Which code wrote the last reply, and this turn's node path. Both are how a
    # finished turn is explained after the fact; without them the UI can show
    # what the agent said but not how it got there.
    "reply_source",
    "trace",
)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    session_id: str = "default"


class _ScrubSecrets(logging.Filter):
    """Replace live secret values anywhere in a log record.

    Nothing in this codebase logs a key deliberately. The risk is text we did not
    write: a provider exception can quote the request it rejected, and a
    subprocess can echo its own argv. The filter is read through
    `config.secret_values()` on every record, so it covers a key supplied at
    runtime as well as one from the environment.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        secrets = secret_values()
        if not secrets:
            return True
        try:
            text = record.getMessage()
        except Exception:
            return True
        scrubbed = text
        for value in secrets:
            scrubbed = scrubbed.replace(value, "[redacted]")
        if scrubbed != text:
            record.msg = scrubbed
            record.args = ()
        return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(_ScrubSecrets())
    runtime = await build_runtime(get_settings())
    app.state.runtime = runtime
    try:
        yield
    finally:
        await runtime.close()


app = FastAPI(title="PEAT", lifespan=lifespan)


def _runtime(request: Request) -> Runtime:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(503, "runtime is not ready")
    return runtime


# --- logins -----------------------------------------------------------------
#
# Everything below is inert unless DESIGNAGENT_AUTH_ENABLED is on: with no
# `runtime.auth`, `_user` returns None and every check passes, so the
# development server, dev.sh and the offline suite see the app as it was.
#
# With it on, a request is someone's only if it carries a live session cookie.
# The cookie is HttpOnly, Secure and SameSite=Strict, and every mutating route
# also refuses an `Origin` that is not this host: SameSite already stops a
# cross-site form, the Origin check stops anything that gets past it, and the
# app still installs no CORS middleware (see `_authorize_write`).

COOKIE = "designagent_session"


def _user(request: Request, runtime: Runtime) -> User | None:
    """The signed-in user, None when logins are off, or 401."""
    if runtime.auth is None:
        return None
    user = runtime.auth.user_for_token(request.cookies.get(COOKIE, ""))
    if user is None:
        raise HTTPException(401, "sign in first")
    return user


def _same_origin(request: Request, runtime: Runtime) -> None:
    """Refuse a cross-origin write. A request with no Origin is not a browser's."""
    if runtime.auth is None:
        return
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.headers.get("host", ""):
        raise HTTPException(403, "cross-origin request refused")


def _require_admin(user: User | None) -> None:
    if user is not None and not user.is_admin:
        raise HTTPException(403, "only an admin may change the server's settings")


def _own_session(runtime: Runtime, user: User | None, session_id: str) -> None:
    """404, not 403, for someone else's session: whether it exists is theirs too."""
    if user is None:
        return
    if runtime.auth.session_owner(session_id) != user.id:
        raise HTTPException(404, "no such session")


def _client_ip(request: Request) -> str:
    """The caller's address, believing X-Forwarded-For only from a local proxy."""
    host = request.client.host if request.client else ""
    forwarded = request.headers.get("x-forwarded-for", "")
    if host in ("127.0.0.1", "::1") and forwarded:
        # Caddy writes the client it saw; the last hop is the one it added.
        return forwarded.split(",")[-1].strip()
    return host


class _LoginLimiter:
    """Failed logins per (address, username) and per username, in memory.

    In memory is enough for one process: the aim is to make guessing slow, and a
    restart that forgets the counts costs an attacker a restart they cannot cause.
    """

    WINDOW = 15 * 60
    PER_PAIR = 10
    PER_USER = 30

    def __init__(self) -> None:
        self._fails: dict[tuple[str, str], list[float]] = {}

    def _recent(self, key: tuple[str, str]) -> list[float]:
        cutoff = time.monotonic() - self.WINDOW
        kept = [t for t in self._fails.get(key, []) if t > cutoff]
        self._fails[key] = kept
        return kept

    def blocked(self, ip: str, username: str) -> bool:
        return (
            len(self._recent((ip, username))) >= self.PER_PAIR
            or len(self._recent(("*", username))) >= self.PER_USER
        )

    def failed(self, ip: str, username: str) -> None:
        now = time.monotonic()
        self._recent((ip, username)).append(now)
        self._recent(("*", username)).append(now)

    def succeeded(self, ip: str, username: str) -> None:
        self._fails.pop((ip, username), None)


_limiter = _LoginLimiter()


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


@app.post("/api/login")
async def login(request: Request, body: LoginRequest) -> JSONResponse:
    runtime = _runtime(request)
    if runtime.auth is None:
        raise HTTPException(404, "logins are not enabled on this server")
    _same_origin(request, runtime)
    ip = _client_ip(request)
    if _limiter.blocked(ip, body.username):
        raise HTTPException(429, "too many failed sign-ins; wait a few minutes")
    # scrypt is deliberately slow; keep it off the event loop.
    user = await asyncio.to_thread(runtime.auth.authenticate, body.username, body.password)
    if user is None:
        _limiter.failed(ip, body.username)
        log.info("failed sign-in for %r from %s", body.username, ip)
        raise HTTPException(401, "wrong username or password")
    _limiter.succeeded(ip, body.username)
    token = runtime.auth.start_session(user)
    response = JSONResponse({"user": _user_view(user)})
    response.set_cookie(
        COOKIE,
        token,
        max_age=int(runtime.settings.auth_session_hours * 3600),
        httponly=True,
        secure=runtime.settings.auth_cookie_secure,
        samesite="strict",
        path="/",
    )
    log.info("%s signed in from %s", user.username, ip)
    return response


@app.post("/api/logout")
async def logout(request: Request) -> JSONResponse:
    runtime = _runtime(request)
    if runtime.auth is not None:
        _same_origin(request, runtime)
        token = request.cookies.get(COOKIE, "")
        user = runtime.auth.user_for_token(token)
        runtime.auth.end_session(token)
        if user is not None:
            # Their decrypted credentials and their Orbit connection go with them.
            runtime.credentials.forget(user)
            await runtime.orbit_registry.drop(user.id)
    response = JSONResponse({"signed_out": True})
    response.delete_cookie(COOKIE, path="/")
    return response


def _user_view(user: User) -> dict[str, str]:
    return {"id": user.id, "username": user.username, "role": user.role}


@app.get("/api/me")
async def me(request: Request) -> JSONResponse:
    """Who is signed in, and what their credentials give them.

    With logins off this says so and nothing else, which is how the frontend
    knows not to show a sign-in page.
    """
    runtime = _runtime(request)
    user = _user(request, runtime)
    if user is None:
        return JSONResponse({"auth": False})
    turn = await runtime.turn_context(user)
    hpc = runtime.manager.interfaces.get("hpc") if turn.use_shared_hpc else turn.hpc
    return JSONResponse(
        {
            "auth": True,
            "user": _user_view(user),
            "llm": turn.settings.llm_available,
            "llm_error": runtime.deps.llm_error_for(user.id),
            "model": turn.settings.model if turn.settings.llm_available else None,
            "hpc": hpc is not None and getattr(hpc, "connected", True),
            "hpc_shared": turn.use_shared_hpc,
            "hpc_error": turn.hpc_error,
            "endpoint": turn.settings.orbit_endpoint if hpc is not None else "",
        }
    )


# --- chat sessions -------------------------------------------------------------


@app.post("/api/sessions")
async def new_session(request: Request) -> JSONResponse:
    """A fresh session id. Minted here, so nobody can claim one by guessing it."""
    runtime = _runtime(request)
    user = _user(request, runtime)
    if user is None:
        return JSONResponse({"session_id": f"s-{secrets.token_urlsafe(9)}"})
    _same_origin(request, runtime)
    return JSONResponse({"session_id": runtime.auth.create_chat_session(user)})


@app.get("/api/sessions")
async def list_sessions(request: Request) -> JSONResponse:
    runtime = _runtime(request)
    user = _user(request, runtime)
    if user is None:
        return JSONResponse({"sessions": []})
    return JSONResponse({"sessions": runtime.auth.sessions_of(user)})


# --- my credentials -----------------------------------------------------------


class CredentialsUpdate(BaseModel):
    """A sparse update of the caller's own credentials. `""` clears a field."""

    model_config = ConfigDict(extra="forbid")

    anthropic_api_key: str | None = None
    model: str | None = None
    orbit_broker_url: str | None = None
    orbit_broker_token: str | None = None
    orbit_broker_cert: str | None = None
    orbit_endpoint: str | None = None
    orbit_account: str | None = None
    orbit_queue: str | None = None

    def values(self) -> dict[str, str]:
        return {k: v for k, v in self.model_dump().items() if v is not None}


def _credentials_view(runtime: Runtime, user: User) -> dict[str, Any]:
    return {
        "credentials": runtime.credentials.describe(user),
        "fields": list(USER_FIELDS),
        "can_store": runtime.credentials.can_store,
        "allowed_brokers": list(runtime.settings.allowed_brokers),
    }


def _signed_in(request: Request, runtime: Runtime) -> User:
    user = _user(request, runtime)
    if user is None:
        raise HTTPException(404, "logins are not enabled on this server")
    return user


@app.get("/api/me/credentials")
async def read_my_credentials(request: Request) -> JSONResponse:
    runtime = _runtime(request)
    return JSONResponse(_credentials_view(runtime, _signed_in(request, runtime)))


@app.put("/api/me/credentials")
async def write_my_credentials(request: Request, body: CredentialsUpdate) -> JSONResponse:
    """Store the caller's credentials, encrypted. Applies from their next turn."""
    runtime = _runtime(request)
    user = _signed_in(request, runtime)
    _same_origin(request, runtime)
    try:
        values = validate(body.values(), runtime.settings)
        runtime.credentials.save(user, values)
    except CredentialError as exc:
        raise HTTPException(422, str(exc)) from exc
    except NoSecretsKey as exc:
        raise HTTPException(503, str(exc)) from exc
    # A changed broker, endpoint or token needs a new connection; the registry
    # notices by fingerprint, but dropping now frees the old one at once.
    if any(name.startswith("orbit_broker") or name == "orbit_endpoint" for name in values):
        await runtime.orbit_registry.drop(user.id)
    runtime.deps.forget_llm_error(user.id)
    return JSONResponse({"applied": True, **_credentials_view(runtime, user)})


@app.delete("/api/me/credentials")
async def clear_my_credentials(request: Request) -> JSONResponse:
    runtime = _runtime(request)
    user = _signed_in(request, runtime)
    _same_origin(request, runtime)
    runtime.credentials.clear(user)
    await runtime.orbit_registry.drop(user.id)
    runtime.deps.forget_llm_error(user.id)
    return JSONResponse({"applied": True, **_credentials_view(runtime, user)})


@app.post("/api/me/credentials/test")
async def test_my_credentials(request: Request, body: CredentialsUpdate) -> JSONResponse:
    """Probe the caller's credentials, with the unsaved values layered on top."""
    runtime = _runtime(request)
    user = _signed_in(request, runtime)
    _same_origin(request, runtime)
    try:
        values = validate(body.values(), runtime.settings)
    except CredentialError as exc:
        raise HTTPException(422, str(exc)) from exc
    resolved = runtime.credentials.resolve(user, runtime.settings)
    update: dict[str, Any] = {}
    for name, value in values.items():
        if name in ("anthropic_api_key", "orbit_broker_token"):
            from pydantic import SecretStr

            update[name] = SecretStr(value)
        elif name == "orbit_broker_cert":
            update[name] = runtime.credentials._cert_path(user, value) if value else ""
        else:
            update[name] = value
    if values.get("orbit_broker_url") and (values.get("orbit_endpoint") or resolved.own_orbit):
        update["orbit_enabled"] = True
    candidate = resolved.settings.model_copy(update=update)
    probes = await probe_all(candidate)
    # Only the user's own groups: the rest are the operator's and not theirs to see.
    return JSONResponse({"probes": {k: v for k, v in probes.items() if k in ("llm", "orbit")}})


def _frame(kind: str, payload: dict | None = None) -> str:
    body = {"type": kind, **(payload or {})}
    return f"data: {json.dumps(body, default=str)}\n\n"


# --- chat -----------------------------------------------------------------


@app.post("/api/chat")
async def chat(request: Request, body: ChatRequest) -> StreamingResponse:
    runtime = _runtime(request)
    session_id = body.session_id
    # Checked before the stream opens, so a refusal is a status code rather than
    # an error frame inside a 200.
    user = _user(request, runtime)
    _same_origin(request, runtime)
    _own_session(runtime, user, session_id)
    turn = await runtime.turn_context(user) if user is not None else None
    # A checkpoint proposal may be consumed by only one turn at a time.
    if session_id in runtime.active_chats:
        raise HTTPException(409, "a chat turn is already running for this session")
    runtime.active_chats.add(session_id)

    async def stream() -> AsyncIterator[str]:
        # Task events arrive from the manager on its own schedule, and graph
        # updates from astream; a queue merges both into one ordered stream.
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def sink(event: dict) -> None:
            await queue.put(_frame(event.pop("type", "task"), event))

        runtime.manager.subscribe(session_id, sink)

        # This turn's node records, collected off the `updates` stream so the
        # turn can be logged as one line when it closes. The authoritative copy
        # is the `trace` state channel; this is a local mirror.
        trace: list[dict] = []

        async def run_graph() -> None:
            # This task's own context (ensure_future copied it), so setting it
            # here reaches every node and task submission of this turn and no
            # other turn's. See `context.py` for why this is not `configurable`.
            if turn is not None:
                current_turn.set(turn)
            config = {
                "configurable": {"thread_id": session_id},
                "recursion_limit": 50,
            }
            # Only what this turn contributes; the rest comes from the
            # checkpoint. Spreading a fresh new_state() here overwrites every
            # `replace`-reduced channel with its empty default, which wipes the
            # reference design from turn 2 onward — the blank is committed to
            # the checkpoint, so it does not come back.
            payload = {
                "messages": [{"role": "user", "content": body.message}],
                "session_id": session_id,
                # Turn-scoped: a cancelled stream can leave a round's raw
                # results and a stale progress line behind, and neither
                # belongs to the next turn.
                "pending_results": {},
                "status": "",
                # Turn-scoped like pending_results: the checkpoint should hold
                # this turn's node path, not every turn's.
                "trace": [],
            }
            try:
                async for mode, chunk in runtime.app.astream(
                    payload,
                    config=config,
                    stream_mode=["messages", "custom", "updates"],
                ):
                    if mode == "messages":
                        message, meta = chunk
                        text = getattr(message, "content", "")
                        if isinstance(text, list):
                            text = "".join(
                                b.get("text", "")
                                for b in text
                                if isinstance(b, dict)
                            )
                        if text:
                            # LangGraph puts the emitting node in the metadata;
                            # it used to be destructured and dropped, which left
                            # streamed text as the one thing on this stream with
                            # no author.
                            await queue.put(
                                _frame(
                                    "token",
                                    {
                                        "text": text,
                                        "node": (meta or {}).get("langgraph_node", ""),
                                    },
                                )
                            )
                    elif mode == "custom":
                        if isinstance(chunk, dict):
                            await queue.put(
                                _frame(chunk.pop("type", "status"), chunk)
                            )
                    elif mode == "updates":
                        for node, update in (chunk or {}).items():
                            if not isinstance(update, dict):
                                continue
                            slim = {
                                k: update[k] for k in UI_STATE_KEYS if k in update
                            }
                            if slim:
                                await queue.put(
                                    _frame("state", {"node": node, "state": slim})
                                )
                            # Without an LLM no tokens stream, so send the
                            # assistant turn explicitly. `node` and `source`
                            # answer "which code wrote this sentence?" — the
                            # sibling `state` frame above has always carried the
                            # node and this one did not.
                            for message in update.get("messages") or []:
                                content = _content_of(message)
                                if content:
                                    await queue.put(
                                        _frame(
                                            "message",
                                            {
                                                "text": content,
                                                "node": node,
                                                "source": update.get(
                                                    "reply_source", ""
                                                ),
                                            },
                                        )
                                    )
                            # Each update carries the whole trace so far (the
                            # wrapper appends to what it read), so this mirror
                            # replaces rather than extends — appending would
                            # repeat every earlier node once per update.
                            if update.get("trace"):
                                trace[:] = update["trace"]
            except Exception as exc:
                log.exception("graph run failed")
                await queue.put(_frame("error", {"message": str(exc)}))
            finally:
                await queue.put(None)

        runner = asyncio.ensure_future(run_graph())
        try:
            while True:
                frame = await queue.get()
                if frame is None:
                    break
                yield frame
            # A clean turn used to log nothing at all.
            log.info("turn %s: %s", session_id, summarize(trace))
            yield _frame("done")
        except asyncio.CancelledError:
            # The browser went away; stop the turn rather than leaking it.
            runner.cancel()
            raise
        finally:
            runtime.manager.unsubscribe(session_id, sink)
            if not runner.done():
                runner.cancel()
            try:
                await asyncio.gather(runner, return_exceptions=True)
            finally:
                runtime.active_chats.discard(session_id)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # let nginx pass chunks straight through
        },
    )


def _content_of(message: Any) -> str:
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    role = getattr(message, "type", None) or (
        message.get("role") if isinstance(message, dict) else None
    )
    if role in ("human", "user"):
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") for b in content if isinstance(b, dict)
        )
    return ""


# --- sessions and state ---------------------------------------------------


@app.get("/api/sessions/{session_id}")
async def get_session(request: Request, session_id: str) -> JSONResponse:
    """Current state for a session, so a reload restores the UI."""
    runtime = _runtime(request)
    _own_session(runtime, _user(request, runtime), session_id)
    config = {"configurable": {"thread_id": session_id}}
    try:
        snapshot = await runtime.app.aget_state(config)
    except Exception as exc:
        log.warning("state fetch failed: %s", exc)
        return JSONResponse({"session_id": session_id, "state": {}, "messages": []})

    values = snapshot.values if snapshot else {}
    return JSONResponse(
        {
            "session_id": session_id,
            "state": {k: values.get(k) for k in UI_STATE_KEYS if k in values},
            "messages": [
                {"role": _role_of(m), "content": _content_of(m) or _raw_content(m)}
                for m in (values.get("messages") or [])
            ],
            "tasks": runtime.manager.snapshot(session_id),
        }
    )


def _role_of(message: Any) -> str:
    role = getattr(message, "type", None) or (
        message.get("role") if isinstance(message, dict) else None
    )
    return "user" if role in ("human", "user") else "assistant"


def _raw_content(message: Any) -> str:
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    return content if isinstance(content, str) else ""


# --- artifacts ------------------------------------------------------------


@app.get("/api/artifacts/{artifact_id}")
async def get_artifact(request: Request, artifact_id: str) -> Response:
    runtime = _runtime(request)
    user = _user(request, runtime)  # before the lookup, for the same reason as tasks
    record = runtime.artifacts.get(artifact_id)
    if record is None:
        raise HTTPException(404, "no such artifact")
    if user is not None:
        try:
            _own_session(runtime, user, record.get("session_id") or "")
        except HTTPException:
            raise HTTPException(404, "no such artifact") from None
    data = runtime.artifacts.read_bytes(artifact_id)
    if data is None:
        raise HTTPException(410, "artifact file is missing")

    headers = {}
    if record["kind"] == "docx":
        filename = f"{record['title'].replace(' ', '_')}.docx"
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return Response(content=data, media_type=record["mime"], headers=headers)


@app.get("/api/artifacts")
async def list_artifacts(request: Request, session_id: str = "default") -> JSONResponse:
    runtime = _runtime(request)
    _own_session(runtime, _user(request, runtime), session_id)
    return JSONResponse({"artifacts": runtime.artifacts.list_for_session(session_id)})


# --- tasks ----------------------------------------------------------------


@app.get("/api/tasks")
async def list_tasks(request: Request, session_id: str | None = None) -> JSONResponse:
    runtime = _runtime(request)
    user = _user(request, runtime)
    if user is None:
        return JSONResponse({"tasks": runtime.manager.snapshot(session_id)})
    if session_id:
        _own_session(runtime, user, session_id)
        return JSONResponse({"tasks": runtime.manager.snapshot(session_id)})
    # No session named: every task this user owns, never anyone else's — this
    # used to return every task in the process.
    tasks: list[dict] = []
    for owned in runtime.auth.sessions_of(user):
        tasks.extend(runtime.manager.snapshot(owned["session_id"]))
    return JSONResponse({"tasks": tasks})


def _own_task(request: Request, runtime: Runtime, task_id: str):
    # Who first: checked the other way round, an anonymous caller learned which
    # task ids exist from the difference between 404 and 401.
    user = _user(request, runtime)
    handle = runtime.manager.get(task_id)
    if handle is None:
        raise HTTPException(404, "no such task")
    if user is not None:
        try:
            _own_session(runtime, user, handle.meta.get("session_id") or "")
        except HTTPException:
            raise HTTPException(404, "no such task") from None
    return handle


@app.get("/api/tasks/{task_id}")
async def get_task(request: Request, task_id: str) -> JSONResponse:
    runtime = _runtime(request)
    return JSONResponse(_own_task(request, runtime, task_id).snapshot())


@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(request: Request, task_id: str) -> JSONResponse:
    runtime = _runtime(request)
    handle = _own_task(request, runtime, task_id)
    _same_origin(request, runtime)
    ok = await runtime.manager.cancel(task_id)
    if not ok:
        interface = runtime.manager.interface_of(task_id) or runtime.manager.interfaces.get(
            handle.interface
        )
        reason = (
            "this interface cannot cancel a running task"
            if interface and not interface.capabilities.supports_cancel
            else "the task had already finished"
        )
        return JSONResponse({"canceled": False, "reason": reason}, status_code=409)
    return JSONResponse({"canceled": True})


# --- settings -------------------------------------------------------------


class SettingsUpdate(BaseModel):
    """A sparse update. `None` means "leave alone"; `""` means "clear"."""

    model_config = ConfigDict(extra="forbid")

    anthropic_api_key: str | None = None
    model: str | None = None
    # Pool-visible, so a change here recycles the workers; reported by
    # `/api/settings` since before it was settable, which the drift test caught.
    max_tokens: int | None = None
    fold_backend: Literal["esmatlas", "local", "hpc"] | None = None

    orbit_enabled: bool | None = None
    orbit_broker_url: str | None = None
    orbit_broker_token: str | None = None
    orbit_broker_cert: str | None = None
    orbit_endpoint: str | None = None
    orbit_local_stack: bool | None = None
    orbit_rhapsody_backends: str | None = None
    orbit_psij_executor: str | None = None
    orbit_account: str | None = None
    orbit_queue: str | None = None
    orbit_job_duration_sec: int | None = None
    orbit_job_gpus: int | None = None
    # Ceilings on the in-band staging channel (tasks/hpc/artifacts.py). Changing
    # either restarts the Orbit interface, which takes them at construction.
    orbit_artifact_max_bytes: int | None = None
    orbit_job_output_max_bytes: int | None = None

    # `mpnn_command` and `mpnn_prologue` are deliberately absent. Both name
    # shell to run -- the prologue is appended to the job script verbatim
    # (`tasks/hpc/artifacts.py`) and the command names the executable -- so
    # accepting them here would turn this route into arbitrary code execution,
    # as the server user locally and on the endpoint under the site's
    # allocation remotely. The route is unauthenticated on loopback by design
    # (`_authorize_write`), so "only local callers" is not a defence. They stay
    # environment-only, like `data_dir` and `pool_workers`.
    mpnn_sampling_temp: float | None = None

    globus_enabled: bool | None = None
    globus_endpoint_id: str | None = None

    # Apply even though tasks are running, accepting that they are abandoned.
    force: bool = False

    def values(self) -> dict[str, Any]:
        data = self.model_dump(exclude={"force"})
        return {name: value for name, value in data.items() if value is not None}


def _settings_view(runtime: Runtime) -> dict[str, Any]:
    """What the UI may see: values for plain fields, hints for secrets."""
    shown = describe(runtime.settings)
    shown["llm"]["anthropic_api_key"]["last_error"] = runtime.llm_error
    shown["orbit"]["orbit_broker_url"]["last_error"] = runtime.orbit_error
    return {
        "credentials": shown,
        "overrides": sorted(overrides()),
        "hpc_available": runtime.manager.hpc_available,
        "llm_available": runtime.settings.llm_available,
        "persistent_sessions": runtime.persistent_checkpoints,
    }


def _authorize_write(request: Request, runtime: Runtime) -> None:
    """Gate the routes that accept secrets.

    Loopback with no token configured is the development default and stays open:
    the server is reachable only from this machine, and a cross-origin `PUT` with
    a JSON content type is stopped by the browser's preflight, since this app
    installs no CORS middleware. **Do not add a permissive CORS policy** — it
    would turn that into a real hole. Setting `DESIGNAGENT_ADMIN_TOKEN` requires
    the header even on loopback, and binding anywhere else requires one.

    With logins on, none of that applies: these routes change the *operator's*
    settings, so they need a signed-in admin and a same-origin request, and the
    shared token is not consulted. That closes backlog A17, where a same-host
    proxy made every caller "loopback".
    """
    if runtime.auth is not None:
        _require_admin(_user(request, runtime))
        _same_origin(request, runtime)
        return
    settings = runtime.settings
    expected = settings.admin_secret
    if not expected:
        if settings.bound_to_loopback:
            return
        raise HTTPException(
            403,
            "this server is not bound to loopback: set DESIGNAGENT_ADMIN_TOKEN "
            "to allow settings changes",
        )
    supplied = request.headers.get("x-designagent-admin", "")
    if not compare_digest(supplied, expected):
        raise HTTPException(403, "X-Designagent-Admin does not match")


#: Task states that mean work would be abandoned by a rebuild. `TaskState.terminal`
#: covers the other side, but a snapshot is plain JSON by then.
LIVE_TASK_STATES = ("PENDING", "PROVISIONING", "QUEUED", "RUNNING")


def _running_tasks(runtime: Runtime) -> list[str]:
    return [
        task["id"]
        for task in runtime.manager.snapshot()
        if str(task.get("state")) in LIVE_TASK_STATES
    ]


@app.get("/api/settings")
async def read_settings(request: Request) -> JSONResponse:
    runtime = _runtime(request)
    # The operator's configuration: paths, scheduler, broker. An admin's to read.
    if runtime.auth is not None:
        _require_admin(_user(request, runtime))
    return JSONResponse(_settings_view(runtime))


@app.put("/api/settings")
async def write_settings(request: Request, body: SettingsUpdate) -> JSONResponse:
    """Apply settings to the running process. Overrides are not persisted.

    A change the pool workers can see needs a new pool, and a new pool needs a new
    flowgentic integration and compiled graph, so that case rebuilds the runtime.
    Conversations survive it when checkpoints are on SQLite, which is why the
    response says whether they are.
    """
    runtime = _runtime(request)
    _authorize_write(request, runtime)
    values = body.values()
    if not values:
        return JSONResponse(_settings_view(runtime))

    busy = _running_tasks(runtime)
    if busy and not body.force:
        return JSONResponse(
            {
                "applied": False,
                "reason": "tasks are still running; retry with force=true to "
                "apply anyway and abandon them",
                "running": busy,
            },
            status_code=409,
        )

    previous = runtime.settings
    try:
        candidate = apply_overrides(values)
    except ValidationError as exc:
        raise HTTPException(422, f"invalid settings: {exc.error_count()} problem(s)") from exc

    if needs_rebuild(previous, candidate):
        runtime = await _rebuild(request.app, candidate, previous)
        restarted = ["pool", "graph"]
        if any(name.startswith(("orbit_", "globus_")) for name in values):
            restarted.append("orbit")
    else:
        restarted = await runtime.reconfigure(candidate)

    return JSONResponse(
        {
            "applied": True,
            "restarted": restarted,
            "sessions_preserved": runtime.persistent_checkpoints,
            **_settings_view(runtime),
        }
    )


@app.delete("/api/settings")
async def reset_settings(request: Request) -> JSONResponse:
    """Drop every override, returning to what the environment and .env say."""
    runtime = _runtime(request)
    _authorize_write(request, runtime)
    previous = runtime.settings
    candidate = clear_overrides()
    if needs_rebuild(previous, candidate):
        runtime = await _rebuild(request.app, candidate, previous)
        restarted = ["pool", "graph", "orbit"]
    else:
        restarted = await runtime.reconfigure(candidate)
    return JSONResponse({"applied": True, "restarted": restarted, **_settings_view(runtime)})


@app.post("/api/settings/test")
async def test_settings(request: Request, body: SettingsUpdate) -> JSONResponse:
    """Validate credentials without storing them.

    The candidate values are layered onto the current settings in a throwaway
    copy, so a key that turns out to be wrong never becomes the one this process
    is using.
    """
    runtime = _runtime(request)
    _authorize_write(request, runtime)
    candidate = runtime.settings.model_copy(update=_coerce(body.values()))
    return JSONResponse({"probes": await probe_all(candidate)})


def _coerce(values: dict[str, Any]) -> dict[str, Any]:
    """Validate loose JSON into field types without installing anything."""
    probe = Settings(**values)
    return {name: getattr(probe, name) for name in values}


async def _rebuild(app: FastAPI, candidate: Settings, previous: Settings) -> Runtime:
    """Replace the whole runtime, falling back to the previous settings.

    The old one is closed first: Kuzu holds an exclusive file lock, so two
    runtimes cannot overlap on one data directory.
    """
    old = app.state.runtime
    await old.close()
    try:
        runtime = await build_runtime(candidate)
    except Exception as exc:
        log.exception("rebuilding with the new settings failed; reverting")
        install_settings(previous)
        app.state.runtime = await build_runtime(previous)
        raise HTTPException(500, f"those settings could not be applied: {exc}") from exc
    app.state.runtime = runtime
    return runtime


# --- health ---------------------------------------------------------------


@app.get("/api/health")
async def health(request: Request) -> JSONResponse:
    runtime = _runtime(request)
    if runtime.auth is not None:
        # Unauthenticated callers learn that the server is up and wants a login,
        # nothing about its configuration. A signed-in user gets `/api/me`, which
        # is about *their* key and endpoint; an admin also gets the full view.
        user = runtime.auth.user_for_token(request.cookies.get(COOKIE, ""))
        if user is None or not user.is_admin:
            return JSONResponse({"ok": True, "auth": True})
    return JSONResponse(
        {
            "ok": True,
            "llm": runtime.settings.llm_available,
            "model": runtime.settings.model if runtime.settings.llm_available else None,
            "hpc": runtime.manager.hpc_available,
            "flowgentic": runtime.integration is not None,
            "fold_backend": runtime.settings.fold_backend,
            # Presence, source and last error per credential — never a value.
            "credentials": _settings_view(runtime)["credentials"],
            "interfaces": {
                name: {
                    "cancel": iface.capabilities.supports_cancel,
                    "logs": iface.capabilities.supports_log_stream,
                    "push_events": iface.capabilities.supports_push_events,
                    "staging": iface.capabilities.supports_staging,
                }
                for name, iface in runtime.manager.interfaces.items()
            },
            "notes": runtime.live_notes(),
        }
    )


@app.get("/api/catalog")
async def catalog(request: Request) -> JSONResponse:
    """What the agent can do, for the UI's help panel."""
    runtime = _runtime(request)
    _user(request, runtime)
    from .tasks.registry import CATALOG

    return JSONResponse(
        {
            "tasks": [
                {
                    "name": t.name,
                    "interface": t.interface,
                    "description": t.description,
                    "produces": t.produces,
                }
                for t in CATALOG.values()
            ]
        }
    )
