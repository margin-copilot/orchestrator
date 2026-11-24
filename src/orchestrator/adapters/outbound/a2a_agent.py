"""Agent port adapters: an A2A client with a fresh OBO token per call, and a fake."""

import asyncio
import logging
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

from orchestrator.application.ask import AgentUnavailable

Exchange = Callable[[str, Sequence[str]], str]
log = logging.getLogger(__name__)
WARM_LIMIT = 120  # seconds: a container start (~30 s), then a paused database resuming (~51 s)


def agent_card(name: str, url: str) -> AgentCard:
    """Built from the configured URL. An agent's own card is served without a token, so its URL is
    never trusted as the place to send user tokens."""
    return AgentCard(
        name=name,
        description=f"Margin Copilot {name}, called as the signed-in user.",
        version="0.3.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[
            AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")
        ],
    )


class A2AAgent:
    def __init__(
        self,
        name: str,
        url: str,
        scope: str,
        exchange: Exchange,
        http: httpx.AsyncClient,
        wakes_database: bool = False,
    ) -> None:
        self._name = name
        self._wakes_database = wakes_database
        self._url = url
        self._scope = scope
        self._exchange = exchange
        self._http = http
        self._client: Client | None = None

    async def ask(self, question: str) -> AgentReply:
        try:
            token = await asyncio.to_thread(self._exchange, current_request().token, [self._scope])
        except OboFailed as failed:
            raise AgentUnavailable(self._name, f"token exchange failed: {failed}") from None
        except Exception as failed:  # managed identity or network failure inside the exchange
            detail = f"token exchange failed: {type(failed).__name__}"
            raise AgentUnavailable(self._name, detail) from None
        try:
            return await self._send(question, token)
        except (A2AError, httpx.HTTPError) as failed:  # A2AError covers client and JSON-RPC errors
            raise AgentUnavailable(self._name, f"call failed: {failed}") from None

    async def warm(self, user_token: str) -> None:
        """Starts the agent's container (its card needs no token); the data agent also resumes the
        database with a query as the user (D-044). Best effort: never raises."""
        base = self._url.rstrip("/")
        try:
            async with asyncio.timeout(WARM_LIMIT):
                await self._http.get(f"{base}/.well-known/agent-card.json")
                if self._wakes_database:
                    token = await asyncio.to_thread(self._exchange, user_token, [self._scope])
                    await self._http.post(
                        f"{base}/warm", headers={"Authorization": f"Bearer {token}"}
                    )
        except Exception as error:
            log.warning("warm failed: %s: %s", self._name, type(error).__name__)

    async def _send(self, question: str, token: str) -> AgentReply:
        if self._client is None:
            self._client = await create_client(
                agent_card(self._name, self._url),
                client_config=ClientConfig(httpx_client=self._http, streaming=False),
            )
        request = SendMessageRequest(message=new_text_message(question, role=Role.ROLE_USER))
        context = ClientCallContext(service_parameters={"Authorization": f"Bearer {token}"})
        async for event in self._client.send_message(request, context=context):
            if event.WhichOneof("payload") == "message":
                parts = get_data_parts(event.message.parts)
                if len(parts) != 1:
                    raise AgentUnavailable(self._name, "reply isn't one data part")
                try:
                    return AgentReply.model_validate(parts[0])
                except ValidationError:
                    raise AgentUnavailable(self._name, "reply broke the contract") from None
        raise AgentUnavailable(self._name, "sent no reply")


class FakeAgent:
    def __init__(self, name: str) -> None:
        self._name = name

    async def warm(self, user_token: str) -> None:
        pass

    async def ask(self, question: str) -> AgentReply:
        oid = current_request().caller.oid
        return AgentReply(answer=f"fake {self._name} sees user {oid}")
