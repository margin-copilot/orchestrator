"""ConversationStore on Azure Table Storage (D-010, D-041): PartitionKey = the owner's oid, so a
query can only ever read the caller's own turns; RowKey = conversation ID + time, so turns sort.
Keyless: the orchestrator's managed identity holds a data role on this table only."""

from datetime import datetime
from typing import Any

from orchestrator.application.conversations import Turn


class TableConversationStore:
    def __init__(self, client: Any) -> None:  # azure.data.tables.aio.TableClient
        self._client = client

    async def turns(self, owner: str, conversation_id: str) -> list[Turn]:
        entities = self._client.query_entities(
            query_filter="PartitionKey eq @owner and RowKey ge @lo and RowKey lt @hi",
            parameters={"owner": owner, "lo": f"{conversation_id}-", "hi": f"{conversation_id}."},
        )
        turns = [
            Turn(e["question"], e["answer"], datetime.fromisoformat(e["at"]))
            async for e in entities
        ]
        return sorted(turns, key=lambda turn: turn.at)

    async def add(self, owner: str, conversation_id: str, turn: Turn) -> None:
        await self._client.create_entity(
            entity={
                "PartitionKey": owner,
                "RowKey": f"{conversation_id}-{turn.at:%Y%m%dT%H%M%S%f}",
                "question": turn.question,
                "answer": turn.answer,
                "at": turn.at.isoformat(),
            }
        )
