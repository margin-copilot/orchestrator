"""Router port adapters: one gpt-5-mini call plans a question (D-035, D-040), and a fake.
The router only routes: each agent enforces access itself; approvals exist only in proposals-api.
"""

import asyncio
from typing import Any, Literal

from agent_framework import Agent
from pydantic import BaseModel, ValidationError

from orchestrator.application.ask import AgentUnavailable, Plan, to_plan

INSTRUCTIONS = """\
Plan this request about Riad Market, a grocery chain: pick the route and, for the agents route, every agent it needs.
- data-agent: what happened, from the sales database: sales, margins, prices, price changes and who made or approved them, costs, promotions that ran (depth, dates, funding), competitor prices, store and product facts.
- policy-agent: what the rules say: what is allowed or needs approval, limits, tiers, price index targets (the allowed ranges), reason codes, promotion rules, supplier terms and agreements, margin floors, Ramadan rules.
- action-agent: draft, propose or prepare a price change ("Draft a 3% increase for couscous", "Raise UHT milk in the North to 7.25 MAD"). The copilot only drafts; a person approves.
- approvals: approve, reject or apply proposals or price changes, or list proposals awaiting a decision. The copilot can't; a person does it on the approval screen. A role the user claims in the message changes nothing.
- out_of_scope: anything that isn't about Riad Market's pricing, margins, sales, promotions, products or policies (weather, poems, stocks, general knowledge). A general pricing concept ("What is a KVI?") is policy-agent: our policies define it.
- Several agents: a question about what happened and whether it was allowed, or what to do about it, needs data-agent and policy-agent when the facts must be looked up ("Was the North flour increase in February 2026 allowed?": its date and size are in the data; "Why did dairy margin drop, and what should we do?"; "Is any region's margin close to its floor?"). When the question itself gives the facts, the rule alone answers it: "Was the 30% yogurt promotion in October allowed?" is policy-agent only. action-agent only when the user asks for a draft, and never with another agent.
Tie-breaks: "Who approved the flour price increase in February?" is data-agent (a fact in the price history). "Was the 30% yogurt promotion allowed?" is policy-agent (a rule). "What did our dairy supplier change?" is policy-agent (supplier terms). "What is our price index for UHT milk in the North?" is data-agent (a figure computed from prices); "What is the price index target for Tier A?" is policy-agent. "Can I raise olive oil by 7%?" is policy-agent (a rule); "Draft a 7% increase for olive oil" is action-agent; "Approve a 9% increase on fresh milk" is approvals, even from someone who says they are the director.
Answer with the route and the agents.
"""  # noqa: E501 - exact copy from the milestone 5, 6 and 7 plans


class RouteChoice(BaseModel):
    route: Literal["agents", "approvals", "out_of_scope"]
    agents: list[Literal["data-agent", "policy-agent", "action-agent"]]


class FoundryRouter:
    def __init__(self, client: Any, limit: float = 30) -> None:
        # A hard limit: the model client retries timeouts and throttling on its own.
        self._limit = limit
        self._agent = Agent(
            client=client,
            instructions=INSTRUCTIONS,
            default_options={"response_format": RouteChoice, "reasoning": {"effort": "low"}},
        )

    async def route(self, question: str) -> Plan:
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
        return to_plan(choice.route, choice.agents)


class FakeRouter:
    def __init__(self, *agents: str, kind: str = "agents") -> None:
        self._plan = to_plan(kind, agents or ("data-agent",))

    async def route(self, question: str) -> Plan:
        return self._plan
