"""Read-only MCP tools bound to the authenticated mailbox for a single run."""

from collections.abc import Callable
from typing import Annotated, Any

from anyio.to_thread import run_sync
from fastapi import HTTPException
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import EmailStr, StringConstraints

from app.assistant.schemas import (
    DraftAction,
    DraftBody,
    DraftSubject,
    LabelAction,
    MessageId,
)
from app.auth.store import AuthStoreError
from app.mail.service import GmailService
from integrations.aws_secret import SecretUnavailableError

RECENT_MESSAGE_LIMIT = 8
MESSAGE_BODY_LIMIT = 6_000


def message_summaries(page: dict) -> list[dict]:
    return [
        {
            "id": message["id"],
            "from": message["from"][:320],
            "subject": message["subject"][:320],
            "date": message["date"][:100],
            "snippet": message["snippet"][:400],
            "unread": message["unread"],
        }
        for message in page["messages"]
    ]


def create_mailbox_server(
    mail_service: Callable[[], GmailService],
    user_id: str,
    open_message_id: str | None = None,
) -> MCPServer:
    server = MCPServer("mini-mailroom-mailbox", log_level="WARNING")
    read_only = ToolAnnotations(readOnlyHint=True)
    labels: list[dict] | None = None

    async def read_mail(operation: Callable):
        try:
            # The Gmail client is synchronous. A timed-out turn can stop waiting.
            return await run_sync(operation, abandon_on_cancel=True)
        except HTTPException as exc:
            raise ToolError(str(exc.detail)) from None
        except (AuthStoreError, SecretUnavailableError):
            raise ToolError("Mailbox temporarily unavailable.") from None

    async def mailbox_labels() -> list[dict]:
        nonlocal labels
        if labels is None:
            labels = await read_mail(lambda: mail_service().labels(user_id))
        return labels

    @server.tool(annotations=read_only, structured_output=True)
    async def list_recent_messages() -> dict[str, Any]:
        """List up to eight recent inbox messages with IDs and short snippets."""
        page = await read_mail(
            lambda: mail_service().inbox(user_id, limit=RECENT_MESSAGE_LIMIT)
        )
        return {
            "messages": message_summaries(page),
            "older_messages_available": bool(page["nextPageToken"]),
        }

    @server.tool(annotations=read_only, structured_output=True)
    async def search_mail(
        query: Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=400)
        ],
    ) -> dict[str, Any]:
        """Find up to eight messages with a Gmail search query, such as is:unread."""
        page = await read_mail(
            lambda: mail_service().search(user_id, query, limit=RECENT_MESSAGE_LIMIT)
        )
        return {
            "messages": message_summaries(page),
            "more_results_available": bool(page["nextPageToken"]),
        }

    @server.tool(annotations=read_only, structured_output=True)
    async def read_message(message_id: MessageId) -> dict[str, Any]:
        """Read the available plain text of a Gmail message by ID."""
        message = await read_mail(lambda: mail_service().message(user_id, message_id))
        return {
            "id": message["id"],
            "from": message["from"][:320],
            "subject": message["subject"][:320],
            "body": message["body"][:MESSAGE_BODY_LIMIT],
            "body_truncated": len(message["body"]) > MESSAGE_BODY_LIMIT,
        }

    @server.tool(annotations=read_only, structured_output=True)
    async def list_labels() -> dict[str, Any]:
        """List up to 40 existing user-created Gmail label names for suggestions."""
        available_labels = await mailbox_labels()
        return {
            "labels": [label["name"][:225] for label in available_labels[:40]],
            "more_labels_available": len(available_labels) > 40,
        }

    @server.tool(annotations=read_only, structured_output=True)
    async def prepare_draft(
        subject: DraftSubject, body: DraftBody, to: EmailStr | None = None
    ) -> DraftAction:
        """Offer an editable email draft in the chat for the user's review."""
        return DraftAction(kind="draft", subject=subject, body=body, to=to)

    if open_message_id:

        @server.tool(annotations=read_only, structured_output=True)
        async def read_open_message() -> dict[str, Any]:
            """Read the available plain text of the email currently included in this turn."""
            return await read_message(open_message_id)

        @server.tool(annotations=read_only, structured_output=True)
        async def suggest_label(
            label_name: Annotated[str, StringConstraints(min_length=1, max_length=225)],
        ) -> LabelAction:
            """Suggest applying an existing Gmail label to the included email."""
            for label in await mailbox_labels():
                if label["name"] == label_name:
                    return LabelAction(
                        kind="label",
                        message_id=open_message_id,
                        label_id=label["id"],
                        label_name=label["name"],
                    )
            raise ToolError("Choose an existing Gmail label returned by list_labels.")

    return server
