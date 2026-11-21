"""In-memory adapters for ADAPTERS=fake and tests."""

from collections import defaultdict

from orchestrator.application.conversations import Turn


class MemoryConversationStore:
    def __init__(self) -> None:
        self._turns: dict[tuple[str, str], list[Turn]] = defaultdict(list)

    async def turns(self, owner: str, conversation_id: str) -> list[Turn]:
        return sorted(self._turns.get((owner, conversation_id), []), key=lambda turn: turn.at)

    async def add(self, owner: str, conversation_id: str, turn: Turn) -> None:
        self._turns[(owner, conversation_id)].append(turn)
