"""Contract test: the A2A adapter against a stand-in data agent built from the same SDK."""

import asyncio

import httpx
import pytest
from a2a.helpers import new_data_message, new_text_message
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from margin_copilot_shared.contracts import AgentReply
from margin_copilot_shared.security.context import (
    RequestContext,
    current_request,
    request_scope,
)
from margin_copilot_shared.security.middleware import AuthMiddleware
from margin_copilot_shared.security.obo import OboFailed
from margin_copilot_shared.security.tokens import Caller
from margin_copilot_shared.testing.issuer import LocalIssuer
from starlette.applications import Starlette
from starlette.middleware import Middleware

from orchestrator.adapters.outbound.data_agent import A2ADataAgent
from orchestrator.application.ask import DataAgentUnavailable

DATA_AGENT = "data-agent-client-id"
ORCHESTRATOR = "orchestrator-client-id"
SCOPE = "api://data-agent-client-id/access_as_user"
URL = "http://data-agent/"


class StandIn(AgentExecutor):
    def __init__(self, reply=None, fail=False):
        self.reply = reply
        self.fail = fail

    async def execute(self, context, event_queue):
        if self.fail:
            raise RuntimeError("tool failed")
        if isinstance(self.reply, str):
            await event_queue.enqueue_event(new_text_message(self.reply))
            return
        reply = self.reply or {"answer": f"sees {current_request().caller.oid}", "refusal": None}
        await event_queue.enqueue_event(new_data_message(reply))

    async def cancel(self, context, event_queue):
        raise NotImplementedError


def stand_in_http(issuer, reply=None, fail=False, paths=None):
    card = AgentCard(
        name="data-agent",
        description="stand-in",
        version="0",
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[
            AgentInterface(protocol_binding="JSONRPC", url=URL, protocol_version="1.0")
        ],
        skills=[AgentSkill(id="whoami", name="whoami", description="d", tags=["t"])],
    )
    handler = DefaultRequestHandler(
        agent_executor=StandIn(reply, fail), task_store=InMemoryTaskStore(), agent_card=card
    )
    validator = issuer.validator(audience=DATA_AGENT, allowed_callers=frozenset({ORCHESTRATOR}))
    app = Starlette(
        routes=[*create_agent_card_routes(card), *create_jsonrpc_routes(handler, "/")],
        middleware=[
            Middleware(
                AuthMiddleware,
                validator=validator,
                public_paths=frozenset({AGENT_CARD_WELL_KNOWN_PATH}),
            )
        ],
    )
    hooks = {"request": [lambda request: _record(paths, request)]} if paths is not None else {}
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), event_hooks=hooks)


async def _record(paths, request):
    paths.append(request.url.path)


def entra_stand_in(issuer, azp=ORCHESTRATOR):
    """Stands in for the OBO exchange: a data-agent token for the same user, never the input."""

    def exchange(user_token, scopes):
        assert scopes == [SCOPE]
        return issuer.issue(audience=DATA_AGENT, azp=azp, oid=user_token.removeprefix("token-"))

    return exchange


def as_user(oid):
    caller = Caller(oid=oid, tid="t", azp="cli", scopes=frozenset({"access_as_user"}))
    return request_scope(RequestContext(caller=caller, token=f"token-{oid}"))


@pytest.fixture
def issuer():
    return LocalIssuer()


async def test_each_call_carries_a_fresh_token_for_the_same_user(issuer):
    agent = A2ADataAgent(URL, SCOPE, entra_stand_in(issuer), stand_in_http(issuer))
    with as_user("north-mgr"):
        assert await agent.ask("Dairy margin?") == AgentReply(answer="sees north-mgr")


async def test_concurrent_users_stay_separate(issuer):
    agent = A2ADataAgent(URL, SCOPE, entra_stand_in(issuer), stand_in_http(issuer))

    async def ask_as(oid):
        with as_user(oid):
            return await agent.ask("Dairy margin?")

    oids = [f"user-{n}" for n in range(10)]
    replies = await asyncio.gather(*(ask_as(oid) for oid in oids))
    assert [reply.answer for reply in replies] == [f"sees {o}" for o in oids]


async def test_rejected_token_becomes_data_agent_unavailable(issuer):
    agent = A2ADataAgent(URL, SCOPE, entra_stand_in(issuer, azp="cli"), stand_in_http(issuer))
    with as_user("north-mgr"), pytest.raises(DataAgentUnavailable, match="403"):
        await agent.ask("Dairy margin?")


async def test_failed_exchange_never_calls_the_agent(issuer):
    def failing(user_token, scopes):
        raise OboFailed("invalid_grant")

    agent = A2ADataAgent(URL, SCOPE, failing, stand_in_http(issuer))
    with as_user("north-mgr"), pytest.raises(DataAgentUnavailable, match="invalid_grant"):
        await agent.ask("Dairy margin?")


async def test_a_refusal_comes_through(issuer):
    http = stand_in_http(issuer, reply={"answer": "That's the South.", "refusal": "no_access"})
    agent = A2ADataAgent(URL, SCOPE, entra_stand_in(issuer), http)
    with as_user("north-mgr"):
        assert (await agent.ask("South margin?")).refusal == "no_access"


@pytest.mark.parametrize(
    "reply",
    [
        {"answer": "x" * 4001, "refusal": None},
        {"answer": "ok", "refusal": "maybe"},
        {"answer": "ok", "refusal": None, "approved": True},
        {"refusal": None},
        "plain text instead of a data part",
    ],
)
async def test_replies_outside_the_contract_are_refused(issuer, reply):
    agent = A2ADataAgent(URL, SCOPE, entra_stand_in(issuer), stand_in_http(issuer, reply=reply))
    with as_user("north-mgr"), pytest.raises(DataAgentUnavailable, match="contract|data part"):
        await agent.ask("Dairy margin?")


async def test_the_agent_card_is_never_fetched(issuer):
    paths = []
    agent = A2ADataAgent(URL, SCOPE, entra_stand_in(issuer), stand_in_http(issuer, paths=paths))
    with as_user("north-mgr"):
        await agent.ask("Dairy margin?")
    assert paths == ["/"]


async def test_unreachable_agent_becomes_data_agent_unavailable():
    def refuse(request):
        raise httpx.ConnectError("connection refused")

    http = httpx.AsyncClient(transport=httpx.MockTransport(refuse))
    agent = A2ADataAgent(URL, SCOPE, lambda token, scopes: "t", http)
    with as_user("north-mgr"), pytest.raises(DataAgentUnavailable):
        await agent.ask("Dairy margin?")


async def test_failing_tool_in_the_agent_becomes_data_agent_unavailable(issuer):
    agent = A2ADataAgent(URL, SCOPE, entra_stand_in(issuer), stand_in_http(issuer, fail=True))
    with as_user("north-mgr"), pytest.raises(DataAgentUnavailable, match="data-agent call failed"):
        await agent.ask("Dairy margin?")


async def test_unexpected_exchange_error_becomes_data_agent_unavailable(issuer):
    def broken(user_token, scopes):
        raise ConnectionError("identity endpoint unreachable")

    agent = A2ADataAgent(URL, SCOPE, broken, stand_in_http(issuer))
    with as_user("north-mgr"), pytest.raises(DataAgentUnavailable, match="ConnectionError"):
        await agent.ask("Dairy margin?")
