"""Chat API: POST /chat {"question", "conversation_id"?} -> {"answer", "refusal", "agents",
"citations", "conversation_id"} (D-029, D-041), behind the token middleware. With `Accept:
text/event-stream` the same turn streams: `step` and `lead` events as it goes, then `done` with that
body, or `error` with the message and status the JSON reply would have had (D-045)."""

import asyncio
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime

from margin_copilot_shared.security.context import current_request
from margin_copilot_shared.security.middleware import AuthMiddleware
from margin_copilot_shared.security.tokens import TokenValidator
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from sse_starlette import EventSourceResponse
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from orchestrator.application.ask import (
    Agent,
    AgentUnavailable,
    Composer,
    Progress,
    Router,
    quiet,
)
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
STREAM = "text/event-stream"
log = logging.getLogger(__name__)
running: set[asyncio.Task] = set()  # a streamed turn outlives a client that leaves


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
    async def chat_turn(request: Request) -> JSONResponse | EventSourceResponse:
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
        if STREAM not in request.headers.get("accept", ""):
            status, reply = await answer(question, conversation_id, owner, quiet)
            return JSONResponse(reply, status_code=status)
        events: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()
        request_span = trace.get_current_span()

        async def turn() -> None:
            def progress(event: str, data: dict[str, str]) -> None:
                events.put_nowait((event, data))

            try:
                status, reply = await answer(question, conversation_id, owner, progress)
            except Exception as error:  # the JSON path's 500, which a stream can't send
                log.error("turn failed: %s", type(error).__name__)
                status, reply = 500, {"error": "internal error"}
            if status >= 500:
                request_span.set_status(Status(StatusCode.ERROR))
            events.put_nowait(
                ("done", reply) if status == 200 else ("error", reply | {"status": status})
            )

        task = asyncio.create_task(turn())
        running.add(task)
        task.add_done_callback(running.discard)

        async def stream():
            while True:
                event, data = await events.get()
                yield {"event": event, "data": json.dumps(data, ensure_ascii=False)}
                if event in ("done", "error"):
                    return

        return EventSourceResponse(stream())

    async def answer(
        question: str, conversation_id: str | None, owner: str, progress: Progress
    ) -> tuple[int, dict]:
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
                    progress,
                )
        except TimeoutError:
            log.warning("turn timed out")
            return 502, {"error": "orchestrator unavailable"}
        except ConversationNotFound:
            return 404, {"error": "conversation not found"}
        except ConversationsUnavailable as unavailable:
            log.warning("conversations unavailable: %s", unavailable)
            return 502, {"error": "conversations unavailable"}
        except AgentUnavailable as unavailable:
            log.warning("%s unavailable: %s", unavailable.name, unavailable)
            return 502, {"error": f"{unavailable.name} unavailable"}
        return 200, asdict(reply) | {"conversation_id": conversation_id}

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
