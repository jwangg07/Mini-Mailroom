"""Authenticated, same-origin API for a single assistant turn."""

import logging
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from groq import (
    APIError,
    APIStatusError,
    APITimeoutError,
    BadRequestError,
    RateLimitError,
)
from mcp import MCPError

from app.assistant.agent import (
    AssistantResponseError,
    provider_error_code,
    run_assistant,
)
from app.assistant.schemas import AssistantRequest, AssistantResponse
from app.auth.models import User
from app.auth.rate_limit import limit_requests
from app.auth.security import require_origin, require_user
from app.auth.store import AuthStore
from app.config import Settings
from app.mail.service import GmailService
from integrations.aws_secret import SecretUnavailableError

logger = logging.getLogger(__name__)


def create_assistant_router(
    settings: Settings,
    store: AuthStore,
    mail_service: Callable[[], GmailService],
    get_api_key: Callable[[], str] | None,
) -> APIRouter:
    router = APIRouter()

    def authorize(request: Request) -> tuple[User, str]:
        require_origin(request, settings)
        user = require_user(request, store, settings)
        if get_api_key is None:
            raise HTTPException(503, "Mail assistant is disabled.")
        limit_requests(
            store, "assistant", user["id"], settings.assistant_requests_per_minute
        )
        try:
            return user, get_api_key().strip()
        except SecretUnavailableError:
            logger.warning("Groq credential lookup unavailable")
            raise HTTPException(
                503, "Mail assistant temporarily unavailable."
            ) from None

    @router.post("/api/mail/assistant", response_model_exclude_none=True)
    async def assist(
        body: AssistantRequest,
        credentials: Annotated[tuple[User, str], Depends(authorize)],
    ) -> AssistantResponse:
        user, api_key = credentials

        def limit_send():
            limit_requests(store, "send", user["id"], settings.send_requests_per_minute)

        try:
            return await run_assistant(
                body, settings.groq_model, api_key, user, mail_service, limit_send
            )
        except* (APIError, MCPError, TimeoutError, AssistantResponseError) as errors:
            error = errors.exceptions[0]
            while isinstance(error, BaseExceptionGroup):
                error = error.exceptions[0]
            if isinstance(error, APIStatusError):
                logger.warning(
                    "Groq request failed (status=%s, code=%s)",
                    error.status_code,
                    provider_error_code(error) or "unknown",
                )
            else:
                logger.warning("Mail assistant run failed (%s)", type(error).__name__)

            if isinstance(error, RateLimitError):
                retry_after = error.response.headers.get("Retry-After", "")
                detail = "Groq's rate limit was reached. Try again shortly."
                headers = None
                if (
                    retry_after.isascii()
                    and retry_after.isdecimal()
                    and len(retry_after) <= 9
                ):
                    seconds = int(retry_after)
                    unit = "second" if seconds == 1 else "seconds"
                    detail = (
                        f"Groq's rate limit was reached. Try again in {seconds} {unit}."
                    )
                    headers = {"Retry-After": retry_after}
                http_error = HTTPException(429, detail, headers=headers)
            elif isinstance(error, (APITimeoutError, TimeoutError)):
                http_error = HTTPException(504, "Mail assistant timed out.")
            elif isinstance(error, BadRequestError):
                http_error = HTTPException(502, "Groq rejected the assistant request.")
            else:
                http_error = HTTPException(
                    502, "Mail assistant could not complete the request."
                )
        # Keep the HTTP exception out of MCP task groups on early Python 3.11.
        raise http_error from None

    return router
