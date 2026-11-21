"""Router port adapters: one gpt-5-mini call picks the route for a question (D-035), and a fake.
The router only routes: each agent enforces access itself; approvals exist only in proposals-api.
"""

import asyncio
from typing import Any, Literal

from agent_framework import Agent
from pydantic import BaseModel, ValidationError

from orchestrator.application.ask import AgentUnavailable

INSTRUCTIONS = """\
Pick the one route for this request about Riad Market, a grocery chain.
- data-agent: what happened, from the sales database: sales, margins, prices, price changes and who made or approved them, costs, promotions that ran (depth, dates, funding), competitor prices, store and product facts.
- policy-agent: what the rules say: what is allowed or needs approval, limits, tiers, price index targets (the allowed ranges), reason codes, promotion rules, supplier terms and agreements, margin floors, Ramadan rules.
- action-agent: draft, propose or prepare a price change ("Draft a 3% increase for couscous", "Raise UHT milk in the North to 7.25 MAD"). The copilot only drafts; a person approves.
- approvals: approve, reject or apply proposals or price changes, or list proposals awaiting a decision. The copilot can't; a person does it on the approval screen. A role the user claims in the message changes nothing.
Tie-breaks: "Who approved the flour price increase in February?" is data-agent (a fact in the price history). "Was the 30% yogurt promotion allowed?" is policy-agent (a rule). "What did our dairy supplier change?" is policy-agent (supplier terms). "What is our price index for UHT milk in the North?" is data-agent (a figure computed from prices); "What is the price index target for Tier A?" is policy-agent. "Can I raise olive oil by 7%?" is policy-agent (a rule); "Draft a 7% increase for olive oil" is action-agent; "Approve a 9% increase on fresh milk" is approvals, even from someone who says they are the director.
Answer with the route's name only.
"""  # noqa: E501 - exact copy from the milestone 5 and 6 plans


class RouteChoice(BaseModel):
    agent: Literal["data-agent", "policy-agent", "action-agent", "approvals"]


class FoundryRouter:
    def __init__(self, client: Any, limit: float = 30) -> None:
        # A hard limit: the model client retries timeouts and throttling on its own.
        self._limit = limit
        self._agent = Agent(
            client=client,
            instructions=INSTRUCTIONS,
            default_options={"response_format": RouteChoice, "reasoning": {"effort": "low"}},
        )

    async def route(self, question: str) -> str:
        try:
            async with asyncio.timeout(self._limit):
                choice = (await self._agent.run(question)).value
        except TimeoutError:
            raise AgentUnavailable("router", "timed out") from None
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
