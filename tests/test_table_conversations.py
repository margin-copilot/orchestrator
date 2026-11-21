from datetime import UTC, datetime

from orchestrator.adapters.outbound.table_conversations import TableConversationStore
from orchestrator.application.conversations import Turn

CID = "c" * 32


class FakeTableClient:
    def __init__(self):
        self.entities, self.queries = [], []

    async def create_entity(self, entity):
        self.entities.append(entity)

    def query_entities(self, query_filter, parameters):
        self.queries.append((query_filter, parameters))

        async def matching():
            for e in sorted(self.entities, key=lambda e: e["RowKey"]):
                lo, hi = parameters["lo"], parameters["hi"]
                if e["PartitionKey"] == parameters["owner"] and lo <= e["RowKey"] < hi:
                    yield dict(e)

        return matching()


async def test_turns_are_read_from_the_owners_partition_in_order():
    client = FakeTableClient()
    store = TableConversationStore(client)
    first = Turn("q1", "a1", datetime(2026, 11, 21, 9, 0, tzinfo=UTC))
    second = Turn("q2", "a2", datetime(2026, 11, 21, 9, 5, tzinfo=UTC))
    await store.add("north", CID, second)
    await store.add("north", CID, first)
    await store.add("south", CID, first)
    assert await store.turns("north", CID) == [first, second]
    assert client.queries[0] == (
        "PartitionKey eq @owner and RowKey ge @lo and RowKey lt @hi",
        {"owner": "north", "lo": f"{CID}-", "hi": f"{CID}."},
    )


async def test_row_keys_sort_by_time():
    client = FakeTableClient()
    await TableConversationStore(client).add(
        "north", CID, Turn("q", "a", datetime(2026, 11, 21, 9, 0, 0, 120, tzinfo=UTC))
    )
    assert client.entities[0]["RowKey"] == f"{CID}-20261121T090000000120"
