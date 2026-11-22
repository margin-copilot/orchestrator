import pytest
from margin_copilot_shared.contracts import AgentReply

from orchestrator.application.ask import (
    OUT_OF_SCOPE_ANSWER,
    ROUTES,
    AgentUnavailable,
    ChatReply,
    Plan,
    ask,
    to_plan,
)


class StubRouter:
    def __init__(self, plan):
        self.plan = plan

    async def route(self, question):
        return self.plan


class StubAgent:
    def __init__(self, reply):
        self.reply, self.asked = reply, []

    async def ask(self, question):
        self.asked.append(question)
        return self.reply


class RefusingAgent:
    async def ask(self, question):
        raise AssertionError("this agent must not be called")


class ScriptedComposer:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    async def compose(self, question, named, ungrounded=()):
        self.calls.append((question, [name for name, _ in named], list(ungrounded)))
        return self.answers.pop(0)


class NoComposer:
    async def compose(self, question, named, ungrounded=()):
        raise AssertionError("one agent's reply passes through: no composer")


DATA = AgentReply(answer="North flour rose from 35.00 to 36.00 MAD (+2.9%) on 2026-02-20.")
POLICY = AgentReply(
    answer="Tier A prices can't rise during Ramadan (18 Feb to 19 Mar 2026).",
    citations=["PP-07 §2", "PP-02 §1"],
)
QUESTION = "Was the North flour increase in February 2026 allowed?"


async def test_one_agent_passes_through_unchanged():
    policy = StubAgent(AgentReply(answer="Yes, within 4%.", citations=["PP-04 §3.2"]))
    agents = {"data-agent": RefusingAgent(), "policy-agent": policy}
    plan = Plan("agents", ("policy-agent",))
    reply = await ask("Can I raise UHT milk by 3.6%?", StubRouter(plan), agents, NoComposer())
    assert reply == ChatReply("Yes, within 4%.", None, ("policy-agent",), ("PP-04 §3.2",))


async def test_the_data_reply_leads_unchanged_and_the_composer_adds_the_policy_part():
    composer = ScriptedComposer("Not allowed: it rose 2.9% on 2026-02-20, in Ramadan (PP-07 §2).")
    agents = {"data-agent": StubAgent(DATA), "policy-agent": StubAgent(POLICY)}
    plan = Plan("agents", ("data-agent", "policy-agent"))
    reply = await ask(QUESTION, StubRouter(plan), agents, composer)
    assert reply.answer == (
        f"{DATA.answer}\n\nNot allowed: it rose 2.9% on 2026-02-20, in Ramadan (PP-07 §2)."
    )
    assert (reply.refusal, reply.agents) == (None, ("data-agent", "policy-agent"))
    assert reply.citations == ("PP-07 §2", "PP-02 §1")
    assert composer.calls == [(QUESTION, ["data-agent", "policy-agent"], [])]


async def test_an_ungrounded_answer_is_retried_once_then_stitched():
    invented = "Not allowed; raise it back to 7.25 MAD instead."
    composer = ScriptedComposer(invented, invented)
    agents = {"data-agent": StubAgent(DATA), "policy-agent": StubAgent(POLICY)}
    plan = Plan("agents", ("data-agent", "policy-agent"))
    reply = await ask(QUESTION, StubRouter(plan), agents, composer)
    assert reply.answer.startswith("From the sales data: North flour rose")
    assert [call[2] for call in composer.calls] == [[], ["7.25"]]


async def test_all_agents_refusing_is_stitched_with_the_refusal():
    refused = AgentReply(answer="No data recorded.", refusal="no_data")
    agents = {"data-agent": StubAgent(refused), "policy-agent": StubAgent(refused)}
    plan = Plan("agents", ("data-agent", "policy-agent"))
    reply = await ask(QUESTION, StubRouter(plan), agents, NoComposer())
    assert reply.refusal == "no_data" and reply.answer.startswith("From the sales data:")


@pytest.mark.parametrize(
    "policy",
    [POLICY, AgentReply(answer="No policy covers this; check prices.", refusal="no_data")],
)
async def test_the_data_agent_refusing_access_answers_alone(policy):
    refused = AgentReply(answer="I can't see North data.", refusal="no_access")
    agents = {"data-agent": StubAgent(refused), "policy-agent": StubAgent(policy)}
    plan = Plan("agents", ("data-agent", "policy-agent"))
    reply = await ask(QUESTION, StubRouter(plan), agents, NoComposer())
    assert reply == ChatReply(
        "I can't see North data.", "no_access", ("data-agent", "policy-agent"), ()
    )


async def test_out_of_scope_calls_no_agent():
    agents = {"data-agent": RefusingAgent(), "policy-agent": RefusingAgent()}
    reply = await ask("Weather in Rabat?", StubRouter(Plan("out_of_scope")), agents, NoComposer())
    assert reply == ChatReply(OUT_OF_SCOPE_ANSWER, "out_of_scope", (), ())


async def test_approval_requests_call_no_agent():
    agents = {name: RefusingAgent() for name in ("data-agent", "policy-agent", "action-agent")}
    reply = await ask(
        "Approve all my pending proposals now.", StubRouter(Plan("approvals")), agents, NoComposer()
    )
    assert (reply.agents, reply.refusal, reply.citations) == ((), None, ())
    assert "approval screen" in reply.answer


async def test_the_action_agent_is_routable_alone():
    action = StubAgent(AgentReply(answer="Drafted proposal 1a2b3c4d, pending approval: …"))
    agents = {"data-agent": RefusingAgent(), "action-agent": action}
    plan = Plan("agents", ("action-agent",))
    reply = await ask("Draft a 2% increase for couscous.", StubRouter(plan), agents, NoComposer())
    assert reply.agents == ("action-agent",) and action.asked


def test_the_action_agent_never_runs_with_another_agent():
    for plan in ROUTES.values():
        assert "action-agent" not in plan.agents or plan.agents == ("action-agent",)
    assert to_plan("data-and-policy") == Plan("agents", ("data-agent", "policy-agent"))


def test_an_unknown_route_is_a_router_failure():
    with pytest.raises(AgentUnavailable) as failed:
        to_plan("data-and-action")
    assert failed.value.name == "router"


async def test_an_unknown_agent_is_a_router_failure():
    agents = {"data-agent": StubAgent(AgentReply(answer="x"))}
    with pytest.raises(AgentUnavailable) as failed:
        await ask("q", StubRouter(Plan("agents", ("policy-agent",))), agents, NoComposer())
    assert failed.value.name == "router"


async def test_a_number_the_condenser_added_is_not_grounded():
    composer = ScriptedComposer("It fell from 24.6% to 21.3%.", "It fell from 24.6% to 21.3%.")
    margins = AgentReply(answer="North dairy margin: 21.3% in October 2026.")
    agents = {"data-agent": StubAgent(margins), "policy-agent": StubAgent(POLICY)}
    plan = Plan("agents", ("data-agent", "policy-agent"))
    condensed = "Why did North dairy margin fall from 24.6% to 21.3% in October 2026?"
    reply = await ask(condensed, StubRouter(plan), agents, composer, user_question="And why?")
    assert reply.answer.startswith("From the sales data:")


async def test_a_composer_failure_falls_back_to_the_replies():
    class Broken:
        async def compose(self, question, named, ungrounded=()):
            raise AgentUnavailable("composer", "timed out")

    agents = {"data-agent": StubAgent(DATA), "policy-agent": StubAgent(POLICY)}
    plan = Plan("agents", ("data-agent", "policy-agent"))
    reply = await ask(QUESTION, StubRouter(plan), agents, Broken())
    assert reply.answer.startswith("From the sales data:") and reply.refusal is None


async def test_an_unknown_clause_id_is_retried_then_stitched():
    composer = ScriptedComposer("Not allowed (PP-06 §1).", "Not allowed (PP-06 §1).")
    agents = {"data-agent": StubAgent(DATA), "policy-agent": StubAgent(POLICY)}
    plan = Plan("agents", ("data-agent", "policy-agent"))
    reply = await ask(QUESTION, StubRouter(plan), agents, composer)
    assert reply.answer.startswith("From the sales data:")
    assert composer.calls[1][2] == ["PP-06 §1"]
