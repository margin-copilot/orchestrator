import asyncio
import json

import pytest
from agent_framework import Message

from orchestrator.adapters.outbound.router import FoundryRouter
from orchestrator.application.ask import AgentUnavailable, Plan
from tests.fakes import ScriptedChatClient


def chooses(*agents, route="agents"):
    return Message("assistant", [json.dumps({"route": route, "agents": list(agents)})])


async def test_the_router_returns_a_plan():
    model = ScriptedChatClient([chooses("policy-agent", "data-agent")])
    plan = await FoundryRouter(model).route("Was the North flour increase in February allowed?")
    assert plan == Plan("agents", ("data-agent", "policy-agent"))


async def test_router_instructions_carry_the_tie_breaks():
    model = ScriptedChatClient([chooses("data-agent")])
    await FoundryRouter(model).route("Who approved the North flour price increase?")
    instructions = model.calls[0][1]["instructions"]
    assert "Who approved the flour price increase" in instructions
    assert "Was the 30% yogurt promotion allowed" in instructions
    assert "What is our price index for UHT milk in the North?" in instructions


async def test_router_reasoning_effort_is_low():
    model = ScriptedChatClient([chooses("data-agent")])
    await FoundryRouter(model).route("Dairy margin in October?")
    assert model.calls[0][1]["reasoning"] == {"effort": "low"}


async def test_no_structured_choice_is_a_router_failure():
    model = ScriptedChatClient([Message("assistant", ["the data agent, I think"])])
    with pytest.raises(AgentUnavailable) as failed:
        await FoundryRouter(model).route("q")
    assert failed.value.name == "router"


async def test_a_slow_router_fails_within_its_limit():
    class Slow(ScriptedChatClient):
        async def _inner_get_response(self, **kwargs):
            await asyncio.sleep(5)
            return await super()._inner_get_response(**kwargs)

    with pytest.raises(AgentUnavailable) as failed:
        await FoundryRouter(Slow([chooses("data-agent")]), limit=0.05).route("q")
    assert failed.value.name == "router"


async def test_router_instructions_carry_the_action_tie_breaks():
    model = ScriptedChatClient([chooses("action-agent")])
    await FoundryRouter(model).route("Draft a 7% increase for olive oil.")
    instructions = model.calls[0][1]["instructions"]
    assert '"Can I raise olive oil by 7%?" is policy-agent (a rule)' in instructions
    assert '"Draft a 7% increase for olive oil" is action-agent' in instructions
    assert '"Approve a 9% increase on fresh milk" is approvals' in instructions


async def test_the_router_can_choose_approvals():
    model = ScriptedChatClient([chooses(route="approvals")])
    assert await FoundryRouter(model).route("Approve all my pending proposals.") == Plan(
        "approvals"
    )


async def test_a_router_model_failure_is_unavailable():
    class Failing(ScriptedChatClient):
        async def _inner_get_response(self, **kwargs):
            raise ConnectionError("model endpoint unreachable")

    with pytest.raises(AgentUnavailable) as failed:
        await FoundryRouter(Failing([])).route("q")
    assert failed.value.name == "router"
    assert "unreachable" not in str(failed.value)


async def test_router_instructions_carry_the_scope_and_combination_rules():
    model = ScriptedChatClient([chooses(route="out_of_scope")])
    assert await FoundryRouter(model).route("Weather?") == Plan("out_of_scope")
    instructions = " ".join(model.calls[0][1]["instructions"].split())
    assert 'A general pricing concept ("What is a KVI?") is policy-agent' in instructions
    assert (
        '"Was the North flour increase in February 2026 allowed?": '
        "its date and size are in the data" in instructions
    )
    assert '"Was the 30% yogurt promotion in October allowed?" is policy-agent only' in instructions
