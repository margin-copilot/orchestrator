import json

import httpx
from agent_framework import Message
from agent_framework.observability import enable_instrumentation
from margin_copilot_shared.testing.telemetry import recorded_spans, texts_in
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware

from orchestrator.adapters.outbound.composer import FoundryComposer
from orchestrator.adapters.outbound.router import FoundryRouter
from orchestrator.application.ask import Plan, ask
from orchestrator.main import create_app
from tests.fakes import ScriptedChatClient
from tests.test_ask import DATA, POLICY, QUESTION, ScriptedComposer, StubAgent, StubRouter

COMPOSED = "Not allowed: it rose 2.9% on 2026-02-20, in Ramadan (PP-07 §2)."


def says(**fields):
    return Message("assistant", [json.dumps(fields)])


async def test_a_composed_turn_records_each_step():
    agents = {"data-agent": StubAgent(DATA), "policy-agent": StubAgent(POLICY)}
    plan = Plan("agents", ("data-agent", "policy-agent"))
    with recorded_spans() as spans:
        await ask(QUESTION, StubRouter(plan), agents, ScriptedComposer(COMPOSED))
    steps = sorted(spans, key=lambda s: s.start_time)
    assert [s.name for s in steps] == [
        "orchestrator.route",
        "orchestrator.agent",
        "orchestrator.agent",
        "orchestrator.compose",
    ]
    assert steps[0].attributes["margin.plan"] == "data-agent+policy-agent"
    assert {s.attributes["margin.agent"] for s in steps[1:3]} == {"data-agent", "policy-agent"}
    assert steps[3].attributes["margin.attempt"] == 1


async def test_no_question_or_answer_text_reaches_a_span():
    enable_instrumentation(enable_sensitive_data=False)
    router = FoundryRouter(ScriptedChatClient([says(route="data-and-policy")]))
    composer = FoundryComposer(ScriptedChatClient([says(answer=COMPOSED)]))
    agents = {"data-agent": StubAgent(DATA), "policy-agent": StubAgent(POLICY)}
    with recorded_spans() as spans:
        reply = await ask(QUESTION, router, agents, composer)
    assert COMPOSED in reply.answer
    texts = texts_in(spans)
    for content in (QUESTION, DATA.answer, POLICY.answer, COMPOSED, "The user sees the data agent"):
        assert content not in texts
    assert any(s.name.startswith(("chat", "invoke_agent")) for s in spans)


async def test_the_app_is_wrapped_and_untraced_without_a_connection_string(monkeypatch):
    monkeypatch.setenv("ADAPTERS", "fake")
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    app = create_app()
    assert isinstance(app, OpenTelemetryMiddleware)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://o") as c:
        assert (await c.post("/chat", json={"question": "hi"})).status_code == 401
