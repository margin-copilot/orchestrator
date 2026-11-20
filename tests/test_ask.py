import pytest
from margin_copilot_shared.contracts import AgentReply

from orchestrator.application.ask import AgentUnavailable, ChatReply, ask


class StubRouter:
    def __init__(self, choice):
        self.choice = choice

    async def route(self, question):
        return self.choice


class StubAgent:
    def __init__(self, reply):
        self.reply, self.asked = reply, []

    async def ask(self, question):
        self.asked.append(question)
        return self.reply


async def test_the_router_picks_the_agent_and_its_reply_passes_through():
    policy = StubAgent(AgentReply(answer="Yes, within 4%.", citations=["PP-04 §3.2"]))
    data = StubAgent(AgentReply(answer="21.3%"))
    agents = {"data-agent": data, "policy-agent": policy}
    reply = await ask("Can I raise UHT milk by 3.6%?", StubRouter("policy-agent"), agents)
    assert reply == ChatReply("Yes, within 4%.", None, ("policy-agent",), ("PP-04 §3.2",))
    assert data.asked == []


async def test_an_unknown_choice_is_a_router_failure():
    agents = {"data-agent": StubAgent(AgentReply(answer="x"))}
    with pytest.raises(AgentUnavailable) as failed:
        await ask("q", StubRouter("action-agent"), agents)
    assert failed.value.name == "router"
