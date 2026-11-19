"""Milestone 4 orchestrator: every question goes to the data agent; routing comes in milestone 7."""

from dataclasses import dataclass
from typing import Protocol

from margin_copilot_shared.contracts import AgentReply, Refusal


class DataAgent(Protocol):
    async def ask(self, question: str) -> AgentReply: ...


class DataAgentUnavailable(Exception):
    pass


@dataclass(frozen=True)
class ChatReply:
    """The /chat response (D-029). `agents` lists the agents called in this turn."""

    answer: str
    refusal: Refusal | None
    agents: tuple[str, ...]


async def ask(question: str, data_agent: DataAgent) -> ChatReply:
    reply = await data_agent.ask(question)
    return ChatReply(reply.answer, reply.refusal, ("data-agent",))
