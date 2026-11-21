import asyncio
import json

import pytest
from agent_framework import Message

from orchestrator.adapters.outbound.router import FoundryRouter
from orchestrator.application.ask import AgentUnavailable
from tests.fakes import ScriptedChatClient


def chooses(agent):
    return Message("assistant", [json.dumps({"agent": agent})])


async def test_the_router_returns_the_models_choice():
    model = ScriptedChatClient([chooses("policy-agent")])
    assert await FoundryRouter(model).route("Is olive oil Tier A or Tier B?") == "policy-agent"


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
    model = ScriptedChatClient([chooses("approvals")])
    assert await FoundryRouter(model).route("Approve all my pending proposals.") == "approvals"


async def test_a_router_model_failure_is_unavailable():
    class Failing(ScriptedChatClient):
        async def _inner_get_response(self, **kwargs):
            raise ConnectionError("model endpoint unreachable")

    with pytest.raises(AgentUnavailable) as failed:
        await FoundryRouter(Failing([])).route("q")
    assert failed.value.name == "router"
    assert "unreachable" not in str(failed.value)
