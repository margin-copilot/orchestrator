"""Conversations (D-011, D-041): turns are stored under the owner's oid from the validated token,
so another user's conversation is simply not found. A follow-up is rewritten into a standalone
question from the last turns; agents stay stateless. History never comes from the request."""

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from orchestrator.application.ask import Agent, ChatReply, Composer, Router, ask

HISTORY_TURNS = 10
EXPIRES_AFTER = timedelta(days=7)
CONVERSATION_ID = re.compile(r"[0-9a-f]{32}")


@dataclass(frozen=True)
class Turn:
    question: str
    answer: str
    at: datetime


class ConversationStore(Protocol):
    async def turns(self, owner: str, conversation_id: str) -> list[Turn]: ...

    async def add(self, owner: str, conversation_id: str, turn: Turn) -> None: ...


class Condenser(Protocol):
    async def standalone(self, question: str, history: Sequence[Turn]) -> str: ...


class ConversationNotFound(Exception):
    """Unknown, someone else's, malformed or expired: one answer for all, so it reveals nothing."""


async def chat(
    question: str,
    conversation_id: str | None,
    owner: str,
    now: datetime,
    router: Router,
    agents: Mapping[str, Agent],
    composer: Composer,
    condenser: Condenser,
    store: ConversationStore,
) -> tuple[ChatReply, str]:
    history: list[Turn] = []
    if conversation_id is None:
        conversation_id = uuid.uuid4().hex
    else:
        if not CONVERSATION_ID.fullmatch(conversation_id):
            raise ConversationNotFound
        history = await store.turns(owner, conversation_id)
        if not history or now - history[0].at > EXPIRES_AFTER:
            raise ConversationNotFound
    if history:
        question_asked = await condenser.standalone(question, history[-HISTORY_TURNS:])
    else:
        question_asked = question
    reply = await ask(question_asked, router, agents, composer)
    await store.add(owner, conversation_id, Turn(question, reply.answer, now))
    return reply, conversation_id
