"""Authenticated, same-origin API for a single assistant turn."""

import logging
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from groq import APIError, APITimeoutError, RateLimitError
from mcp import MCPError
from pydantic import BaseModel, StringConstraints

from app.assistant.agent import AssistantResponseError, run_assistant
from app.assistant.mail_tools import create_mailbox_server
from app.auth.rate_limit import limit_requests
from app.auth.security import require_origin, require_user
from app.auth.store import AuthStore
from app.config import Settings
from app.mail.service import GmailService
from integrations.aws_secret import SecretUnavailableError

logger = logging.getLogger(__name__)


class AssistantRequest(BaseModel):
    prompt: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000)
    ]


class AssistantResponse(BaseModel):
    answer: str


def create_assistant_router(
    settings: Settings,
    store: AuthStore,
    mail_service: Callable[[], GmailService],
    get_api_key: Callable[[], str] | None,
) -> APIRouter:
    router = APIRouter()

    def authorize(request: Request) -> tuple[str, str]:
        require_origin(request, settings)
        user = require_user(request, store, settings)
        if get_api_key is None:
            raise HTTPException(503, "Mail assistant is disabled.")
        limit_requests(
            store, "assistant", user["id"], settings.assistant_requests_per_minute
        )
        try:
            return user["id"], get_api_key().strip()
        except SecretUnavailableError:
            logger.warning("Groq credential lookup unavailable")
            raise HTTPException(
                503, "Mail assistant temporarily unavailable."
            ) from None

    @router.post("/api/mail/assistant")
    async def assist(
        body: AssistantRequest,
        credentials: Annotated[tuple[str, str], Depends(authorize)],
    ) -> AssistantResponse:
        user_id, api_key = credentials
        try:
            answer = await run_assistant(
                body.prompt,
                settings.groq_model,
                api_key,
                create_mailbox_server(mail_service, user_id),
            )
            return AssistantResponse(answer=answer)
        except* (APITimeoutError, TimeoutError):
            http_error = HTTPException(504, "Mail assistant timed out.")
        except* RateLimitError:
            http_error = HTTPException(503, "Mail assistant is busy. Try again later.")
        except* (APIError, MCPError, AssistantResponseError) as errors:
            error = errors.exceptions[0]
            while isinstance(error, BaseExceptionGroup):
                error = error.exceptions[0]
            logger.warning("Mail assistant run failed (%s)", type(error).__name__)
            http_error = HTTPException(
                502, "Mail assistant could not complete the request."
            )
        # Keep the HTTP exception out of MCP task groups on early Python 3.11.
        raise http_error from None

    return router
