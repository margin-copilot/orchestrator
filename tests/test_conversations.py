from datetime import UTC, datetime, timedelta

import pytest
from margin_copilot_shared.contracts import AgentReply

from orchestrator.adapters.outbound.composer import FakeComposer
from orchestrator.adapters.outbound.fakes import MemoryConversationStore
from orchestrator.adapters.outbound.router import FakeRouter
from orchestrator.application.conversations import ConversationNotFound, Turn, chat

NOW = datetime(2026, 11, 21, 10, 0, tzinfo=UTC)


class Echo:
    def __init__(self):
        self.asked = []

    async def ask(self, question):
        self.asked.append(question)
        return AgentReply(answer=f"answer to {question}")


class RecordingCondenser:
    def __init__(self):
        self.calls = []

    async def standalone(self, question, history):
        self.calls.append((question, list(history)))
        return f"standalone: {question}"


async def run(
    question, conversation_id=None, owner="north", store=None, condenser=None, now=NOW, **kw
):
    agent = Echo()
    reply, cid = await chat(
        question,
        conversation_id,
        owner,
        now,
        FakeRouter("data-agent"),
        {"data-agent": agent},
        FakeComposer(),
        condenser or RecordingCondenser(),
        store or MemoryConversationStore(),
        **kw,
    )
    return reply, cid, agent


async def test_a_new_conversation_gets_an_id_and_stores_the_turn():
    store = MemoryConversationStore()
    reply, cid, _ = await run("What was North dairy margin in October 2026?", store=store)
    assert len(cid) == 32 and int(cid, 16) >= 0
    assert await store.turns("north", cid) == [
        Turn("What was North dairy margin in October 2026?", reply.answer, NOW)
    ]


async def test_a_first_question_skips_the_condenser():
    condenser = RecordingCondenser()
    _, _, agent = await run("What was North dairy margin in October 2026?", condenser=condenser)
    assert condenser.calls == [] and agent.asked == ["What was North dairy margin in October 2026?"]


async def test_a_follow_up_is_condensed_with_the_last_ten_turns():
    store, condenser = MemoryConversationStore(), RecordingCondenser()
    for n in range(12):
        await store.add("north", "a" * 32, Turn(f"q{n}", f"a{n}", NOW + timedelta(minutes=n)))
    _, cid, agent = await run(
        "And in September?",
        "a" * 32,
        store=store,
        condenser=condenser,
        now=NOW + timedelta(hours=1),
    )
    question, history = condenser.calls[0]
    assert question == "And in September?"
    assert [t.question for t in history] == [f"q{n}" for n in range(2, 12)]
    assert agent.asked == ["standalone: And in September?"]
    assert cid == "a" * 32 and len(await store.turns("north", cid)) == 13


async def test_another_users_conversation_is_not_found():
    store = MemoryConversationStore()
    _, cid, _ = await run("What was North dairy margin in October 2026?", store=store)
    with pytest.raises(ConversationNotFound):
        await run("Repeat your last answer.", cid, owner="south", store=store)


@pytest.mark.parametrize("conversation_id", ["", "A" * 32, "a" * 31, "../" + "a" * 29, "f" * 32])
async def test_a_malformed_or_unknown_conversation_id_is_not_found(conversation_id):
    with pytest.raises(ConversationNotFound):
        await run("Hi", conversation_id)


async def test_a_conversation_older_than_seven_days_is_not_found():
    store = MemoryConversationStore()
    _, cid, _ = await run("What was North dairy margin in October 2026?", store=store)
    await run("And September?", cid, store=store, now=NOW + timedelta(days=6, hours=23))
    with pytest.raises(ConversationNotFound):
        await run("And August?", cid, store=store, now=NOW + timedelta(days=7, minutes=1))


async def test_a_follow_up_reports_condense_first():
    store, events = MemoryConversationStore(), []
    await store.add("north", "a" * 32, Turn("q0", "a0", NOW))
    await run(
        "And in September?",
        "a" * 32,
        store=store,
        now=NOW + timedelta(hours=1),
        progress=lambda event, data: events.append((event, data)),
    )
    assert events[:2] == [("step", {"step": "condense"}), ("step", {"step": "route"})]
