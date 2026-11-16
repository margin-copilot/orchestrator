"""DataAgent port adapters: A2A client with a fresh OBO token per call, and a fake."""

import asyncio
from collections.abc import Callable, Sequence

import httpx
from a2a.client import Client, ClientCallContext, ClientConfig, create_client
from a2a.client.errors import A2AClientError
from a2a.helpers import get_message_text, new_text_message
from a2a.types import Role, SendMessageRequest
from margin_copilot_shared.security.context import current_request
from margin_copilot_shared.security.obo import OboFailed

from orchestrator.application.ask import DataAgentUnavailable

MAX_REPLY_CHARS = 8000
Exchange = Callable[[str, Sequence[str]], str]


class A2ADataAgent:
    def __init__(self, url: str, scope: str, exchange: Exchange, http: httpx.AsyncClient) -> None:
        self._url = url
        self._scope = scope
        self._exchange = exchange
        self._http = http
        self._client: Client | None = None

    async def ask(self, question: str) -> str:
        try:
            token = await asyncio.to_thread(self._exchange, current_request().token, [self._scope])
        except OboFailed as failed:
            raise DataAgentUnavailable(f"token exchange failed: {failed.error}") from None
        try:
            return await self._send(question, token)
        except (A2AClientError, httpx.HTTPError) as failed:
            raise DataAgentUnavailable(f"data-agent call failed: {failed}") from None

    async def _send(self, question: str, token: str) -> str:
        if self._client is None:
            self._client = await create_client(
                self._url, client_config=ClientConfig(httpx_client=self._http, streaming=False)
            )
        request = SendMessageRequest(message=new_text_message(question, role=Role.ROLE_USER))
        context = ClientCallContext(service_parameters={"Authorization": f"Bearer {token}"})
        async for event in self._client.send_message(request, context=context):
            if event.WhichOneof("payload") == "message":
                reply = get_message_text(event.message)
                if len(reply) > MAX_REPLY_CHARS:
                    raise DataAgentUnavailable("data-agent reply too large")
                return reply
        raise DataAgentUnavailable("data-agent sent no reply")


class FakeDataAgent:
    async def ask(self, question: str) -> str:
        return f"fake data-agent sees user {current_request().caller.oid}"
