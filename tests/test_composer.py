import asyncio
import json

import pytest
from agent_framework import Message
from margin_copilot_shared.contracts import AgentReply

from orchestrator.adapters.outbound.composer import FoundryComposer
from orchestrator.application.ask import AgentUnavailable
from tests.fakes import ScriptedChatClient

NAMED = [
    ("data-agent", AgentReply(answer="North flour rose 2.9% on 2026-02-20.")),
    ("policy-agent", AgentReply(answer="No Tier A increases in Ramadan.", citations=["PP-07 §2"])),
]


def says(answer):
    return Message("assistant", [json.dumps({"answer": answer})])


async def test_the_composer_sees_each_reply_and_no_tools():
    model = ScriptedChatClient([says("Not allowed (PP-07 §2).")])
    answer = await FoundryComposer(model).compose("Was it allowed?", NAMED)
    assert answer == "Not allowed (PP-07 §2)."
    messages, options = model.calls[0]
    sent = " ".join(str(m.text) for m in messages)
    assert "North flour rose 2.9% on 2026-02-20." in sent
    assert "No Tier A increases in Ramadan." in sent and "PP-07 §2" in sent
    assert not options.get("tools")
    assert options["reasoning"] == {"effort": "medium"}
    instructions = " ".join(options["instructions"].split())
    assert (
        "never compute new ones (no differences, sums, percentages or suggested prices)"
        in instructions
    )


async def test_a_retry_names_the_ungrounded_numbers():
    model = ScriptedChatClient([says("Not allowed.")])
    await FoundryComposer(model).compose("Was it allowed?", NAMED, ungrounded=["7.25"])
    sent = " ".join(str(m.text) for m in model.calls[0][0])
    assert (
        "These numbers or clause IDs aren't in any reply: 7.25. "
        "Remove them or quote the replies' figures." in sent
    )


async def test_a_slow_composer_fails_within_its_limit():
    class Slow(ScriptedChatClient):
        async def _inner_get_response(self, **kwargs):
            await asyncio.sleep(5)
            return await super()._inner_get_response(**kwargs)

    with pytest.raises(AgentUnavailable) as failed:
        await FoundryComposer(Slow([says("x")]), limit=0.05).compose("q", NAMED)
    assert failed.value.name == "composer"


async def test_an_empty_answer_is_a_composer_failure():
    model = ScriptedChatClient([says("")])
    with pytest.raises(AgentUnavailable) as failed:
        await FoundryComposer(model).compose("q", NAMED)
    assert failed.value.name == "composer"
