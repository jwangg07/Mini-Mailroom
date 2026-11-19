"""Convert Gmail payloads to display data and drafts to MIME messages."""

import base64
from email import policy
from email.message import EmailMessage, Message

from fastapi import HTTPException

MAX_INLINE_IMAGES = 12
MAX_INLINE_IMAGE_BYTES = 1_048_576
MAX_INLINE_IMAGE_DATA = 1_400_000
INLINE_IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/avif",
}


def _headers(part: dict) -> dict[str, str]:
    return {
        header["name"].lower(): header["value"] for header in part.get("headers", [])
    }


def mail_summary(data: dict) -> dict:
    headers = _headers(data.get("payload", {}))
    return {
        "id": data["id"],
        "threadId": data.get("threadId"),
        "from": headers.get("from", ""),
        "subject": headers.get("subject", "(No subject)"),
        "date": headers.get("date", ""),
        "snippet": data.get("snippet", ""),
        "unread": "UNREAD" in data.get("labelIds", []),
    }


def mail_message(data: dict) -> dict:
    payload = data.get("payload", {})
    headers = _headers(payload)
    bodies: dict[str, str] = {}
    images: dict[str, dict] = {}
    fallback = "This message has no displayable body. View it in Gmail."
    pending = [(payload, 0)]
    visited = 0
    while pending:
        part, depth = pending.pop()
        visited += 1
        if depth > 20 or visited > 100:
            raise HTTPException(413, "This message is too complex. Open it in Gmail.")
        part_headers = _headers(part)
        mime_type = part.get("mimeType")
        body = part.get("body", {})
        content_id = part_headers.get("content-id", "").strip().strip("<>")
        image_data, attachment_id = body.get("data"), body.get("attachmentId")
        if (
            mime_type in INLINE_IMAGE_TYPES
            and content_id
            and len(content_id) <= 512
            and content_id not in images
            and len(images) < MAX_INLINE_IMAGES
            and body.get("size", 0) <= MAX_INLINE_IMAGE_BYTES
            and (image_data or attachment_id)
        ):
            if image_data and len(image_data) <= MAX_INLINE_IMAGE_DATA:
                images[content_id] = {"mimeType": mime_type, "data": image_data}
            elif attachment_id:
                images[content_id] = {
                    "mimeType": mime_type,
                    "attachmentId": attachment_id,
                }
        if (
            mime_type == "message/rfc822"
            or part.get("filename")
            or part_headers.get("content-disposition", "")
            .lower()
            .startswith("attachment")
        ):
            continue
        if mime_type in ("text/plain", "text/html") and mime_type not in bodies:
            encoded = body.get("data", "")
            if not encoded:
                if body.get("attachmentId"):
                    fallback = "This message is too large to preview. Open it in Gmail."
                continue
            if len(encoded) > 1_400_000:
                raise HTTPException(413, "This message is too large. Open it in Gmail.")
            try:
                raw = base64.b64decode(
                    encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
                )
                content_type = Message(policy=policy.default)
                content_type["Content-Type"] = part_headers.get(
                    "content-type", mime_type
                )
                try:
                    text = raw.decode(
                        content_type.get_content_charset() or "utf-8", errors="replace"
                    )
                except LookupError:
                    text = raw.decode("utf-8", errors="replace")
            except (ValueError, TypeError):
                if mime_type == "text/html":
                    continue
                raise HTTPException(502, "Gmail returned an invalid message.") from None
            bodies[mime_type] = text
        pending.extend((child, depth + 1) for child in reversed(part.get("parts", [])))
    return {
        "id": data["id"],
        "from": headers.get("from", ""),
        "subject": headers.get("subject", "(No subject)"),
        "body": bodies.get("text/plain", fallback),
        "html": bodies.get("text/html"),
        "inlineImages": images if bodies.get("text/html") else {},
        "labelIds": data.get("labelIds", []),
    }


def encode_draft(sender: str, to: str, subject: str, body: str) -> str:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
