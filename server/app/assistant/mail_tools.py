"""Read-only MCP tools bound to the authenticated mailbox for a single run."""

from collections.abc import Callable
from typing import Annotated, Any

from anyio.to_thread import run_sync
from fastapi import HTTPException
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from app.auth.store import AuthStoreError
from app.mail.service import GmailService
from integrations.aws_secret import SecretUnavailableError

RECENT_MESSAGE_LIMIT = 8
MESSAGE_BODY_LIMIT = 6_000


def create_mailbox_server(
    mail_service: Callable[[], GmailService], user_id: str
) -> MCPServer:
    server = MCPServer("mini-mailroom-mailbox", log_level="WARNING")
    read_only = ToolAnnotations(readOnlyHint=True)

    async def read_mail(operation: Callable):
        try:
            # The Gmail client is synchronous. A timed-out turn can stop waiting.
            return await run_sync(operation, abandon_on_cancel=True)
        except HTTPException as exc:
            raise ToolError(str(exc.detail)) from None
        except (AuthStoreError, SecretUnavailableError):
            raise ToolError("Mailbox temporarily unavailable.") from None

    @server.tool(annotations=read_only, structured_output=True)
    async def list_recent_messages() -> dict[str, Any]:
        """List up to eight recent inbox messages with IDs and short snippets."""
        page = await read_mail(
            lambda: mail_service().inbox(user_id, limit=RECENT_MESSAGE_LIMIT)
        )
        return {
            "messages": [
                {
                    "id": message["id"],
                    "from": message["from"][:320],
                    "subject": message["subject"][:320],
                    "date": message["date"][:100],
                    "snippet": message["snippet"][:400],
                    "unread": message["unread"],
                }
                for message in page["messages"]
            ],
            "older_messages_available": bool(page["nextPageToken"]),
        }

    @server.tool(annotations=read_only, structured_output=True)
    async def read_message(
        message_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9]{1,128}$")],
    ) -> dict[str, Any]:
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
        labels = await read_mail(lambda: mail_service().labels(user_id))
        return {
            "labels": [label["name"][:225] for label in labels[:40]],
            "more_labels_available": len(labels) > 40,
        }

    return server
