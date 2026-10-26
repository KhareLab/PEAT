"""Logins, ownership and per-user credentials (plans/LINODE_DEPLOY.md, Phase 2).

Everything here runs with `auth_enabled=True` on the real FastAPI app and the
real graph, with stubbed tools. The rest of the suite runs with it off, which is
the other half of the contract: logins must change nothing until switched on.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest
from designagent.auth.credentials import CredentialBox, CredentialError, CredentialService
from designagent.auth.store import AuthStore
from designagent.config import Settings, install_settings, secret_values
from designagent.context import TurnContext, current_turn

COOKIE = "designagent_session"
PASSWORD = "correct horse battery"
OPERATOR_KEY = "sk-ant-operator-0123456789abcdef"
ALICE_KEY = "sk-ant-alice-0123456789abcdef0123"
BROKER = "https://broker.example:8443"
REDESIGN = "redesign 1UBQ for thermostability"


@pytest.fixture
def auth_settings(settings: Settings) -> Settings:
    from pydantic import SecretStr

    return settings.model_copy(
        update={
            "auth_enabled": True,
            "secrets_key": SecretStr(CredentialBox.generate_key()),
            "orbit_allowed_brokers": BROKER,
            "anthropic_api_key": SecretStr(OPERATOR_KEY),
        }
    )


@pytest.fixture
def app_client(deps, auth_settings, monkeypatch):
    """The app with logins on, three accounts, and a way to act as each."""
    from designagent import app as app_module
    from designagent.graph.build import build_graph
    from designagent.runtime import OrbitRegistry, Runtime
    from fastapi.testclient import TestClient
    from langgraph.checkpoint.memory import InMemorySaver

    deps.settings = auth_settings
    install_settings(auth_settings)
    auth = AuthStore(auth_settings.auth_db_path)
    accounts = {
        "alice": auth.create_user("alice", PASSWORD),
        "bob": auth.create_user("bob", PASSWORD),
        "root": auth.create_user("root", PASSWORD, "admin"),
    }
    saver = InMemorySaver()
    runtime = Runtime(
        settings=auth_settings,
        deps=deps,
        app=build_graph(deps, checkpointer=saver),
        manager=deps.tasks,
        history=deps.history,
        artifacts=deps.artifacts,
        auth=auth,
        credentials=CredentialService(auth, auth_settings),
        orbit_registry=OrbitRegistry(),
    )

    async def fake_build_runtime(settings=None):
        return runtime

    monkeypatch.setattr(app_module, "build_runtime", fake_build_runtime)
    # A fresh limiter per test: it is module state, and the lockout test fills it.
    monkeypatch.setattr(app_module, "_limiter", app_module._LoginLimiter())
    with TestClient(app_module.app, base_url="https://testserver") as client:
        client.runtime = runtime
        client.accounts = accounts
        client.saver = saver
        client.tokens = {}

        def as_user(name: str | None):
            client.cookies.clear()
            if name is None:
                return client
            if name not in client.tokens:
                response = client.post(
                    "/api/login", json={"username": name, "password": PASSWORD}
                )
                assert response.status_code == 200, response.text
                client.tokens[name] = response.cookies[COOKIE]
                client.cookies.clear()
            # No domain: for a dotless host the jar files a server-set cookie
            # under "testserver.local", so one set for "testserver" is never sent.
            client.cookies.set(COOKIE, client.tokens[name])
            return client

        client.as_user = as_user
        yield client


def _frames(response) -> list[dict]:
    lines = response.text.splitlines()
    return [json.loads(line[6:]) for line in lines if line.startswith("data: ")]


# --- logins ------------------------------------------------------------------


def test_nothing_but_login_and_a_bare_health_answers_without_a_session(app_client):
    c = app_client.as_user(None)
    assert c.get("/api/health").json() == {"ok": True, "auth": True}
    refused = [
        ("get", "/api/sessions/s-anything"),
        ("get", "/api/sessions"),
        ("post", "/api/sessions"),
        ("get", "/api/artifacts?session_id=s-anything"),
        ("get", "/api/tasks"),
        ("get", "/api/tasks/t-anything"),
        ("post", "/api/tasks/t-anything/cancel"),
        ("get", "/api/settings"),
        ("get", "/api/me/credentials"),
        ("get", "/api/catalog"),
    ]
    for method, url in refused:
        assert getattr(c, method)(url).status_code == 401, url
    assert c.post("/api/chat", json={"message": "hi", "session_id": "s-x"}).status_code == 401
    assert c.put("/api/settings", json={"orbit_queue": "main"}).status_code == 401


def test_a_login_sets_a_strict_cookie_and_logout_ends_it(app_client):
    c = app_client.as_user(None)
    for username, password in (("alice", "wrong-password"), ("nobody", PASSWORD)):
        response = c.post("/api/login", json={"username": username, "password": password})
        assert response.status_code == 401

    response = c.post("/api/login", json={"username": "alice", "password": PASSWORD})
    assert response.status_code == 200
    header = response.headers["set-cookie"].lower()
    assert "httponly" in header and "secure" in header and "samesite=strict" in header
    token = response.cookies[COOKIE]
    # Stored only as a hash: the token itself is nowhere in the database.
    raw = app_client.runtime.auth.path.read_bytes()
    assert token.encode() not in raw

    c.cookies.set(COOKIE, token)
    assert c.get("/api/me").json()["user"]["username"] == "alice"
    c.post("/api/logout")
    c.cookies.set(COOKIE, token)
    assert c.get("/api/me").status_code == 401


def test_repeated_failures_lock_the_pair_out(app_client):
    c = app_client.as_user(None)
    for _ in range(10):
        c.post("/api/login", json={"username": "bob", "password": "not-his-password"})
    # Even the right password is refused while locked, or the lock would be moot.
    response = c.post("/api/login", json={"username": "bob", "password": PASSWORD})
    assert response.status_code == 429


def test_a_cross_origin_write_is_refused(app_client):
    c = app_client.as_user("alice")
    assert c.post("/api/sessions", headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/sessions", headers={"Origin": "https://testserver"}).status_code == 200


# --- ownership -----------------------------------------------------------------


def test_a_session_is_its_owners_alone(app_client, stub_tools):
    alice = app_client.as_user("alice")
    sid = alice.post("/api/sessions").json()["session_id"]
    turn = alice.post("/api/chat", json={"message": REDESIGN, "session_id": sid})
    assert turn.status_code == 200
    assert _frames(turn)[-1]["type"] == "done"
    mine = alice.get("/api/tasks").json()["tasks"]
    assert mine, "alice's own turn should have tasks"
    assert alice.get(f"/api/sessions/{sid}").status_code == 200
    assert [s["session_id"] for s in alice.get("/api/sessions").json()["sessions"]] == [sid]

    bob = app_client.as_user("bob")
    assert bob.get(f"/api/sessions/{sid}").status_code == 404
    assert bob.get(f"/api/artifacts?session_id={sid}").status_code == 404
    assert bob.post("/api/chat", json={"message": "hi", "session_id": sid}).status_code == 404
    # No session named used to mean every task in the process.
    assert bob.get("/api/tasks").json()["tasks"] == []
    for task in mine:
        assert bob.get(f"/api/tasks/{task['id']}").status_code == 404
        assert bob.post(f"/api/tasks/{task['id']}/cancel").status_code == 404


def test_a_session_id_cannot_be_claimed_by_naming_it(app_client, stub_tools):
    alice = app_client.as_user("alice")
    response = alice.post("/api/chat", json={"message": "hi", "session_id": "s-made-up"})
    assert response.status_code == 404


def test_the_operators_settings_are_an_admins(app_client):
    alice = app_client.as_user("alice")
    assert alice.get("/api/settings").status_code == 403
    assert alice.put("/api/settings", json={"orbit_queue": "main"}).status_code == 403
    root = app_client.as_user("root")
    assert root.get("/api/settings").status_code == 200
    # No admin token needed: the role replaces it (backlog A17).
    assert root.put("/api/settings", json={"orbit_queue": "main"}).status_code == 200


# --- credentials ----------------------------------------------------------------


def test_credentials_are_encrypted_and_never_returned(app_client):
    alice = app_client.as_user("alice")
    response = alice.put("/api/me/credentials", json={"anthropic_api_key": ALICE_KEY})
    assert response.status_code == 200, response.text
    view = alice.get("/api/me/credentials").json()["credentials"]["anthropic_api_key"]
    assert view["present"] is True and ALICE_KEY not in json.dumps(view)
    raw = app_client.runtime.auth.path.read_bytes()
    assert ALICE_KEY.encode() not in raw
    # Bob does not see that alice has a key, let alone which.
    bob = app_client.as_user("bob")
    theirs = bob.get("/api/me/credentials").json()["credentials"]
    assert theirs["anthropic_api_key"]["present"] is False


def test_a_users_broker_must_be_on_the_allow_list(app_client):
    alice = app_client.as_user("alice")
    bad = alice.put("/api/me/credentials", json={"orbit_broker_url": "https://elsewhere:8443"})
    assert bad.status_code == 422
    ok = alice.put("/api/me/credentials", json={"orbit_broker_url": BROKER + "/"})
    assert ok.status_code == 200
    key = alice.put(
        "/api/me/credentials",
        json={"orbit_broker_cert": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"},
    )
    assert key.status_code == 422


def test_credentials_without_a_secrets_key_are_refused(app_client):
    from pydantic import SecretStr

    runtime = app_client.runtime
    runtime.credentials = CredentialService(
        runtime.auth, runtime.settings.model_copy(update={"secrets_key": SecretStr("")})
    )
    alice = app_client.as_user("alice")
    response = alice.put("/api/me/credentials", json={"anthropic_api_key": ALICE_KEY})
    assert response.status_code == 503


def test_a_ciphertext_moved_to_another_user_does_not_decrypt(app_client):
    runtime = app_client.runtime
    alice, bob = app_client.accounts["alice"], app_client.accounts["bob"]
    runtime.credentials.save(alice, {"anthropic_api_key": ALICE_KEY})
    with sqlite3.connect(runtime.auth.path) as db:
        db.execute("UPDATE user_credentials SET user_id = ? WHERE user_id = ?", (bob.id, alice.id))
    runtime.credentials.forget(bob)
    values, errors = runtime.credentials.load(bob)
    assert values == {} and errors


def test_a_user_never_inherits_the_operators_key_but_an_admin_does(app_client):
    runtime = app_client.runtime
    alice, root = app_client.accounts["alice"], app_client.accounts["root"]
    assert runtime.credentials.resolve(alice, runtime.settings).settings.llm_available is False
    assert runtime.credentials.resolve(root, runtime.settings).settings.llm_key == OPERATOR_KEY
    runtime.credentials.save(alice, {"anthropic_api_key": ALICE_KEY})
    assert runtime.credentials.resolve(alice, runtime.settings).settings.llm_key == ALICE_KEY
    # And a plain user never gets the operator's HPC either.
    resolved = runtime.credentials.resolve(alice, runtime.settings)
    assert resolved.shared_orbit is False and resolved.settings.orbit_enabled is False


def test_a_decrypted_key_is_scrubbed_from_logs(app_client):
    runtime = app_client.runtime
    alice = app_client.accounts["alice"]
    runtime.credentials.save(alice, {"anthropic_api_key": ALICE_KEY})
    runtime.credentials.load(alice)
    assert ALICE_KEY in secret_values()
    runtime.credentials.forget(alice)
    assert ALICE_KEY not in secret_values()


@pytest.fixture
def recorded_llm(monkeypatch):
    """A stand-in model that records the key each call was built with.

    Without it, a user with a key makes the nodes call the real API — which the
    first version of these tests did, against the offline suite's one rule.
    """
    from designagent import llm as llm_module

    keys: list[str] = []

    class Reply:
        content = "{}"

    class Fake:
        def __init__(self, key):
            self.key = key

        async def ainvoke(self, messages):
            keys.append(self.key)
            return Reply()

    def fake_build_llm(settings=None, *, stream=False, **kwargs):
        settings = settings or llm_module.get_settings()
        return Fake(settings.llm_key) if settings.llm_available else None

    monkeypatch.setattr(llm_module, "build_llm", fake_build_llm)
    return keys


def test_no_user_secret_is_persisted_by_a_turn(app_client, stub_tools, recorded_llm):
    """The ATOM test's rule, for keys: nothing that must not persist rides the graph."""
    alice = app_client.as_user("alice")
    response = alice.put("/api/me/credentials", json={"anthropic_api_key": ALICE_KEY})
    assert response.status_code == 200
    sid = alice.post("/api/sessions").json()["session_id"]
    alice.post("/api/chat", json={"message": REDESIGN, "session_id": sid})

    # The turn really ran on alice's key, and only hers — not the operator's.
    assert recorded_llm and set(recorded_llm) == {ALICE_KEY}

    # repr, not json: the saver's keys are tuples, and its values serialized bytes
    # in which an ASCII key would still appear verbatim.
    saved = repr(app_client.saver.storage) + repr(app_client.saver.writes)
    assert ALICE_KEY not in saved
    tasks = json.dumps(app_client.runtime.manager.snapshot(), default=str)
    assert ALICE_KEY not in tasks
    # Flush stores and release Kuzu's exclusive Windows file lock before scanning bytes.
    app_client.runtime.history.close()
    data_dir = app_client.runtime.settings.data_dir
    for path in data_dir.rglob("*"):
        if path.is_file() and path.name != "auth.sqlite":
            assert ALICE_KEY.encode() not in path.read_bytes(), path


def test_authenticated_approval_to_fake_hpc_results(app_client, stub_tools, recorded_llm):
    from tests.test_approval import FakeHpc

    runtime = app_client.runtime
    runtime.settings = runtime.settings.model_copy(update={"max_rounds": 1, "fold_backend": "hpc"})
    runtime.deps.settings = runtime.settings
    fake = FakeHpc()
    runtime.manager.attach_hpc(fake)
    root = app_client.as_user("root")
    sid = root.post("/api/sessions").json()["session_id"]
    proposal = root.post("/api/chat", json={"message": REDESIGN, "session_id": sid})
    frames = _frames(proposal)
    assert frames[-1]["type"] == "done"
    assert any("HPC approval required" in f.get("text", "") for f in frames)
    assert fake.submitted == []

    bob = app_client.as_user("bob")
    refused = bob.post("/api/chat", json={"message": "approve hpc", "session_id": sid})
    assert refused.status_code == 404
    assert fake.submitted == []

    root = app_client.as_user("root")
    result = root.post("/api/chat", json={"message": "approve hpc", "session_id": sid})
    assert _frames(result)[-1]["type"] == "done"
    assert fake.submitted == ["proteinmpnn", "fold_sequence", "fold_sequence"]
    restored = root.get(f"/api/sessions/{sid}").json()["state"]
    assert restored["ensemble"]
    assert all(d["provenance"]["source"] == "proteinmpnn" for d in restored["ensemble"])
    markdown = next(a for a in restored["artifacts"] if a["kind"] == "markdown")
    rendered = root.get(markdown["url"])
    assert rendered.status_code == 200
    assert "proteinmpnn" in rendered.text and "hpc" in rendered.text
    assert any(f["type"] == "state" and f["state"].get("artifacts") for f in _frames(result))


# --- per-turn settings and interfaces -------------------------------------------


async def test_concurrent_turns_see_their_own_settings(deps):
    from pydantic import SecretStr

    def turn(user: str, key: str) -> TurnContext:
        settings = deps.settings.model_copy(update={"anthropic_api_key": SecretStr(key)})
        return TurnContext(user_id=user, username=user, role="user", settings=settings)

    seen: dict[str, list[str]] = {"a": [], "b": []}

    async def run(user: str, key: str) -> None:
        current_turn.set(turn(user, key))
        for _ in range(5):
            seen[user].append(deps.settings.llm_key)
            await asyncio.sleep(0)

    await asyncio.gather(run("a", "key-for-a-0123456789"), run("b", "key-for-b-0123456789"))
    assert set(seen["a"]) == {"key-for-a-0123456789"}
    assert set(seen["b"]) == {"key-for-b-0123456789"}
    # Outside any turn, the process's own settings, as before.
    assert deps.settings.llm_key == ""


async def test_an_llm_task_gets_the_key_as_an_argument_never_a_param(monkeypatch, settings):
    from designagent.tasks import local as local_module
    from designagent.tasks.base import TaskSpec
    from designagent.tasks.local import LocalTaskInterface
    from designagent.tasks.registry import CATALOG, TaskDef
    from pydantic import SecretStr

    received: dict = {}

    async def body(prompt: str = "", _llm=None, **_):
        received.update(_llm or {})
        return {"ok": True}

    original = CATALOG["generate_visualization"]
    monkeypatch.setitem(
        CATALOG,
        "generate_visualization",
        TaskDef(original.name, body, "local", original.description, needs_llm=True),
    )
    interface = LocalTaskInterface()
    spec = TaskSpec(name="generate_visualization", params={"prompt": "show it"})
    current_turn.set(
        TurnContext(
            user_id="u",
            username="u",
            role="user",
            settings=settings.model_copy(update={"anthropic_api_key": SecretStr(ALICE_KEY)}),
        )
    )
    handle = await interface.submit(spec)
    await handle.future
    assert received["api_key"] == ALICE_KEY
    assert "_llm" not in spec.params and ALICE_KEY not in json.dumps(spec.params)
    assert local_module.llm_credentials("pdb_lookup") is None


async def test_each_users_orbit_interface_is_their_own(monkeypatch, settings):
    """Distinct client names, or two users' interfaces steal replies (backlog A18)."""
    from designagent import runtime as runtime_module
    from designagent.auth.store import User
    from designagent.runtime import OrbitRegistry

    made: list[str] = []

    class Fake:
        connected = True

        def __init__(self, name):
            self.name = name

        async def close(self):
            self.connected = False

    async def fake_make_orbit(s):
        made.append(s.orbit_client_name)
        return Fake(s.orbit_client_name), ""

    monkeypatch.setattr(runtime_module, "_make_orbit", fake_make_orbit)
    registry = OrbitRegistry()
    configured = settings.model_copy(
        update={"orbit_enabled": True, "orbit_broker_url": BROKER, "orbit_endpoint": "amarel3"}
    )
    a, b = User("u-a", "alice", "user"), User("u-b", "bob", "user")
    ia, _ = await registry.get(a, configured)
    ib, _ = await registry.get(b, configured)
    again, _ = await registry.get(a, configured)
    assert ia is again and ia is not ib
    assert len(set(made)) == 2 and all(n.startswith("designagent-") for n in made)
    moved, _ = await registry.get(a, configured.model_copy(update={"orbit_endpoint": "other"}))
    assert moved is not ia and ia.connected is False


def test_validate_rejects_rather_than_repairs(settings):
    with pytest.raises(CredentialError):
        validate_one(settings, "orbit_endpoint", "amarel3; rm -rf /")
    with pytest.raises(CredentialError):
        validate_one(settings, "anthropic_api_key", "sk ant with spaces")
    with pytest.raises(CredentialError):
        validate_one(settings, "orbit_psij_executor", "slurm")


def validate_one(settings, name, value):
    from designagent.auth.credentials import validate

    return validate({name: value}, settings)
