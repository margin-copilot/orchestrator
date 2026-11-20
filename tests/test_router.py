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
