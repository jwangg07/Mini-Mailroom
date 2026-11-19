"""Gmail mailbox operations."""

from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

from fastapi import HTTPException

from app.mail.credentials import MODIFY_SCOPES
from app.mail.formatting import (
    MAX_INLINE_IMAGE_DATA,
    encode_draft,
    mail_message,
    mail_summary,
)
from app.mail.gmail_client import GmailClient


class GmailService:
    def __init__(self, client: GmailClient):
        self.client = client

    def inbox(self, user_id: str, page_token: str = "") -> dict:
        request = self.client.for_requests(user_id, "GET").request
        page = request(
            "/messages",
            params={
                "labelIds": "INBOX",
                "maxResults": 20,
                "pageToken": page_token,
            },
        )

        def summary(item):
            message = request(
                "/messages/" + quote(item["id"], safe=""),
                params={
                    "format": "metadata",
                    "metadataHeaders": ["From", "Subject", "Date"],
                },
            )
            return mail_summary(message)

        with ThreadPoolExecutor(max_workers=5) as pool:
            messages = list(pool.map(summary, page.get("messages", [])[:20]))
        return {"messages": messages, "nextPageToken": page.get("nextPageToken")}

    def message(self, user_id: str, message_id: str) -> dict:
        data = self.client.request(
            user_id,
            "GET",
            "/messages/" + quote(message_id, safe=""),
            params={"format": "full"},
        )
        return mail_message(data)

    def inline_images(
        self, user_id: str, message_id: str, attachment_ids: list[str]
    ) -> dict[str, str]:
        request = self.client.for_requests(user_id, "GET").request
        path = "/messages/" + quote(message_id, safe="") + "/attachments/"

        def fetch(attachment_id: str) -> str:
            try:
                data = request(path + quote(attachment_id, safe="")).get("data", "")
            except HTTPException as exc:
                if exc.status_code in (404, 413):
                    return ""
                raise
            return (
                data
                if isinstance(data, str) and len(data) <= MAX_INLINE_IMAGE_DATA
                else ""
            )

        ids = list(dict.fromkeys(attachment_ids))
        images = {}
        remaining = 2_000_000
        with ThreadPoolExecutor(max_workers=4) as pool:
            for attachment_id, data in zip(ids, pool.map(fetch, ids)):
                if data and len(data) <= remaining:
                    images[attachment_id] = data
                    remaining -= len(data)
        return images

    def labels(self, user_id: str) -> list[dict]:
        data = self.client.request(
            user_id, "GET", "/labels", params={"fields": "labels(id,name,type)"}
        )
        return [
            {"id": label["id"], "name": label["name"]}
            for label in data.get("labels", [])
            if label["type"] == "user"
        ]

    def change_label(
        self, user_id: str, message_id: str, label_id: str, applied: bool
    ) -> dict:
        field = "addLabelIds" if applied else "removeLabelIds"
        data = self.client.request(
            user_id,
            "POST",
            "/messages/" + quote(message_id, safe="") + "/modify",
            scopes=MODIFY_SCOPES,
            json={field: [label_id]},
            params={"fields": "labelIds"},
        )
        return {"labelIds": data.get("labelIds", [])}

    def trash(self, user_id: str, message_id: str) -> dict:
        return self.client.request(
            user_id,
            "POST",
            "/messages/" + quote(message_id, safe="") + "/trash",
            scopes=MODIFY_SCOPES,
            params={"fields": "id"},
        )

    def send(self, user_id: str, sender: str, to: str, subject: str, body: str) -> dict:
        data = self.client.request(
            user_id,
            "POST",
            "/messages/send",
            json={"raw": encode_draft(sender, to, subject, body)},
        )
        return {"id": data["id"], "threadId": data.get("threadId")}
