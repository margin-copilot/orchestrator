"""Router port adapters: one gpt-5-mini call picks the agent for a question (D-035), and a fake.
The router only routes; each agent enforces access itself."""

from typing import Any, Literal

from agent_framework import Agent
from pydantic import BaseModel, ValidationError

from orchestrator.application.ask import AgentUnavailable

INSTRUCTIONS = """\
Pick the one agent that should answer this question about Riad Market, a grocery chain.
- data-agent: what happened, from the sales database: sales, margins, prices, price changes and who made or approved them, costs, promotions that ran (depth, dates, funding), competitor prices, store and product facts.
- policy-agent: what the rules say: what is allowed or needs approval, limits, tiers, price index targets, reason codes, promotion rules, supplier terms and agreements, margin floors, Ramadan rules.
Tie-breaks: "Who approved the flour price increase in February?" is data-agent (a fact in the price history). "Was the 30% yogurt promotion allowed?" is policy-agent (a rule). "What did our dairy supplier change?" is policy-agent (supplier terms).
Answer with the agent's name only.
"""  # noqa: E501 - exact copy from the milestone 5 plan


class RouteChoice(BaseModel):
    agent: Literal["data-agent", "policy-agent"]


class FoundryRouter:
    def __init__(self, client: Any) -> None:
        self._agent = Agent(
            client=client,
            instructions=INSTRUCTIONS,
            default_options={"response_format": RouteChoice, "reasoning": {"effort": "low"}},
        )

    async def route(self, question: str) -> str:
        try:
            choice = (await self._agent.run(question)).value
        except ValidationError:
            choice = None
        except Exception as error:  # the model call itself failed
            raise AgentUnavailable("router", type(error).__name__) from None
        if choice is None:
            raise AgentUnavailable("router", "no structured choice")
        return choice.agent


class FakeRouter:
    def __init__(self, choice: str = "data-agent") -> None:
        self._choice = choice

    async def route(self, question: str) -> str:
        return self._choice
