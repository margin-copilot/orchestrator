"""Milestone 6 orchestrator: a router picks one agent per question, and that agent's reply passes
through unchanged (D-035). Approval requests get a fixed answer from code: no agent can approve.
Composing answers from several agents arrives in milestone 7."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from margin_copilot_shared.contracts import AgentReply, Refusal

AGENTS = ("data-agent", "policy-agent", "action-agent")
APPROVALS = "approvals"
APPROVALS_ANSWER = (
    "I can't approve, reject or apply proposals. A person with the right role does that on the "
    "approval screen, where proposals-api checks the role and region in their sign-in."
)


class Agent(Protocol):
    async def ask(self, question: str) -> AgentReply: ...


class Router(Protocol):
    async def route(self, question: str) -> str: ...


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


async def ask(question: str, router: Router, agents: Mapping[str, Agent]) -> ChatReply:
    name = await router.route(question)
    if name == APPROVALS:
        return ChatReply(APPROVALS_ANSWER, None, (), ())
    if name not in agents:
        raise AgentUnavailable("router", f"chose {name!r}")
    reply = await agents[name].ask(question)
    return ChatReply(reply.answer, reply.refusal, (name,), tuple(reply.citations))
