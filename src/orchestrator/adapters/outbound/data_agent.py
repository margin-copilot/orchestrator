"""DataAgent port adapters: A2A client with a fresh OBO token per call, and a fake."""

import asyncio
from collections.abc import Callable, Sequence

import httpx
from a2a.client import Client, ClientCallContext, ClientConfig, create_client
from a2a.helpers import get_data_parts, new_text_message
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, Role, SendMessageRequest
from a2a.utils.errors import A2AError
from margin_copilot_shared.contracts import AgentReply
from margin_copilot_shared.security.context import current_request
from margin_copilot_shared.security.obo import OboFailed
from pydantic import ValidationError

from orchestrator.application.ask import DataAgentUnavailable

Exchange = Callable[[str, Sequence[str]], str]


def data_agent_card(url: str) -> AgentCard:
    """Built from DATA_AGENT_URL. The agent's own card is served without a token, so its URL is
    never trusted as the place to send user tokens."""
    return AgentCard(
        name="data-agent",
        description="Riad Market sales data, read as the signed-in user.",
        version="0.2.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[
            AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")
        ],
    )


class A2ADataAgent:
    def __init__(self, url: str, scope: str, exchange: Exchange, http: httpx.AsyncClient) -> None:
        self._url = url
        self._scope = scope
        self._exchange = exchange
        self._http = http
        self._client: Client | None = None

    async def ask(self, question: str) -> AgentReply:
        try:
            token = await asyncio.to_thread(self._exchange, current_request().token, [self._scope])
        except OboFailed as failed:
            raise DataAgentUnavailable(f"token exchange failed: {failed}") from None
        except Exception as failed:  # managed identity or network failure inside the exchange
            raise DataAgentUnavailable(f"token exchange failed: {type(failed).__name__}") from None
        try:
            return await self._send(question, token)
        except (A2AError, httpx.HTTPError) as failed:  # A2AError covers client and JSON-RPC errors
            raise DataAgentUnavailable(f"data-agent call failed: {failed}") from None

    async def _send(self, question: str, token: str) -> AgentReply:
        if self._client is None:
            self._client = await create_client(
                data_agent_card(self._url),
                client_config=ClientConfig(httpx_client=self._http, streaming=False),
            )
        request = SendMessageRequest(message=new_text_message(question, role=Role.ROLE_USER))
        context = ClientCallContext(service_parameters={"Authorization": f"Bearer {token}"})
        async for event in self._client.send_message(request, context=context):
            if event.WhichOneof("payload") == "message":
                parts = get_data_parts(event.message.parts)
                if len(parts) != 1:
                    raise DataAgentUnavailable("data-agent reply isn't one data part")
                try:
                    return AgentReply.model_validate(parts[0])
                except ValidationError:
                    raise DataAgentUnavailable("data-agent reply broke the contract") from None
        raise DataAgentUnavailable("data-agent sent no reply")


class FakeDataAgent:
    async def ask(self, question: str) -> AgentReply:
        return AgentReply(answer=f"fake data-agent sees user {current_request().caller.oid}")
