"""POST /chat as server-sent events (D-045): steps, the data agent's reply, then the JSON body."""

import asyncio
import json

import httpx
import pytest
from margin_copilot_shared.testing.issuer import LocalIssuer
from sse_starlette.sse import AppStatus

from orchestrator.adapters.inbound.http_api import create_http_app
from orchestrator.adapters.outbound.a2a_agent import FakeAgent
from orchestrator.adapters.outbound.composer import FakeComposer
from orchestrator.adapters.outbound.condenser import FakeCondenser
from orchestrator.adapters.outbound.fakes import MemoryConversationStore
from orchestrator.adapters.outbound.router import FakeRouter
from orchestrator.application.ask import AgentUnavailable
from tests.test_http_api import CLI, ORCHESTRATOR, bearer

STREAM = {"Accept": "text/event-stream"}
WEB = "https://web.example"


@pytest.fixture
def issuer():
    return LocalIssuer()


def app_for(issuer, agents=None, router=None, store=None, **kw):
    return create_http_app(
        router or FakeRouter("data-agent", "policy-agent"),
        agents or {name: FakeAgent(name) for name in ("data-agent", "policy-agent")},
        FakeComposer(),
        FakeCondenser(),
        store or MemoryConversationStore(),
        issuer.validator(audience=ORCHESTRATOR, allowed_callers=frozenset({CLI})),
        **kw,
    )


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://orch")


def events(text):
    """(event, data) pairs from a text/event-stream body; comments (pings) are skipped."""
    found = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        lines = [line for line in block.split("\n") if line and not line.startswith(":")]
        if lines:
            fields = dict(line.split(": ", 1) for line in lines)
            found.append((fields.get("event", "message"), json.loads(fields["data"])))
    return found


async def test_a_streamed_turn_reports_steps_then_done(issuer):
    app = app_for(issuer)
    streamed = await client(app).post(
        "/chat", json={"question": "q"}, headers=bearer(issuer) | STREAM
    )
    assert streamed.headers["content-type"].startswith("text/event-stream")
    found = events(streamed.text)
    assert [(e, d.get("step")) for e, d in found] == [
        ("step", "route"),
        ("step", "data-agent"),
        ("step", "policy-agent"),
        ("lead", None),
        ("step", "compose"),
        ("done", None),
    ]
    assert found[3][1] == {"answer": "fake data-agent sees user north-mgr"}
    plain = (await client(app).post("/chat", json={"question": "q"}, headers=bearer(issuer))).json()
    done = found[-1][1]
    assert len(done.pop("conversation_id")) == 32
    plain.pop("conversation_id")
    assert done == plain


async def test_without_the_stream_header_the_reply_is_json(issuer):
    response = await client(app_for(issuer)).post(
        "/chat", json={"question": "q"}, headers=bearer(issuer)
    )
    assert response.headers["content-type"] == "application/json"
    assert set(response.json()) == {"answer", "refusal", "agents", "citations", "conversation_id"}


async def test_a_bad_body_is_still_a_json_400(issuer):
    response = await client(app_for(issuer)).post(
        "/chat", json={"question": ""}, headers=bearer(issuer) | STREAM
    )
    assert (response.status_code, response.json()) == (400, {"error": "question is required"})


async def test_no_token_is_401_before_any_stream(issuer):
    response = await client(app_for(issuer)).post("/chat", json={"question": "q"}, headers=STREAM)
    assert response.status_code == 401
    assert not response.headers["content-type"].startswith("text/event-stream")


class Down:
    async def ask(self, question):
        raise AgentUnavailable("data-agent", "connect failed")


class Slow:
    async def ask(self, question):
        await asyncio.sleep(5)


class Broken:
    async def ask(self, question):
        raise RuntimeError("a bug")


class BrokenStore(MemoryConversationStore):
    async def add(self, owner, conversation_id, turn):
        raise OSError("table storage down")


@pytest.mark.parametrize(
    ("setup", "body", "message", "status"),
    [
        ({"agents": {"data-agent": Down()}}, {}, "data-agent unavailable", 502),
        (
            {"agents": {"data-agent": Slow()}, "turn_limit": 0.05},
            {},
            "orchestrator unavailable",
            502,
        ),
        ({}, {"conversation_id": "f" * 32}, "conversation not found", 404),
        ({"store": BrokenStore()}, {}, "conversations unavailable", 502),
        ({"agents": {"data-agent": Broken()}}, {}, "internal error", 500),
    ],
)
async def test_a_failure_after_the_stream_starts_is_one_error_event(
    issuer, setup, body, message, status
):
    app = app_for(issuer, router=FakeRouter("data-agent"), **setup)
    response = await client(app).post(
        "/chat", json={"question": "q", **body}, headers=bearer(issuer) | STREAM
    )
    found = events(response.text)
    assert found[-1] == ("error", {"error": message, "status": status})
    assert [e for e, _ in found].count("error") == 1 and "done" not in [e for e, _ in found]


async def test_a_turn_finishes_and_is_stored_when_the_client_leaves(issuer):
    gate, store = asyncio.Event(), MemoryConversationStore()

    class Gated(FakeAgent):
        async def ask(self, question):
            await gate.wait()
            return await super().ask(question)

    app = app_for(
        issuer, agents={"data-agent": Gated("data-agent")}, router=FakeRouter(), store=store
    )
    first_body = asyncio.Event()
    request = {"type": "http.request", "body": b'{"question": "q"}', "more_body": False}

    async def receive():
        nonlocal request
        if request:
            sent, request = request, None
            return sent
        await first_body.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            first_body.set()

    token = bearer(issuer)["Authorization"].encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/chat",
        "raw_path": b"/chat",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"authorization", token),
            (b"accept", b"text/event-stream"),
            (b"content-type", b"application/json"),
        ],
        "client": ("127.0.0.1", 1),
        "server": ("orch", 80),
    }
    await asyncio.wait_for(app(scope, receive, send), 2)  # the response ends with the client
    gate.set()
    for _ in range(100):
        if store._turns:
            break
        await asyncio.sleep(0.01)
    assert [len(turns) for turns in store._turns.values()] == [1]


async def test_a_shutdown_lets_an_open_stream_finish(issuer):
    # A deploy stops the old replica with SIGTERM; a JSON turn in flight still gets its reply.
    gate = asyncio.Event()

    class Gated(FakeAgent):
        async def ask(self, question):
            await gate.wait()
            return await super().ask(question)

    app = app_for(issuer, agents={"data-agent": Gated("data-agent")}, router=FakeRouter())
    AppStatus.should_exit = True  # what uvicorn's SIGTERM handler sets
    try:
        asyncio.get_running_loop().call_later(1.0, gate.set)  # after the watcher's 0.5 s poll
        response = await client(app).post(
            "/chat", json={"question": "q"}, headers=bearer(issuer) | STREAM
        )
    finally:
        AppStatus.should_exit = False
    assert events(response.text)[-1][0] == "done"


async def test_the_stream_grace_comes_from_the_app(issuer):
    gate = asyncio.Event()

    class Gated(FakeAgent):
        async def ask(self, question):
            await gate.wait()
            return await super().ask(question)

    app = app_for(
        issuer, agents={"data-agent": Gated("data-agent")}, router=FakeRouter(), stream_grace=0.01
    )
    request = {"type": "http.request", "body": b'{"question": "q"}', "more_body": False}
    sent = []

    async def receive():
        nonlocal request
        if request:
            message, request = request, None
            return message
        await asyncio.Event().wait()  # the client stays connected

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/chat",
        "raw_path": b"/chat",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"authorization", bearer(issuer)["Authorization"].encode()),
            (b"accept", b"text/event-stream"),
            (b"content-type", b"application/json"),
        ],
        "client": ("127.0.0.1", 1),
        "server": ("orch", 80),
    }
    AppStatus.should_exit = True
    try:
        await asyncio.wait_for(app(scope, receive, send), 5)  # the default grace is 25 s
    finally:
        AppStatus.should_exit = False
        gate.set()
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    assert b"event: done" not in body


async def test_a_streamed_reply_carries_cors_headers_for_the_web_app(issuer):
    app = app_for(issuer, allowed_origins=[WEB])
    response = await client(app).post(
        "/chat", json={"question": "q"}, headers=bearer(issuer) | STREAM | {"Origin": WEB}
    )
    assert response.headers["access-control-allow-origin"] == WEB
    assert events(response.text)[-1][0] == "done"
