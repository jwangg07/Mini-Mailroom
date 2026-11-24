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

    def inbox(self, user_id: str, page_token: str = "", *, limit: int = 20) -> dict:
        return self._message_page(
            user_id, {"labelIds": "INBOX", "pageToken": page_token}, limit
        )

    def recent_messages(self, user_id: str, limit: int, page_token: str = "") -> dict:
        return self._message_page(
            user_id, {"labelIds": "INBOX", "pageToken": page_token}, limit, full=True
        )

    def search(
        self, user_id: str, query: str, *, limit: int, page_token: str = ""
    ) -> dict:
        return self._message_page(user_id, {"q": query, "pageToken": page_token}, limit)

    def _message_page(
        self, user_id: str, filters: dict, limit: int, *, full: bool = False
    ) -> dict:
        request = self.client.for_requests(user_id, "GET").request
        page = request("/messages", params={**filters, "maxResults": limit})
        params = (
            {"format": "full"}
            if full
            else {"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]}
        )
        format_message = mail_message if full else mail_summary

        def load(item):
            try:
                return format_message(
                    request("/messages/" + quote(item["id"], safe=""), params=params)
                )
            except HTTPException:
                if full:
                    return None
                raise

        with ThreadPoolExecutor(max_workers=5) as pool:
            messages = list(pool.map(load, page.get("messages", [])[:limit]))
        result = {"messages": messages, "nextPageToken": page.get("nextPageToken")}
        if full:
            result["unreadable"] = messages.count(None)
            result["messages"] = [
                message for message in messages if message is not None
            ]
        return result

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
        return self._change_labels(user_id, message_id, {field: [label_id]})

    def change_labels(
        self, user_id: str, message_id: str, add: list[str], remove: list[str]
    ) -> dict:
        changes = {}
        if add:
            changes["addLabelIds"] = add
        if remove:
            changes["removeLabelIds"] = remove
        return self._change_labels(user_id, message_id, changes)

    def _change_labels(self, user_id: str, message_id: str, changes: dict) -> dict:
        data = self.client.request(
            user_id,
            "POST",
            "/messages/" + quote(message_id, safe="") + "/modify",
            scopes=MODIFY_SCOPES,
            json=changes,
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
