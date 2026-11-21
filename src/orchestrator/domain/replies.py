"""What code, not a model, decides about several agents' replies: citations, the refusal label
(D-029: set only when nothing could be answered), and the plain fallback answer."""

from collections.abc import Sequence

from margin_copilot_shared.contracts import AgentReply, Refusal

MAX_CITATIONS = 12  # AgentReply's cap
SOURCES = {"data-agent": "From the sales data", "policy-agent": "From the policies"}


def combined_citations(replies: Sequence[AgentReply]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for reply in replies:
        seen.update(dict.fromkeys(reply.citations))
    return tuple(seen)[:MAX_CITATIONS]


def combined_refusal(replies: Sequence[AgentReply]) -> Refusal | None:
    labels = [reply.refusal for reply in replies]
    if None in labels:
        return None
    return "no_access" if "no_access" in labels else "no_data"


def stitched(named: Sequence[tuple[str, AgentReply]]) -> str:
    return "\n\n".join(f"{SOURCES.get(name, name)}: {reply.answer}" for name, reply in named)
