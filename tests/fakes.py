"""Test double: a scripted model."""

from collections.abc import Sequence
from typing import Any

from agent_framework import BaseChatClient, ChatResponse, FunctionInvocationLayer, Message


class ScriptedChatClient(FunctionInvocationLayer, BaseChatClient):
    """Replays scripted model turns and records what the model was sent. `_inner_get_response` is
    Agent Framework's private extension point (1.26.0); re-check it when upgrading."""

    def __init__(self, turns: Sequence[Message], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.turns, self.calls = list(turns), []

    async def _inner_get_response(self, *, messages, stream, options, **kwargs) -> ChatResponse:
        self.calls.append((list(messages), dict(options)))
        return ChatResponse(messages=[self.turns.pop(0)])
