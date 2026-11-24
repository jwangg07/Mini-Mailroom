"""MCP tools and confirmed results for a signed-in user's assistant turn."""

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from email.utils import parseaddr
from typing import Annotated, Any, Literal

from anyio.to_thread import run_sync
from fastapi import HTTPException
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, EmailStr, Field, StringConstraints, ValidationError

from app.assistant.schemas import (
    AssistantAction,
    AssistantRequest,
    AssistantResponse,
    DraftAction,
    DraftBody,
    DraftContext,
    DraftSubject,
    LabelAction,
    MessageId,
    MessageLabelUpdate,
    WritePermission,
)
from app.auth.models import User
from app.auth.store import AuthStoreError
from app.mail.schemas import MailDraft
from app.mail.service import GmailService
from integrations.aws_secret import SecretUnavailableError

PageSize = Annotated[int, Field(ge=1, le=20)]
MessageIds = Annotated[list[MessageId], Field(max_length=20)]
PageToken = Annotated[str, Field(max_length=2048)]
SearchQuery = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=400)
]
SEND_UNKNOWN = "Gmail did not confirm the send. Check Sent before retrying."
EMAIL_ADDRESS = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)


class LabelChange(BaseModel, extra="forbid"):
    message_id: MessageId
    add: list[str] = Field(default_factory=list, max_length=20)
    remove: list[str] = Field(default_factory=list, max_length=20)


@dataclass
class TurnResults:
    actions: list[AssistantAction] = field(default_factory=list)
    labels: dict[str, MessageLabelUpdate] = field(default_factory=dict)
    delivery_notes: list[str] = field(default_factory=list)
    draft_delivery: Literal["sent", "uncertain"] | None = None
    label_failures: set[str] = field(default_factory=set)

    def response(self, answer: str = "") -> AssistantResponse:
        if not answer:
            notes = self.delivery_notes.copy()
            if self.labels:
                notes.append(
                    f"Gmail confirmed label updates for {len(self.labels)} emails."
                )
            if self.label_failures:
                notes.append(
                    f"Gmail could not confirm {len(self.label_failures)} label updates."
                )
            if self.actions:
                notes.append("Your draft or label suggestion is ready.")
            answer = "\n\n".join(notes)
        return AssistantResponse(
            answer=answer,
            actions=self.actions or None,
            label_updates=list(self.labels.values()) or None,
            draft_delivery=self.draft_delivery,
        )


def excerpt(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    tail = limit // 4
    return text[: limit - tail - 5] + "\n[…]\n" + text[-tail:]


async def gmail_call(operation: Callable):
    try:
        return await run_sync(operation, abandon_on_cancel=True)
    except HTTPException as exc:
        raise ToolError(str(exc.detail)) from None
    except (AuthStoreError, SecretUnavailableError):
        raise ToolError("Mailbox temporarily unavailable.") from None


def create_mailbox_server(
    mail_service: Callable[[], GmailService],
    user: User,
    request: AssistantRequest,
    results: TurnResults,
    authorize: Callable[[], Awaitable[WritePermission]],
    limit_send: Callable[[], None],
) -> MCPServer:
    server = MCPServer("mini-mailroom-mailbox", log_level="WARNING")
    read_only = ToolAnnotations(readOnlyHint=True)
    user_id = user["id"]
    draft = request.draft if isinstance(request.draft, DraftContext) else None
    visible_messages = {request.message_id} if request.message_id else set()
    explicit_recipients = {
        match.casefold() for match in EMAIL_ADDRESS.findall(request.prompt)
    }
    mailbox_recipients: set[str] = set()
    changed_messages: set[str] = set()
    sent: set[tuple[str, str, str]] = set()
    uncertain: set[str] = set()
    labels: dict[str, str] | None = None

    def expose(messages: list[dict], *, full: bool) -> list[dict]:
        allowance = min(2_400, 4_000 // max(1, len(messages))) if full else 180
        output = []
        for message in messages:
            visible_messages.add(message["id"])
            _, address = parseaddr(message["from"])
            if address:
                mailbox_recipients.add(address.casefold())
            text = message.get("body" if full else "snippet", "")
            item = {
                "id": message["id"],
                "from": message["from"][:240],
                "subject": message["subject"][:240],
                "date": message.get("date", ""),
                "body" if full else "snippet": excerpt(text, allowance),
            }
            if full:
                item["labels"] = message.get("labelIds", [])
            output.append(item)
        return output

    async def existing_labels() -> dict[str, str]:
        nonlocal labels
        if labels is None:
            items = await gmail_call(lambda: mail_service().labels(user_id))
            labels = {item["id"]: item["name"] for item in items}
        return labels

    @server.tool(annotations=read_only, structured_output=True)
    async def read_recent_messages(
        limit: PageSize = 5, page_token: PageToken = "", include_labels: bool = False
    ) -> dict[str, Any]:
        """Read recent inbox emails. Set include_labels for the label catalog. Page for older mail."""
        page = await gmail_call(
            lambda: mail_service().recent_messages(user_id, limit, page_token)
        )
        return {
            "emails": expose(page["messages"], full=True),
            "next_page_token": page.get("nextPageToken"),
            "unreadable": page.get("unreadable", 0),
            **(await list_labels() if include_labels else {}),
        }

    @server.tool(annotations=read_only, structured_output=True)
    async def search_mail(
        query: SearchQuery, limit: PageSize = 8, page_token: PageToken = ""
    ) -> dict[str, Any]:
        """Find email IDs and senders using Gmail search syntax."""
        page = await gmail_call(
            lambda: mail_service().search(
                user_id, query, limit=limit, page_token=page_token
            )
        )
        return {
            "emails": expose(page["messages"], full=False),
            "next_page_token": page.get("nextPageToken"),
        }

    @server.tool(annotations=read_only, structured_output=True)
    async def read_messages(
        message_ids: MessageIds | None = None, include_labels: bool = False
    ) -> dict[str, Any]:
        """Read emails by ID or the open email. Set include_labels for the label catalog."""
        ids = message_ids or ([request.message_id] if request.message_id else [])
        if not ids:
            raise ToolError("Search for the email or provide its ID.")
        fetched = await asyncio.gather(
            *(
                gmail_call(
                    lambda message_id=message_id: mail_service().message(
                        user_id, message_id
                    )
                )
                for message_id in dict.fromkeys(ids)
            ),
            return_exceptions=True,
        )
        return {
            "emails": expose(
                [item for item in fetched if isinstance(item, dict)], full=True
            ),
            "errors": [str(item) for item in fetched if isinstance(item, ToolError)],
            **(await list_labels() if include_labels else {}),
        }

    @server.tool(annotations=read_only, structured_output=True)
    async def list_labels(query: str = "") -> dict[str, Any]:
        """List existing user-defined Gmail label IDs and names. Filter by name when useful."""
        available = {
            label_id: name
            for label_id, name in (await existing_labels()).items()
            if query.casefold() in name.casefold()
        }
        return {
            "labels": dict(list(available.items())[:80]),
            "more": len(available) > 80,
        }

    @server.tool(annotations=read_only, structured_output=True)
    async def prepare_draft(
        subject: DraftSubject, body: DraftBody, to: EmailStr | None = None
    ) -> str:
        """Show an editable email draft to the user."""
        results.actions.append(
            DraftAction(kind="draft", subject=subject, body=body, to=to)
        )
        return "Draft prepared."

    @server.tool(annotations=read_only, structured_output=True)
    async def suggest_label(label_id: str, message_id: MessageId | None = None) -> str:
        """Show a label suggestion without applying it."""
        target = message_id or request.message_id
        available = await existing_labels()
        if target not in visible_messages or label_id not in available:
            raise ToolError("Choose a retrieved email and an existing label.")
        results.actions.append(
            LabelAction(
                kind="label",
                message_id=target,
                label_id=label_id,
                label_name=available[label_id],
            )
        )
        return "Label suggestion prepared."

    @server.tool(structured_output=True)
    async def change_labels(
        updates: Annotated[list[LabelChange], Field(min_length=1, max_length=20)],
    ) -> dict[str, Any]:
        """Add or remove existing Gmail labels on retrieved emails. Combine updates in one call."""
        permission = await authorize()
        if not permission.labels:
            raise ToolError("The latest request did not authorize changing labels.")
        available = await existing_labels()
        targets = {update.message_id for update in updates}
        if (
            len(targets) != len(updates)
            or not targets.issubset(visible_messages)
            or (
                permission.label_limit is not None
                and len(targets | changed_messages) > permission.label_limit
            )
            or any(
                set(update.add) & set(update.remove)
                or not set(update.add + update.remove).issubset(available)
                or not (update.add or update.remove)
                for update in updates
            )
        ):
            raise ToolError(
                "Use the requested number of retrieved emails and existing label IDs, with one update per email."
            )

        async def change(update: LabelChange) -> dict:
            message_id = update.message_id
            results.label_failures.add(message_id)
            try:
                receipt = await gmail_call(
                    lambda: mail_service().change_labels(
                        user_id,
                        message_id,
                        list(dict.fromkeys(update.add)),
                        list(dict.fromkeys(update.remove)),
                    )
                )
            except ToolError as exc:
                return {"id": message_id, "error": str(exc)}
            ids = receipt["labelIds"]
            if not set(update.add).issubset(ids) or set(update.remove).intersection(
                ids
            ):
                return {"id": message_id, "error": "Gmail did not confirm this update."}
            results.label_failures.discard(message_id)
            results.labels[message_id] = MessageLabelUpdate(
                message_id=message_id, label_ids=ids
            )
            return {
                "id": message_id,
                "added": [available[label] for label in update.add],
                "removed": [available[label] for label in update.remove],
            }

        changed_messages.update(targets)
        return {
            "updates": await asyncio.gather(*(change(update) for update in updates))
        }

    async def deliver(
        to: str, subject: str, body: str, *, current_draft: bool = False
    ) -> str:
        if not (await authorize()).send:
            raise ToolError("The latest request did not authorize sending email.")
        if not body.strip():
            raise ToolError("The email needs a message body.")
        try:
            outgoing = MailDraft(to=to, subject=subject, body=body)
        except ValidationError:
            raise ToolError(
                "A valid recipient address, subject, and message body are needed."
            ) from None
        address = str(outgoing.to)
        normalized = address.casefold()
        if explicit_recipients:
            recipients = explicit_recipients
        else:
            recipients = mailbox_recipients
            if draft and draft.to:
                recipients = recipients | {draft.to.strip().casefold()}
        if normalized not in recipients:
            raise ToolError(
                "Resolve the recipient from the user's request, a draft, or a Gmail sender."
            )
        key = (normalized, subject, body)
        current_draft = current_draft or bool(
            draft and key == (draft.to.strip().casefold(), draft.subject, draft.body)
        )
        if key in sent:
            return f"Already sent to {address} in this turn."
        if normalized in uncertain:
            raise ToolError(SEND_UNKNOWN)

        await gmail_call(limit_send)
        uncertain.add(normalized)
        unknown_note = (
            f"Delivery to {address} is unconfirmed. Check Sent before retrying."
        )
        results.delivery_notes.append(unknown_note)
        if current_draft:
            results.draft_delivery = "uncertain"
        try:
            receipt = await run_sync(
                lambda: mail_service().send(
                    user_id, user["email"], address, subject, body
                ),
                abandon_on_cancel=True,
            )
        except HTTPException as exc:
            if exc.status_code < 500 and exc.status_code != 408:
                uncertain.discard(normalized)
                results.delivery_notes.remove(unknown_note)
                if current_draft:
                    results.draft_delivery = None
                raise ToolError(str(exc.detail)) from None
            raise ToolError(SEND_UNKNOWN) from None
        except (AuthStoreError, SecretUnavailableError):
            raise ToolError(SEND_UNKNOWN) from None
        if not receipt.get("id"):
            raise ToolError(SEND_UNKNOWN)
        uncertain.discard(normalized)
        sent.add(key)
        results.delivery_notes.remove(unknown_note)
        note = f"Email sent to {address}."
        results.delivery_notes.append(note)
        results.actions[:] = [
            action
            for action in results.actions
            if not (
                isinstance(action, DraftAction)
                and ((action.to or "").casefold(), action.subject, action.body) == key
            )
        ]
        if current_draft:
            results.draft_delivery = "sent"
        return note

    @server.tool(structured_output=True)
    async def send_email(to: EmailStr, subject: DraftSubject, body: DraftBody) -> str:
        """Send an email from the user's account when the user asks you to send it."""
        return await deliver(to, subject, body)

    if draft:

        @server.tool(annotations=read_only, structured_output=True)
        async def read_draft() -> dict[str, str]:
            """Read the draft currently in chat."""
            return {
                "to": draft.to,
                "subject": draft.subject,
                "body": excerpt(draft.body, 4_000),
            }

        @server.tool(structured_output=True)
        async def send_current_draft() -> str:
            """Send the current draft exactly as written."""
            return await deliver(
                draft.to, draft.subject, draft.body, current_draft=True
            )

    return server
