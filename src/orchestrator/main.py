"""Composition root. ADAPTERS=azure|fake picks the adapters; there is no default."""

import os

import httpx
from agent_framework.foundry import FoundryChatClient
from agent_framework.observability import enable_instrumentation
from azure.data.tables.aio import TableClient
from azure.identity.aio import ManagedIdentityCredential
from margin_copilot_shared.security.obo import OboClient, managed_identity_assertion
from margin_copilot_shared.security.tokens import entra_validator
from margin_copilot_shared.telemetry import configure_telemetry, instrument_app
from margin_copilot_shared.testing.issuer import LocalIssuer
from starlette.applications import Starlette
from starlette.types import ASGIApp

from orchestrator.adapters.inbound.http_api import create_http_app
from orchestrator.adapters.outbound.a2a_agent import A2AAgent, FakeAgent
from orchestrator.adapters.outbound.composer import FakeComposer, FoundryComposer
from orchestrator.adapters.outbound.condenser import FakeCondenser, FoundryCondenser
from orchestrator.adapters.outbound.fakes import MemoryConversationStore
from orchestrator.adapters.outbound.router import FakeRouter, FoundryRouter
from orchestrator.adapters.outbound.table_conversations import TableConversationStore
from orchestrator.application.ask import AGENTS

ROUTER_TIMEOUT = 30
COMPOSER_TIMEOUT = 30
CONDENSER_TIMEOUT = 20


def create_app() -> ASGIApp:
    if configure_telemetry("orchestrator", os.environ):
        enable_instrumentation(enable_sensitive_data=False)
    return instrument_app(_app())


def _app() -> Starlette:
    env = os.environ
    adapters = env["ADAPTERS"]
    if adapters == "azure":
        tenant_id, client_id, identity = env["TENANT_ID"], env["CLIENT_ID"], env["AZURE_CLIENT_ID"]
        callers = frozenset(env["ALLOWED_CALLERS"].split(","))
        obo = OboClient(tenant_id, client_id, managed_identity_assertion(identity))
        # Scale-from-zero, a database waking from auto-pause and several model turns.
        http = httpx.AsyncClient(timeout=200)
        agents = {
            name: A2AAgent(name, env[f"{prefix}_URL"], env[f"{prefix}_SCOPE"], obo.exchange, http)
            for name, prefix in (
                ("data-agent", "DATA_AGENT"),
                ("policy-agent", "POLICY_AGENT"),
                ("action-agent", "ACTION_AGENT"),
            )
        }
        credential = ManagedIdentityCredential(client_id=identity)
        model = FoundryChatClient(
            project_endpoint=env["FOUNDRY_PROJECT_ENDPOINT"],
            model=env["FOUNDRY_MODEL"],
            credential=credential,
            timeout=max(ROUTER_TIMEOUT, COMPOSER_TIMEOUT),
        )
        router = FoundryRouter(model, limit=ROUTER_TIMEOUT)
        composer = FoundryComposer(model, limit=COMPOSER_TIMEOUT)
        condenser = FoundryCondenser(model, limit=CONDENSER_TIMEOUT)
        table = TableClient(
            endpoint=env["TABLE_ENDPOINT"],
            table_name=env["CONVERSATIONS_TABLE"],
            credential=credential,
        )
        store = TableConversationStore(table)
        validator = entra_validator(tenant_id, client_id, callers)
        origins = [origin.strip() for origin in env["ALLOWED_ORIGINS"].split(",") if origin.strip()]
        return create_http_app(
            router, agents, composer, condenser, store, validator, allowed_origins=origins
        )
    if adapters == "fake":
        # A fresh in-memory key per process: nobody outside this process can mint a token for it.
        validator = LocalIssuer().validator(
            audience="orchestrator", allowed_callers=frozenset({"cli"})
        )
        agents = {name: FakeAgent(name) for name in AGENTS}
        return create_http_app(
            FakeRouter(),
            agents,
            FakeComposer(),
            FakeCondenser(),
            MemoryConversationStore(),
            validator,
            allowed_origins=["http://localhost:5173"],
        )
    raise ValueError(f"ADAPTERS must be azure or fake, not {adapters!r}")
