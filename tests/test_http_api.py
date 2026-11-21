import httpx
import pytest
from margin_copilot_shared.contracts import AgentReply
from margin_copilot_shared.testing.issuer import LocalIssuer

from orchestrator.adapters.inbound.http_api import create_http_app
from orchestrator.adapters.outbound.a2a_agent import FakeAgent
from orchestrator.adapters.outbound.composer import FakeComposer
from orchestrator.adapters.outbound.router import FakeRouter
from orchestrator.application.ask import AgentUnavailable

ORCHESTRATOR = "orchestrator-client-id"
CLI = "cli-client-id"


@pytest.fixture
def issuer():
    return LocalIssuer()


def client_for(issuer, data_agent=None, router=None, policy_agent=None):
    agents = {
        "data-agent": data_agent or FakeAgent("data-agent"),
        "policy-agent": policy_agent or FakeAgent("policy-agent"),
    }
    app = create_http_app(
        router or FakeRouter(),
        agents,
        FakeComposer(),
        issuer.validator(audience=ORCHESTRATOR, allowed_callers=frozenset({CLI})),
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://orch")


def bearer(issuer, azp=CLI, oid="north-mgr"):
    return {"Authorization": f"Bearer {issuer.issue(audience=ORCHESTRATOR, azp=azp, oid=oid)}"}


async def test_signed_in_user_gets_an_answer_as_themselves(issuer):
    response = await client_for(issuer).post(
        "/chat", json={"question": "who am I?"}, headers=bearer(issuer)
    )
    assert response.status_code == 200
    assert response.json() == {
        "answer": "fake data-agent sees user north-mgr",
        "refusal": None,
        "agents": ["data-agent"],
        "citations": [],
    }


async def test_a_refusal_from_the_data_agent_is_reported(issuer):
    class Refuses:
        async def ask(self, question):
            return AgentReply(answer="That's the South's data.", refusal="no_access")

    response = await client_for(issuer, Refuses()).post(
        "/chat", json={"question": "South margin?"}, headers=bearer(issuer)
    )
    assert response.json()["refusal"] == "no_access"
    assert response.json()["agents"] == ["data-agent"]


async def test_no_token_is_401(issuer):
    response = await client_for(issuer).post("/chat", json={"question": "who am I?"})
    assert response.status_code == 401


async def test_unlisted_client_app_is_403(issuer):
    response = await client_for(issuer).post(
        "/chat", json={"question": "who am I?"}, headers=bearer(issuer, azp="unlisted-cli")
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("body", "status"),
    [
        (b"not json", 400),
        (b'["a list"]', 400),
        (b'{"question": ""}', 400),
        (b'{"question": 42}', 400),
        (b'{"question": "' + b"x" * 2001 + b'"}', 413),
        (b'{"question": "' + b"x" * 20000 + b'"}', 413),
    ],
)
async def test_bad_bodies_are_rejected(issuer, body, status):
    headers = {**bearer(issuer), "content-type": "application/json"}
    response = await client_for(issuer).post("/chat", content=body, headers=headers)
    assert response.status_code == status


async def test_unavailable_data_agent_is_502_without_internal_details(issuer):
    class Down:
        async def ask(self, question):
            raise AgentUnavailable("data-agent", "call failed: 503 for http://internal-name/")

    response = await client_for(issuer, Down()).post(
        "/chat", json={"question": "who am I?"}, headers=bearer(issuer)
    )
    assert response.status_code == 502
    assert response.json() == {"error": "data-agent unavailable"}


async def test_an_unavailable_agent_names_itself(issuer):
    class Down:
        async def ask(self, question):
            raise AgentUnavailable("policy-agent", "call failed: 503")

    client = client_for(issuer, router=FakeRouter("policy-agent"), policy_agent=Down())
    response = await client.post("/chat", json={"question": "Tier?"}, headers=bearer(issuer))
    assert response.status_code == 502
    assert response.json() == {"error": "policy-agent unavailable"}


async def test_citations_are_reported(issuer):
    class Cites:
        async def ask(self, question):
            return AgentReply(answer="Tier B.", citations=["PP-02 §2"])

    client = client_for(issuer, router=FakeRouter("policy-agent"), policy_agent=Cites())
    response = await client.post("/chat", json={"question": "Tier?"}, headers=bearer(issuer))
    assert response.json()["citations"] == ["PP-02 §2"]
    assert response.json()["agents"] == ["policy-agent"]
