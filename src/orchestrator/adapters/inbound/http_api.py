"""Chat API: POST /chat {"question", "conversation_id"?} -> {"answer", "refusal", "agents",
"citations", "conversation_id"} (D-029, D-041), behind the token middleware."""

import asyncio
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime

from margin_copilot_shared.security.context import current_request
from margin_copilot_shared.security.middleware import AuthMiddleware
from margin_copilot_shared.security.tokens import TokenValidator
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from orchestrator.application.ask import Agent, AgentUnavailable, Composer, Router
from orchestrator.application.conversations import (
    Condenser,
    ConversationNotFound,
    ConversationStore,
    ConversationsUnavailable,
    chat,
)

MAX_BODY_BYTES = 16_384
MAX_QUESTION_CHARS = 2_000
TURN_LIMIT = 230  # under the 240 s ingress and client limit; nothing is stored past it
FIELDS = {"question", "conversation_id"}  # history never comes from the request
log = logging.getLogger(__name__)


def create_http_app(
    router: Router,
    agents: Mapping[str, Agent],
    composer: Composer,
    condenser: Condenser,
    store: ConversationStore,
    validator: TokenValidator,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    turn_limit: float = TURN_LIMIT,
    allowed_origins: Sequence[str] = (),
) -> Starlette:
    async def chat_turn(request: Request) -> JSONResponse:
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            return JSONResponse({"error": "request too large"}, status_code=413)
        try:
            payload = json.loads(body)
        except ValueError:
            return JSONResponse({"error": "body must be JSON"}, status_code=400)
        if isinstance(payload, dict) and set(payload) - FIELDS:
            return JSONResponse({"error": "unknown field"}, status_code=400)
        question = payload.get("question") if isinstance(payload, dict) else None
        if not isinstance(question, str) or not question.strip():
            return JSONResponse({"error": "question is required"}, status_code=400)
        if len(question) > MAX_QUESTION_CHARS:
            return JSONResponse({"error": "question too long"}, status_code=413)
        conversation_id = payload.get("conversation_id")
        if conversation_id is not None and not isinstance(conversation_id, str):
            return JSONResponse({"error": "conversation_id must be a string"}, status_code=400)
        owner = current_request().caller.oid
        try:
            async with asyncio.timeout(turn_limit):
                reply, conversation_id = await chat(
                    question,
                    conversation_id,
                    owner,
                    clock(),
                    router,
                    agents,
                    composer,
                    condenser,
                    store,
                )
        except TimeoutError:
            log.warning("turn timed out")
            return JSONResponse({"error": "orchestrator unavailable"}, status_code=502)
        except ConversationNotFound:
            return JSONResponse({"error": "conversation not found"}, status_code=404)
        except ConversationsUnavailable as unavailable:
            log.warning("conversations unavailable: %s", unavailable)
            return JSONResponse({"error": "conversations unavailable"}, status_code=502)
        except AgentUnavailable as unavailable:
            log.warning("%s unavailable: %s", unavailable.name, unavailable)
            return JSONResponse({"error": f"{unavailable.name} unavailable"}, status_code=502)
        return JSONResponse(asdict(reply) | {"conversation_id": conversation_id})

    warming: dict[str, asyncio.Task] = {}

    async def warm_up(request: Request) -> JSONResponse:
        """D-044: answers at once and wakes the agents and the database; returns nothing. One
        warm-up at a time per process, so repeated calls can't multiply the work."""
        running = warming.get("task")
        if running is None or running.done():
            token = current_request().token
            warming["task"] = asyncio.create_task(
                asyncio.wait(
                    [asyncio.ensure_future(agent.warm(token)) for agent in agents.values()]
                )
            )
        return JSONResponse({}, status_code=202)

    middleware = [Middleware(AuthMiddleware, validator=validator)]
    if allowed_origins:
        # Outermost, so a browser's preflight is answered before the token check; CORS is a
        # convenience for the web app, the token check still decides (D-042).
        cors = Middleware(
            CORSMiddleware,
            allow_origins=list(allowed_origins),
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
            max_age=600,
        )
        middleware.insert(0, cors)
    routes = [
        Route("/chat", chat_turn, methods=["POST"]),
        Route("/warm", warm_up, methods=["POST"]),
    ]
    return Starlette(routes=routes, middleware=middleware)
