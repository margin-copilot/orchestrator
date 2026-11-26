from types import SimpleNamespace

import httpx
import pytest

from orchestrator.main import create_app


async def test_fake_mode_requires_tokens(monkeypatch):
    monkeypatch.setenv("ADAPTERS", "fake")
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://o") as c:
        assert (await c.post("/chat", json={"question": "hi"})).status_code == 401


AZURE = {
    "ADAPTERS": "azure",
    "TENANT_ID": "11111111-1111-1111-1111-111111111111",
    "CLIENT_ID": "orchestrator-client-id",
    "AZURE_CLIENT_ID": "managed-identity-client-id",
    "ALLOWED_CALLERS": "cli-client-id",
    "DATA_AGENT_URL": "http://data-agent/",
    "DATA_AGENT_SCOPE": "api://data-agent-client-id/access_as_user",
    "POLICY_AGENT_URL": "http://policy-agent/",
    "POLICY_AGENT_SCOPE": "api://policy-agent-client-id/access_as_user",
    "ACTION_AGENT_URL": "http://action-agent/",
    "ACTION_AGENT_SCOPE": "api://action-agent-client-id/access_as_user",
    "FOUNDRY_PROJECT_ENDPOINT": "https://example.services.ai.azure.com/api/projects/example",
    "FOUNDRY_MODEL": "gpt-5-mini",
    "TABLE_ENDPOINT": "https://stexample.table.core.windows.net/",
    "CONVERSATIONS_TABLE": "conversations",
    "ALLOWED_ORIGINS": "https://web.example,http://localhost:5173",
}


@pytest.fixture
def azure_env(monkeypatch):
    for name, value in AZURE.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr("orchestrator.main.OboClient", lambda *args: SimpleNamespace(exchange=None))


def test_azure_mode_composes(azure_env):
    assert create_app() is not None


@pytest.mark.parametrize("missing", sorted(set(AZURE) - {"ADAPTERS"}))
def test_azure_mode_has_no_defaults(azure_env, monkeypatch, missing):
    monkeypatch.delenv(missing)
    with pytest.raises(KeyError):
        create_app()


@pytest.mark.parametrize("value", ["-1", "soon"])
def test_a_bad_stream_grace_is_refused(monkeypatch, value):
    monkeypatch.setenv("ADAPTERS", "fake")
    monkeypatch.setenv("STREAM_SHUTDOWN_GRACE_SECONDS", value)
    with pytest.raises(ValueError, match="STREAM_SHUTDOWN_GRACE_SECONDS"):
        create_app()


def test_adapters_must_be_set_explicitly(monkeypatch):
    monkeypatch.delenv("ADAPTERS", raising=False)
    with pytest.raises(KeyError):
        create_app()
    monkeypatch.setenv("ADAPTERS", "prod")
    with pytest.raises(ValueError, match="azure or fake"):
        create_app()
