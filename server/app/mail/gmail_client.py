"""Authenticated Gmail HTTP requests and provider error translation."""

import json
from concurrent.futures import Future
from datetime import UTC, datetime
from threading import Lock

import requests
from fastapi import HTTPException

from app.auth.store import AuthStoreError
from app.mail.credentials import READ_SCOPES, SEND_SCOPES, GmailCredentials, reconnect

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class GmailClient:
    def __init__(self, credentials: GmailCredentials):
        self.credentials = credentials

    def request(self, user_id: str, method: str, path: str, **kwargs) -> dict:
        return self.for_requests(user_id, method).request(path, **kwargs)

    def for_requests(self, user_id: str, method: str) -> "_GmailRequests":
        return _GmailRequests(self.credentials, user_id, method)


class _GmailRequests:
    """Credentials for the Gmail calls made by one app request."""

    def __init__(self, credentials: GmailCredentials, user_id: str, method: str):
        self.credentials = credentials
        self.user_id = user_id
        self.method = method
        self._recovery_lock = Lock()
        self._recovery: Future[str] = Future()
        self._token = self._authorize(*credentials.load(user_id))

    def _authorize(self, encrypted: str, data: dict, *, force: bool = False) -> str:
        needed = READ_SCOPES if self.method == "GET" else SEND_SCOPES
        if not needed.intersection(data["scopes"]):
            raise reconnect()
        expiry = (
            datetime.fromisoformat(data["expiry"]).replace(tzinfo=UTC).timestamp()
            if data.get("expiry")
            else 0
        )
        if (
            force
            or not data.get("token")
            or expiry <= datetime.now(UTC).timestamp() + 60
        ):
            data = self.credentials.refresh(self.user_id, encrypted, data)
        if not needed.intersection(data["scopes"]):
            raise reconnect()
        return data["token"]

    def _recover(self) -> str:
        with self._recovery_lock:
            if not self._recovery.done():
                try:
                    # Another app request may already have refreshed or disconnected.
                    encrypted, latest = self.credentials.load(self.user_id)
                    token = self._authorize(
                        encrypted, latest, force=latest.get("token") == self._token
                    )
                except (HTTPException, AuthStoreError) as exc:
                    self._recovery.set_exception(exc)
                else:
                    self._recovery.set_result(token)
        return self._recovery.result()

    def request(self, path: str, *, _retry: bool = False, **kwargs) -> dict:
        recovered = _retry or self._recovery.done()
        token = self._recover() if recovered else self._token
        method = self.method
        try:
            # No automatic transport retries: a timed-out send might have succeeded.
            with requests.Session() as session:
                response = session.request(
                    method,
                    GMAIL + path,
                    headers={"Authorization": "Bearer " + token},
                    timeout=20,
                    stream=True,
                    allow_redirects=False,
                    **kwargs,
                )
                if response.status_code == 200:
                    payload = bytearray()
                    try:
                        for chunk in response.iter_content(chunk_size=65536):
                            payload.extend(chunk)
                            if len(payload) > MAX_RESPONSE_BYTES:
                                raise HTTPException(
                                    413,
                                    "This message is too large for Mini Mailroom. Open it in Gmail.",
                                )
                    finally:
                        response.close()
                else:
                    response.close()
        except requests.RequestException:
            detail = (
                "Send result unknown. Check Sent in Gmail before sending again."
                if method == "POST"
                else "Gmail temporarily unavailable."
            )
            raise HTTPException(503, detail) from None
        if response.status_code == 401:
            if recovered:
                raise reconnect()
            # Google rejected this request before processing it. Refresh and retry once.
            return self.request(path, _retry=True, **kwargs)
        if response.status_code == 403:
            raise HTTPException(
                403,
                "Gmail denied access. Check granted permissions and that Gmail API is enabled.",
            )
        if response.status_code == 404:
            raise HTTPException(404, "Message not found.")
        if response.status_code == 429:
            raise HTTPException(429, "Gmail rate limit reached. Try again later.")
        if response.status_code != 200:
            detail = (
                "Send result unknown. Check Sent in Gmail before sending again."
                if method == "POST" and response.status_code >= 500
                else "Gmail could not complete this request."
            )
            raise HTTPException(502, detail)
        try:
            result = json.loads(payload)
            if not isinstance(result, dict):
                raise TypeError
            return result
        except (ValueError, TypeError):
            raise HTTPException(502, "Gmail returned an invalid response.") from None
