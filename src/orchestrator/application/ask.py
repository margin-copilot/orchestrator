"""The orchestrator's turn (D-040): the router plans, code calls the chosen agents. One agent's
reply passes through unchanged. With two, the data agent's reply leads unchanged and a composer
adds what the policies say, its numbers checked against the replies (D-011). Approval requests
and out-of-scope questions get fixed answers."""

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from margin_copilot_shared.contracts import AgentReply, Refusal
from margin_copilot_shared.telemetry import span

from orchestrator.domain.grounding import ungrounded_numbers, unknown_citations
from orchestrator.domain.replies import (
    after_echo,
    combined_citations,
    combined_refusal,
    stitched,
)

AGENTS = ("data-agent", "policy-agent", "action-agent")
APPROVALS = "approvals"
APPROVALS_ANSWER = (
    "I can't approve, reject or apply proposals. A person with the right role does that on the "
    "approval screen, where proposals-api checks the role and region in their sign-in."
)
OUT_OF_SCOPE_ANSWER = (
    "I can only help with Riad Market's pricing, margins, promotions, products and policies."
)


# What the turn reports as it goes (D-045): ("step", {"step": <fixed name>}) as each stage starts,
# and ("lead", {"answer": <the data agent's reply>}) when it arrives in a turn with two agents.
Progress = Callable[[str, dict[str, str]], None]


def quiet(event: str, data: dict[str, str]) -> None:
    pass


@dataclass(frozen=True)
class Plan:
    kind: Literal["agents", "approvals", "out_of_scope"]
    agents: tuple[str, ...] = ()


class Agent(Protocol):
    async def ask(self, question: str) -> AgentReply: ...


class Router(Protocol):
    async def route(self, question: str) -> Plan: ...


class Composer(Protocol):
    async def compose(
        self,
        question: str,
        named: Sequence[tuple[str, AgentReply]],
        ungrounded: Sequence[str] = (),
    ) -> str: ...


class AgentUnavailable(Exception):
    def __init__(self, name: str, detail: str) -> None:
        super().__init__(f"{name}: {detail}")
        self.name = name


@dataclass(frozen=True)
class ChatReply:
    """The /chat response (D-029). `agents` lists the agents called in this turn."""

    answer: str
    refusal: Refusal | None
    agents: tuple[str, ...]
    citations: tuple[str, ...]


# One choice per question. No route pairs the action agent with another: drafts only when asked
# for alone, never as a side effect of a question.
ROUTES = {
    "data-agent": Plan("agents", ("data-agent",)),
    "policy-agent": Plan("agents", ("policy-agent",)),
    "data-and-policy": Plan("agents", ("data-agent", "policy-agent")),
    "action-agent": Plan("agents", ("action-agent",)),
    "approvals": Plan("approvals"),
    "out_of_scope": Plan("out_of_scope"),
}


def to_plan(route: str) -> Plan:
    if route not in ROUTES:
        raise AgentUnavailable("router", f"chose {route!r}")
    return ROUTES[route]


async def _asked(
    name: str, agent: Agent, question: str, arrived: Callable[[AgentReply], None]
) -> AgentReply:
    with span("orchestrator.agent", **{"margin.agent": name}) as asked:
        reply = await agent.ask(question)
        if reply.refusal:
            asked.set_attribute("margin.refusal", reply.refusal)
        arrived(reply)
        return reply


def _ungrounded(answer: str, sources: list[str]) -> list[str]:
    return ungrounded_numbers(answer, sources) + unknown_citations(answer, sources)


async def ask(
    question: str,
    router: Router,
    agents: Mapping[str, Agent],
    composer: Composer,
    user_question: str | None = None,
    progress: Progress = quiet,
) -> ChatReply:
    """`question` may be a condensed follow-up; numbers are checked against `user_question`, what
    the user typed, so a number the condenser wrote never counts as given."""
    progress("step", {"step": "route"})
    with span("orchestrator.route") as routed:
        plan = await router.route(question)
        routed.set_attribute("margin.plan", "+".join(plan.agents) or plan.kind)
    if plan.kind == APPROVALS:
        return ChatReply(APPROVALS_ANSWER, None, (), ())
    if plan.kind == "out_of_scope":
        return ChatReply(OUT_OF_SCOPE_ANSWER, "out_of_scope", (), ())
    if unknown := [name for name in plan.agents if name not in agents]:
        raise AgentUnavailable("router", f"chose {unknown[0]!r}")
    for name in plan.agents:
        progress("step", {"step": name})

    def arrived(name: str) -> Callable[[AgentReply], None]:
        if name == "data-agent" and len(plan.agents) > 1:  # it leads the answer, unchanged
            return lambda reply: progress("lead", {"answer": reply.answer})
        return lambda reply: None

    replies = await asyncio.gather(
        *(_asked(name, agents[name], question, arrived(name)) for name in plan.agents)
    )
    named = list(zip(plan.agents, replies, strict=True))
    citations = combined_citations(replies)
    if len(named) == 1:
        reply = replies[0]
        return ChatReply(reply.answer, reply.refusal, plan.agents, citations)
    # The data agent's no_access means the question is about regions the user can't see; another
    # agent's reply beside it would read as an answer anyway.
    if (data := dict(named).get("data-agent")) and data.refusal == "no_access":
        return ChatReply(data.answer, data.refusal, plan.agents, tuple(data.citations))
    if refusal := combined_refusal(replies):
        return ChatReply(stitched(named), refusal, plan.agents, citations)
    sources = [user_question or question, *(f"{r.answer} {' '.join(r.citations)}" for r in replies)]
    progress("step", {"step": "compose"})
    try:
        with span("orchestrator.compose", **{"margin.attempt": 1}):
            part = await composer.compose(question, named)
        if ungrounded := _ungrounded(part, sources):
            with span("orchestrator.compose", **{"margin.attempt": 2}) as retried:
                part = await composer.compose(question, named, ungrounded)
                if _ungrounded(part, sources):  # never an answer with a number no agent gave
                    retried.set_attribute("margin.fallback", "stitched")
                    return ChatReply(stitched(named), None, plan.agents, citations)
    except AgentUnavailable:  # two good replies beat a 502
        return ChatReply(stitched(named), None, plan.agents, citations)
    lead = dict(named).get("data-agent")
    if lead and not (part := after_echo(part, lead.answer)):
        return ChatReply(stitched(named), None, plan.agents, citations)
    answer = f"{lead.answer}\n\n{part}" if lead else part
    return ChatReply(answer, None, plan.agents, citations)
