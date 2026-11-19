"""Authenticated Gmail mailbox endpoints."""

from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Query, Request

from app.auth.rate_limit import limit_requests
from app.auth.security import require_origin, require_user
from app.auth.store import AuthStore
from app.config import Settings
from app.mail.schemas import InlineImageRequest, MailDraft, MailLabelChange
from app.mail.service import GmailService


def validate_message_id(message_id: str) -> None:
    if not message_id.isalnum() or len(message_id) > 128:
        raise HTTPException(400, "Invalid message ID")


def create_mail_router(
    settings: Settings,
    store: AuthStore,
    mail_service: Callable[[], GmailService],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/mail/messages")
    def inbox(request: Request, page_token: str = Query("", max_length=2048)):
        user = require_user(request, store, settings)
        limit_requests(store, "mail", user["id"], settings.mail_requests_per_minute)
        return mail_service().inbox(user["id"], page_token)

    @router.get("/api/mail/messages/{message_id}")
    def read_message(request: Request, message_id: str):
        user = require_user(request, store, settings)
        limit_requests(store, "mail", user["id"], settings.mail_requests_per_minute)
        validate_message_id(message_id)
        return mail_service().message(user["id"], message_id)

    @router.post("/api/mail/messages/{message_id}/inline-images")
    def inline_images(request: Request, message_id: str, images: InlineImageRequest):
        require_origin(request, settings)
        user = require_user(request, store, settings)
        limit_requests(store, "mail", user["id"], settings.mail_requests_per_minute)
        validate_message_id(message_id)
        return mail_service().inline_images(
            user["id"], message_id, images.attachment_ids
        )

    @router.get("/api/mail/labels")
    def labels(request: Request):
        user = require_user(request, store, settings)
        limit_requests(store, "mail", user["id"], settings.mail_requests_per_minute)
        return mail_service().labels(user["id"])

    @router.post("/api/mail/messages/{message_id}/labels")
    def change_label(request: Request, message_id: str, change: MailLabelChange):
        require_origin(request, settings)
        user = require_user(request, store, settings)
        limit_requests(store, "mail", user["id"], settings.mail_requests_per_minute)
        validate_message_id(message_id)
        return mail_service().change_label(
            user["id"], message_id, change.label_id, change.applied
        )

    @router.post("/api/mail/messages/{message_id}/trash")
    def trash(request: Request, message_id: str):
        require_origin(request, settings)
        user = require_user(request, store, settings)
        limit_requests(store, "mail", user["id"], settings.mail_requests_per_minute)
        validate_message_id(message_id)
        return mail_service().trash(user["id"], message_id)

    @router.post("/api/mail/send")
    def send_mail(request: Request, mail: MailDraft):
        require_origin(request, settings)
        user = require_user(request, store, settings)
        limit_requests(store, "send", user["id"], settings.send_requests_per_minute)
        return mail_service().send(
            user["id"], user["email"], str(mail.to), mail.subject, mail.body
        )

    return router
