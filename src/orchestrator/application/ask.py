"""The orchestrator's turn (D-040): the router plans, code calls the chosen agents. One agent's
reply passes through unchanged; several are composed into one answer whose numbers code checks
against the replies (D-011). Approval requests and out-of-scope questions get fixed answers."""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from margin_copilot_shared.contracts import AgentReply, Refusal

from orchestrator.domain.grounding import ungrounded_numbers
from orchestrator.domain.replies import combined_citations, combined_refusal, stitched

AGENTS = ("data-agent", "policy-agent", "action-agent")
APPROVALS = "approvals"
APPROVALS_ANSWER = (
    "I can't approve, reject or apply proposals. A person with the right role does that on the "
    "approval screen, where proposals-api checks the role and region in their sign-in."
)
OUT_OF_SCOPE_ANSWER = (
    "I can only help with Riad Market's pricing, margins, promotions, products and policies."
)


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


async def ask(
    question: str, router: Router, agents: Mapping[str, Agent], composer: Composer
) -> ChatReply:
    plan = await router.route(question)
    if plan.kind == APPROVALS:
        return ChatReply(APPROVALS_ANSWER, None, (), ())
    if plan.kind == "out_of_scope":
        return ChatReply(OUT_OF_SCOPE_ANSWER, "out_of_scope", (), ())
    if unknown := [name for name in plan.agents if name not in agents]:
        raise AgentUnavailable("router", f"chose {unknown[0]!r}")
    replies = await asyncio.gather(*(agents[name].ask(question) for name in plan.agents))
    named = list(zip(plan.agents, replies, strict=True))
    citations = combined_citations(replies)
    if len(named) == 1:
        reply = replies[0]
        return ChatReply(reply.answer, reply.refusal, plan.agents, citations)
    if refusal := combined_refusal(replies):
        return ChatReply(stitched(named), refusal, plan.agents, citations)
    sources = [question, *(f"{r.answer} {' '.join(r.citations)}" for r in replies)]
    answer = await composer.compose(question, named)
    if ungrounded := ungrounded_numbers(answer, sources):
        answer = await composer.compose(question, named, ungrounded)
        if ungrounded_numbers(answer, sources):
            answer = stitched(named)  # never an answer with a number no agent gave
    return ChatReply(answer, None, plan.agents, citations)
