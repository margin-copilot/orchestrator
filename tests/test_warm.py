"""POST /warm (D-044): wake the agents and the database at sign-in, returning nothing."""

import asyncio

import httpx
import pytest
from margin_copilot_shared.testing.issuer import LocalIssuer

from orchestrator.adapters.inbound.http_api import create_http_app
from orchestrator.adapters.outbound.a2a_agent import WARM_LIMIT, A2AAgent
from orchestrator.adapters.outbound.composer import FakeComposer
from orchestrator.adapters.outbound.condenser import FakeCondenser
from orchestrator.adapters.outbound.fakes import MemoryConversationStore
from orchestrator.adapters.outbound.router import FakeRouter
from tests.test_http_api import CLI, ORCHESTRATOR, bearer

SCOPE = "api://data-agent-client-id/access_as_user"


@pytest.fixture
def issuer():
    return LocalIssuer()


class WarmingAgent:
    def __init__(self, gate=None):
        self.gate, self.tokens = gate, []

    async def warm(self, user_token):
        self.tokens.append(user_token)
        if self.gate is not None:
            await self.gate.wait()


def client_for(issuer, agents):
    app = create_http_app(
        FakeRouter(),
        agents,
        FakeComposer(),
        FakeCondenser(),
        MemoryConversationStore(),
        issuer.validator(audience=ORCHESTRATOR, allowed_callers=frozenset({CLI})),
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://orch")


async def settle():
    for _ in range(20):
        await asyncio.sleep(0)


async def test_warm_answers_at_once_and_wakes_every_agent(issuer):
    agents = {name: WarmingAgent() for name in ("data-agent", "policy-agent", "action-agent")}
    headers = bearer(issuer)
    response = await client_for(issuer, agents).post("/warm", headers=headers)
    await settle()
    assert (response.status_code, response.json()) == (202, {})
    token = headers["Authorization"].removeprefix("Bearer ")
    assert [agent.tokens for agent in agents.values()] == [[token], [token], [token]]


async def test_a_warm_call_while_one_runs_does_nothing(issuer):
    gate = asyncio.Event()
    agent = WarmingAgent(gate)
    client = client_for(issuer, {"data-agent": agent})
    assert (await client.post("/warm", headers=bearer(issuer))).status_code == 202
    await settle()
    assert (await client.post("/warm", headers=bearer(issuer))).status_code == 202
    await settle()
    assert len(agent.tokens) == 1
    gate.set()
    await settle()
    assert (await client.post("/warm", headers=bearer(issuer))).status_code == 202
    await settle()
    assert len(agent.tokens) == 2  # the first one finished: a new warm-up can start


async def test_warm_needs_a_token(issuer):
    response = await client_for(issuer, {"data-agent": WarmingAgent()}).post("/warm")
    assert response.status_code == 401


async def test_the_data_agent_is_warmed_with_its_own_token():
    seen, exchanged = [], []

    def handler(request):
        seen.append((request.method, str(request.url), request.headers.get("authorization")))
        return httpx.Response(204 if request.method == "POST" else 200, json={})

    def exchange(user_token, scopes):
        exchanged.append((user_token, list(scopes)))
        return "obo-token-for-data-agent"

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    agent = A2AAgent("data-agent", "http://data-agent/", SCOPE, exchange, http, wakes_database=True)
    await agent.warm("user-token")
    assert exchanged == [("user-token", [SCOPE])]
    assert seen == [
        ("GET", "http://data-agent/.well-known/agent-card.json", None),
        ("POST", "http://data-agent/warm", "Bearer obo-token-for-data-agent"),
    ]


async def test_another_agent_is_only_started():
    seen = []

    def handler(request):
        seen.append((request.method, str(request.url)))
        return httpx.Response(200, json={})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    agent = A2AAgent("policy-agent", "http://policy-agent/", SCOPE, lambda *a: "never", http)
    await agent.warm("user-token")
    assert seen == [("GET", "http://policy-agent/.well-known/agent-card.json")]


async def test_a_failing_warm_up_never_raises(caplog):
    def handler(request):
        raise httpx.ConnectError("down")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    agent = A2AAgent(
        "data-agent", "http://data-agent/", SCOPE, lambda *a: "t", http, wakes_database=True
    )
    await agent.warm("user-token")
    assert "warm failed: data-agent: ConnectError" in caplog.text


def test_the_warm_up_outlasts_a_cold_start_and_a_database_resume():
    # Measured from cold: the data agent's start about 30 s, then the database's resume about 51 s.
    # A shorter limit logs "warm failed" for a warm-up that works.
    assert WARM_LIMIT >= 120
