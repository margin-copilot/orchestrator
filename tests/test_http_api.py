import httpx
import pytest
from margin_copilot_shared.testing.issuer import LocalIssuer

from orchestrator.adapters.inbound.http_api import create_http_app
from orchestrator.adapters.outbound.data_agent import FakeDataAgent
from orchestrator.application.ask import DataAgentUnavailable

ORCHESTRATOR = "orchestrator-client-id"
CLI = "cli-client-id"


@pytest.fixture
def issuer():
    return LocalIssuer()


def client_for(issuer, data_agent=None):
    app = create_http_app(
        data_agent or FakeDataAgent(),
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
    assert response.json() == {"answer": "fake data-agent sees user north-mgr"}


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
            raise DataAgentUnavailable("data-agent call failed: 503 for http://internal-name/")

    response = await client_for(issuer, Down()).post(
        "/chat", json={"question": "who am I?"}, headers=bearer(issuer)
    )
    assert response.status_code == 502
    assert response.json() == {"error": "data-agent unavailable"}
