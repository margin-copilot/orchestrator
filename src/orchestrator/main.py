"""Composition root. ADAPTERS=azure|fake picks the adapters; there is no default."""

import os

import httpx
from margin_copilot_shared.security.obo import OboClient, managed_identity_assertion
from margin_copilot_shared.security.tokens import entra_validator
from margin_copilot_shared.testing.issuer import LocalIssuer
from starlette.applications import Starlette

from orchestrator.adapters.inbound.http_api import create_http_app
from orchestrator.adapters.outbound.data_agent import A2ADataAgent, FakeDataAgent


def create_app() -> Starlette:
    env = os.environ
    adapters = env["ADAPTERS"]
    if adapters == "azure":
        tenant_id, client_id = env["TENANT_ID"], env["CLIENT_ID"]
        callers = frozenset(env["ALLOWED_CALLERS"].split(","))
        obo = OboClient(tenant_id, client_id, managed_identity_assertion(env["AZURE_CLIENT_ID"]))
        data_agent = A2ADataAgent(
            env["DATA_AGENT_URL"],
            env["DATA_AGENT_SCOPE"],
            obo.exchange,
            # Scale-from-zero, a database waking from auto-pause and several model turns.
            httpx.AsyncClient(timeout=200),
        )
        return create_http_app(data_agent, entra_validator(tenant_id, client_id, callers))
    if adapters == "fake":
        # A fresh in-memory key per process: nobody outside this process can mint a token for it.
        validator = LocalIssuer().validator(
            audience="orchestrator", allowed_callers=frozenset({"cli"})
        )
        return create_http_app(FakeDataAgent(), validator)
    raise ValueError(f"ADAPTERS must be azure or fake, not {adapters!r}")
