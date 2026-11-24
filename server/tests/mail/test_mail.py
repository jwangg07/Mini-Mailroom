"""Credential lifecycle and Gmail routes using fake Google/AWS boundaries."""

import base64
import json
import time
from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from botocore.exceptions import ClientError
from fastapi import HTTPException
from fastapi.testclient import TestClient
from google.auth.exceptions import RefreshError

from app.application import create_app
from app.auth.models import Session
from app.auth.security import SESSION_COOKIE
from app.auth.store import CREDENTIAL_TTL, DynamoDBAuthStore, MemoryAuthStore
from app.config import Settings
from app.mail.credentials import PREFIX, GmailCredentials
from app.mail.gmail_client import GmailClient
from app.mail.service import GmailService
from integrations.google.credential_vault import CredentialVault


def provider_response(data):
    response = Mock(status_code=200, ok=True)
    response.iter_content.side_effect = lambda **kwargs: iter(
        [json.dumps(data).encode()]
    )
    return response


def credential(**overrides):
    fields = {
        "token": "access-secret",
        "refresh_token": "refresh-secret",
        "id_token": "id-secret",
        "expiry": datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
        "scopes": [PREFIX + "modify"],
        "granted_scopes": None,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


@pytest.fixture
def setup(monkeypatch):
    store, vault = MemoryAuthStore(), CredentialVault()
    credentials = GmailCredentials(store, vault, "client", lambda: "client-secret")
    service = GmailService(GmailClient(credentials))
    service.client.credentials.capture("alice", credential())
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    session.request.return_value = provider_response({"id": "abc123"})
    monkeypatch.setattr("app.mail.gmail_client.requests.Session", lambda: session)
    return store, vault, service, session


def test_capture_preserves_refresh_token_and_tracks_actual_scopes(setup):
    store, vault, service, _ = setup
    service.client.credentials.capture(
        "alice", credential(refresh_token=None, granted_scopes=[PREFIX + "readonly"])
    )
    stored = store.read_credentials("alice")
    assert "refresh-secret" not in stored
    data = vault.decrypt("alice", stored)
    assert data["refresh_token"] == "refresh-secret"
    assert data["scopes"] == [PREFIX + "readonly"]
    with pytest.raises(HTTPException) as exc:
        service.send("alice", "alice@example.com", "bob@example.com", "Hi", "Body")
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        service.trash("alice", "abc")
    assert exc.value.status_code == 409


def test_dynamodb_credentials_have_retention_ttl_and_conditional_refresh(monkeypatch):
    monkeypatch.setattr("app.auth.store.time.time", lambda: 1000)
    store = DynamoDBAuthStore("table")
    table = Mock()
    monkeypatch.setattr(store, "_table", lambda: table)
    store.save_credentials("alice", "encrypted")
    assert table.put_item.call_args.kwargs["Item"] == {
        "pk": "credentials#alice",
        "encrypted": "encrypted",
        "expires_at": 1000 + CREDENTIAL_TTL,
    }
    table.get_item.return_value = {
        "Item": {"encrypted": "encrypted", "expires_at": 1000 + CREDENTIAL_TTL}
    }
    assert store.read_credentials("alice") == "encrypted"
    assert table.get_item.call_args.kwargs["ConsistentRead"]
    assert store.replace_credentials("alice", "encrypted", "new")
    assert table.put_item.call_args.kwargs["ConditionExpression"] == "encrypted = :old"
    table.put_item.side_effect = ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem"
    )
    assert not store.replace_credentials("alice", "old", "new")


def test_automatic_refresh_is_persisted_before_gmail_call(setup, monkeypatch):
    store, vault, service, session = setup
    service.client.credentials.capture("alice", credential(expiry=datetime(2000, 1, 1)))

    def refresh(credentials, request):
        credentials.token = "new-access"
        credentials.expiry = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)

    mock = Mock(side_effect=refresh)
    monkeypatch.setattr(
        "app.mail.credentials.Credentials.refresh",
        lambda self, request: mock(self, request),
    )
    service.client.request("alice", "GET", "/messages")
    assert mock.call_count == 1
    assert (
        session.request.call_args.kwargs["headers"]["Authorization"]
        == "Bearer new-access"
    )
    assert (
        vault.decrypt("alice", store.read_credentials("alice"))["token"] == "new-access"
    )
    service.client.request("alice", "GET", "/messages")
    assert mock.call_count == 1


def test_refresh_does_not_overwrite_new_login(setup, monkeypatch):
    store, vault, service, session = setup
    service.client.credentials.capture("alice", credential(expiry=datetime(2000, 1, 1)))

    def refresh(credentials, request):
        service.client.credentials.capture(
            "alice", credential(token="new-login", refresh_token="new-grant")
        )
        credentials.token = "stale-refresh"
        credentials.expiry = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)

    monkeypatch.setattr("app.mail.credentials.Credentials.refresh", refresh)
    service.client.request("alice", "GET", "/messages")
    assert (
        session.request.call_args.kwargs["headers"]["Authorization"]
        == "Bearer new-login"
    )
    assert (
        vault.decrypt("alice", store.read_credentials("alice"))["refresh_token"]
        == "new-grant"
    )


@pytest.mark.parametrize("retryable,status", [(False, 409), (True, 503)])
def test_failed_refresh_never_calls_gmail_or_leaks_provider_text(
    setup, monkeypatch, retryable, status
):
    _, _, service, session = setup
    service.client.credentials.capture("alice", credential(expiry=datetime(2000, 1, 1)))
    monkeypatch.setattr(
        "app.mail.credentials.Credentials.refresh",
        Mock(side_effect=RefreshError("private-token", retryable=retryable)),
    )
    with pytest.raises(HTTPException) as exc:
        service.client.request("alice", "GET", "/messages")
    assert exc.value.status_code == status
    assert "private-token" not in exc.value.detail
    session.request.assert_not_called()


def test_401_refreshes_and_retries_once(setup, monkeypatch):
    _, _, service, session = setup
    session.request.side_effect = [Mock(status_code=401), Mock(status_code=401)]

    def refresh(credentials, request):
        credentials.token = "renewed"
        credentials.expiry = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)

    mock = Mock(side_effect=refresh)
    monkeypatch.setattr(
        "app.mail.credentials.Credentials.refresh",
        lambda self, request: mock(self, request),
    )
    with pytest.raises(HTTPException) as exc:
        service.client.request("alice", "GET", "/messages")
    assert exc.value.status_code == 409
    assert session.request.call_count == 2 and mock.call_count == 1


def test_send_creates_mime_and_never_retries_timeout(setup):
    _, _, service, session = setup
    service.send(
        "alice", "alice@example.com", "bob@example.com", "Hello ☀", "A letter."
    )
    call = session.request.call_args
    raw = call.kwargs["json"]["raw"]
    message = BytesParser(policy=policy.default).parsebytes(
        base64.urlsafe_b64decode(raw)
    )
    assert message["From"] == "alice@example.com" and message["To"] == "bob@example.com"
    assert (
        message["Subject"] == "Hello ☀" and message.get_content().strip() == "A letter."
    )
    session.request.reset_mock()
    session.request.side_effect = requests.Timeout("access-secret")
    with pytest.raises(HTTPException) as exc:
        service.send("alice", "alice@example.com", "bob@example.com", "Hi", "Body")
    assert "Check Sent" in exc.value.detail and session.request.call_count == 1


def test_read_html_and_plain_text_and_list_pagination(setup):
    _, _, service, session = setup
    session.request.return_value = provider_response(
        {
            "id": "abc",
            "labelIds": ["INBOX", "Label_123"],
            "payload": {
                "mimeType": "multipart/alternative",
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "body": {
                            "data": base64.urlsafe_b64encode(b"Safe text").decode()
                        },
                    },
                    {
                        "mimeType": "text/html",
                        "body": {
                            "data": base64.urlsafe_b64encode(
                                b"<b>Rich text</b>"
                            ).decode()
                        },
                    },
                ],
            },
        }
    )
    message = service.message("alice", "abc")
    assert message["body"] == "Safe text"
    assert message["html"] == "<b>Rich text</b>"
    assert message["labelIds"] == ["INBOX", "Label_123"]
    assert session.request.call_args.kwargs["params"]["format"] == "full"
    session.request.side_effect = [
        provider_response({"messages": [{"id": "abc"}], "nextPageToken": "next"}),
        provider_response(
            {
                "id": "abc",
                "labelIds": ["UNREAD"],
                "payload": {"headers": [{"name": "Subject", "value": "Hello"}]},
            }
        ),
    ]
    page = service.inbox("alice", "older")
    assert page["nextPageToken"] == "next" and page["messages"][0]["unread"]
    assert session.request.call_args_list[-2].kwargs["params"]["pageToken"] == "older"


def test_mailbox_labels_and_trash(setup, monkeypatch):
    store, vault, _, gmail = setup
    store.save_session(
        "session",
        Session({"id": "alice", "email": "alice@example.com"}, int(time.time()) + 600),
    )
    monkeypatch.setattr("app.application.CredentialVault", lambda *_: vault)
    gmail.request.side_effect = [
        provider_response(
            {
                "labels": [
                    {"id": "INBOX", "name": "INBOX", "type": "system"},
                    {"id": "Label_123", "name": "Work", "type": "user"},
                ]
            }
        ),
        provider_response({"labelIds": ["INBOX", "Label_123"]}),
        provider_response({"labelIds": ["INBOX"]}),
        provider_response({"id": "abc"}),
    ]
    path = "/api/mail/messages/abc"
    headers = {"X-Mailroom-Request": "1"}
    app = create_app(Settings(_env_file=None), store=store)
    with TestClient(app) as client:
        assert client.get("/api/mail/labels").status_code == 401
        assert client.post(path + "/trash", headers=headers).status_code == 401
        client.cookies.set(SESSION_COOKIE, "session")
        assert client.post(path + "/trash").status_code == 403
        assert (
            client.post(
                path + "/labels", json={"label_id": "Label_123", "applied": True}
            ).status_code
            == 403
        )
        assert client.get("/api/mail/labels").json() == [
            {"id": "Label_123", "name": "Work"}
        ]
        for applied, expected in [(True, ["INBOX", "Label_123"]), (False, ["INBOX"])]:
            response = client.post(
                path + "/labels",
                headers=headers,
                json={"label_id": "Label_123", "applied": applied},
            )
            assert response.json() == {"labelIds": expected}
        assert client.post(path + "/trash", headers=headers).json() == {"id": "abc"}

    labels, add, remove, trash = gmail.request.call_args_list
    assert labels.args[1].endswith("/labels")
    assert add.args[1].endswith("/messages/abc/modify")
    assert add.kwargs["json"] == {"addLabelIds": ["Label_123"]}
    assert remove.kwargs["json"] == {"removeLabelIds": ["Label_123"]}
    assert trash.args[1].endswith("/messages/abc/trash")
    assert trash.kwargs["params"] == {"fields": "id"}


def test_message_includes_cid_images_with_data_or_attachment_reference(setup):
    _, _, service, session = setup
    session.request.return_value = provider_response(
        {
            "id": "abc",
            "payload": {
                "mimeType": "multipart/related",
                "parts": [
                    {
                        "mimeType": "text/html",
                        "body": {
                            "data": base64.urlsafe_b64encode(
                                b'<img src="cid:logo"><img src="cid:photo">'
                            ).decode()
                        },
                    },
                    {"mimeType": "text/plain", "body": {"data": "VGV4dA"}},
                    {
                        "mimeType": "image/png",
                        "filename": "logo.png",
                        "headers": [{"name": "Content-ID", "value": "<logo>"}],
                        "body": {"data": "YWJj", "size": 3},
                    },
                    {
                        "mimeType": "image/jpeg",
                        "filename": "photo.jpg",
                        "headers": [{"name": "Content-ID", "value": "<photo>"}],
                        "body": {"attachmentId": "photo-id", "size": 1200},
                    },
                    {
                        "mimeType": "image/png",
                        "filename": "ordinary-attachment.png",
                        "body": {"attachmentId": "other-id", "size": 200},
                    },
                ],
            },
        }
    )

    message = service.message("alice", "abc")

    assert message["inlineImages"] == {
        "logo": {"mimeType": "image/png", "data": "YWJj"},
        "photo": {"mimeType": "image/jpeg", "attachmentId": "photo-id"},
    }
    session.request.assert_called_once()


def test_inline_images_batch_checks_session_and_reuses_credentials(setup, monkeypatch):
    store, vault, _, gmail = setup
    store.save_session(
        "session",
        Session({"id": "alice", "email": "alice@example.com"}, int(time.time()) + 600),
    )
    monkeypatch.setattr("app.application.CredentialVault", lambda *_: vault)
    credential_read = Mock(wraps=store.read_credentials)
    monkeypatch.setattr(store, "read_credentials", credential_read)

    def fetch(method, url, **kwargs):
        assert method == "GET"
        assert "/messages/abc/attachments/" in url
        if url.endswith("/missing"):
            return Mock(status_code=404)
        return provider_response({"data": "YWJj"})

    gmail.request.side_effect = fetch
    path = "/api/mail/messages/abc/inline-images"
    payload = {"attachment_ids": ["image-id", "image-id", "missing"]}
    headers = {"X-Mailroom-Request": "1"}
    with TestClient(create_app(Settings(_env_file=None), store=store)) as client:
        assert client.post(path, json=payload, headers=headers).status_code == 401
        client.cookies.set(SESSION_COOKIE, "session")
        assert client.post(path, json=payload).status_code == 403
        response = client.post(path, json=payload, headers=headers)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert response.json() == {"image-id": "YWJj"}

    credential_read.assert_called_once_with("alice")
    assert gmail.request.call_count == 2


def test_inbox_loads_and_decrypts_credentials_once_per_page(setup, monkeypatch):
    store, vault, service, session = setup
    read_credentials = Mock(wraps=store.read_credentials)
    decrypt = Mock(wraps=vault.decrypt)
    monkeypatch.setattr(store, "read_credentials", read_credentials)
    monkeypatch.setattr(vault, "decrypt", decrypt)
    ids = [str(index) for index in range(20)]

    def fetch(method, url, **kwargs):
        if url.endswith("/messages"):
            return provider_response({"messages": [{"id": id_} for id_ in ids]})
        return provider_response({"id": url.rsplit("/", 1)[-1]})

    session.request.side_effect = fetch

    for page_number in (1, 2):
        page = service.inbox("alice")
        assert [message["id"] for message in page["messages"]] == ids
        assert session.request.call_count == 21 * page_number
        assert read_credentials.call_count == decrypt.call_count == page_number


def test_read_recent_messages_fetches_bodies_and_reports_unreadable_mail(setup):
    _, _, service, session = setup

    def fetch(method, url, *, params, **kwargs):
        assert method == "GET"
        if url.endswith("/messages"):
            assert params == {
                "labelIds": "INBOX",
                "pageToken": "older",
                "maxResults": 2,
            }
            return provider_response(
                {"messages": [{"id": "a"}, {"id": "b"}], "nextPageToken": "next"}
            )
        assert params == {"format": "full"}
        if url.endswith("/b"):
            return Mock(status_code=404)
        return provider_response(
            {
                "id": "a",
                "payload": {
                    "mimeType": "text/plain",
                    "body": {
                        "data": base64.urlsafe_b64encode(b"Deadline Monday").decode()
                    },
                },
            }
        )

    session.request.side_effect = fetch
    page = service.recent_messages("alice", 2, "older")
    assert page["nextPageToken"] == "next"
    assert page["unreadable"] == 1
    assert [message["body"] for message in page["messages"]] == ["Deadline Monday"]
    assert session.request.call_count == 3


def test_inbox_workers_share_recovery_after_gmail_rejects_token(setup, monkeypatch):
    _, _, service, session = setup
    workers = Barrier(5, timeout=5)

    def fetch(method, url, *, headers, **kwargs):
        if url.endswith("/messages"):
            return provider_response(
                {"messages": [{"id": str(index)} for index in range(5)]}
            )
        if headers["Authorization"] == "Bearer access-secret":
            workers.wait()
            return Mock(status_code=401)
        assert headers["Authorization"] == "Bearer renewed"
        return provider_response({"id": url.rsplit("/", 1)[-1]})

    def refresh(credentials, request):
        credentials.token = "renewed"
        credentials.expiry = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)

    refresh_call = Mock(side_effect=refresh)
    monkeypatch.setattr(
        "app.mail.credentials.Credentials.refresh",
        lambda self, request: refresh_call(self, request),
    )
    session.request.side_effect = fetch

    page = service.inbox("alice")

    assert [message["id"] for message in page["messages"]] == ["0", "1", "2", "3", "4"]
    assert session.request.call_count == 11
    refresh_call.assert_called_once()


def test_mail_routes_authenticate_scope_user_and_protect_send(monkeypatch):
    store = MemoryAuthStore()
    store.save_session(
        "session",
        Session({"id": "alice", "email": "alice@example.com"}, int(time.time()) + 600),
    )
    app = create_app(Settings(_env_file=None), store=store)
    fake = Mock()
    fake.inbox.return_value = {"messages": [], "nextPageToken": None}
    fake.message.return_value = {"id": "abc"}
    fake.send.return_value = {"id": "sent"}
    monkeypatch.setattr(GmailService, "inbox", fake.inbox)
    monkeypatch.setattr(GmailService, "message", fake.message)
    monkeypatch.setattr(GmailService, "send", fake.send)
    with TestClient(app) as client:
        assert client.get("/api/mail/messages").status_code == 401
        client.cookies.set(SESSION_COOKIE, "session")
        response = client.get("/api/mail/messages?page_token=next")
        assert (
            response.status_code == 200
            and response.headers["Cache-Control"] == "no-store"
        )
        fake.inbox.assert_called_once_with("alice", "next")
        assert client.get("/api/mail/messages/abc").status_code == 200
        fake.message.assert_called_once_with("alice", "abc")
        payload = {"to": "bob@example.com", "subject": "Hi", "body": "Body"}
        assert client.post("/api/mail/send", json=payload).status_code == 403
        assert (
            client.post(
                "/api/mail/send",
                json=payload,
                headers={"X-Mailroom-Request": "1", "Origin": "https://evil.example"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/mail/send",
                json={**payload, "subject": "Hi\r\nBcc: attacker@example.com"},
                headers={"X-Mailroom-Request": "1"},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/mail/send", json=payload, headers={"X-Mailroom-Request": "1"}
            ).status_code
            == 200
        )
        fake.send.assert_called_once_with(
            "alice", "alice@example.com", "bob@example.com", "Hi", "Body"
        )


def test_credentials_are_not_shared_between_users(setup):
    _, _, service, session = setup
    with pytest.raises(HTTPException) as exc:
        service.client.request("bob", "GET", "/messages")
    assert exc.value.status_code == 409
    session.request.assert_not_called()


def test_missing_refresh_token_requires_reconnect(setup):
    store, vault, service, session = setup
    store.save_credentials(
        "alice",
        vault.encrypt(
            "alice",
            {
                "token": "old",
                "refresh_token": None,
                "expiry": None,
                "scopes": [PREFIX + "modify"],
            },
        ),
    )
    with pytest.raises(HTTPException) as exc:
        service.client.request("alice", "GET", "/messages")
    assert exc.value.status_code == 409
    session.request.assert_not_called()


def test_lambda_requires_kms(monkeypatch):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "mailroom")
    with pytest.raises(RuntimeError, match="CREDENTIAL_KMS_KEY_ID"):
        create_app(
            Settings(
                _env_file=None,
                dynamodb_table="auth",
                google_client_secret_arn="arn:secret",
                cookie_secure=True,
            )
        )
