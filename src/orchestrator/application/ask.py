"""Milestone 1 orchestrator: pass the question to the data agent and return its reply."""

from typing import Protocol


class DataAgent(Protocol):
    async def ask(self, question: str) -> str: ...


class DataAgentUnavailable(Exception):
    pass


async def ask(question: str, data_agent: DataAgent) -> str:
    return await data_agent.ask(question)
