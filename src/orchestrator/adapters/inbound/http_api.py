"""Chat API: POST /chat {"question": ...} -> {"answer", "refusal", "agents", "citations"} (D-029),
behind the token middleware."""

import json
import logging
from collections.abc import Mapping
from dataclasses import asdict

from margin_copilot_shared.security.middleware import AuthMiddleware
from margin_copilot_shared.security.tokens import TokenValidator
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from orchestrator.application.ask import Agent, AgentUnavailable, Router, ask

MAX_BODY_BYTES = 16_384
MAX_QUESTION_CHARS = 2_000
log = logging.getLogger(__name__)


def create_http_app(
    router: Router, agents: Mapping[str, Agent], validator: TokenValidator
) -> Starlette:
    async def chat(request: Request) -> JSONResponse:
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            return JSONResponse({"error": "request too large"}, status_code=413)
        try:
            payload = json.loads(body)
        except ValueError:
            return JSONResponse({"error": "body must be JSON"}, status_code=400)
        question = payload.get("question") if isinstance(payload, dict) else None
        if not isinstance(question, str) or not question.strip():
            return JSONResponse({"error": "question is required"}, status_code=400)
        if len(question) > MAX_QUESTION_CHARS:
            return JSONResponse({"error": "question too long"}, status_code=413)
        try:
            reply = await ask(question, router, agents)
        except AgentUnavailable as unavailable:
            log.warning("%s unavailable: %s", unavailable.name, unavailable)
            return JSONResponse({"error": f"{unavailable.name} unavailable"}, status_code=502)
        return JSONResponse(asdict(reply))

    return Starlette(
        routes=[Route("/chat", chat, methods=["POST"])],
        middleware=[Middleware(AuthMiddleware, validator=validator)],
    )
