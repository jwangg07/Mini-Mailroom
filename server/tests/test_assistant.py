"""Exercise the HTTP, Groq tool-calling, and MCP path with fake provider I/O."""

import json
import time
from types import SimpleNamespace
from unittest.mock import Mock, call

import httpx
import pytest
from fastapi.testclient import TestClient
from groq import AsyncGroq
from pydantic import SecretStr

from app.application import create_app
from app.auth.models import Session
from app.auth.security import SESSION_COOKIE
from app.auth.store import MemoryAuthStore
from app.config import Settings
from app.mail.service import GmailService

PATH = "/api/mail/assistant"
HEADERS = {"X-Mailroom-Request": "1"}


def post(client: TestClient):
    return client.post(PATH, json={"prompt": "Help with my inbox."}, headers=HEADERS)


def completion(content=None, *, tool_calls=None, finish_reason="stop"):
    return {
        "id": "completion",
        "created": 1,
        "model": "openai/gpt-oss-20b",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls,
                },
            }
        ],
    }


def tool_call(name, arguments, call_id="call-1"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


@pytest.fixture
def setup(monkeypatch):
    store = MemoryAuthStore()
    store.save_session(
        "session",
        Session({"id": "alice", "email": "alice@example.com"}, int(time.time()) + 300),
    )
    gmail = Mock(spec=GmailService)
    monkeypatch.setattr("app.application.GmailService", lambda _: gmail)
    return store, gmail


def mock_groq(monkeypatch, *responses):
    requests = []
    pending = iter(responses)

    def handle(request):
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["Authorization"] == "Bearer fake-groq-key"
        requests.append(json.loads(request.content))
        response = next(pending)
        return (
            response
            if isinstance(response, httpx.Response)
            else httpx.Response(200, json=response)
        )

    def client(**kwargs):
        return AsyncGroq(
            **kwargs,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        )

    monkeypatch.setattr("app.assistant.agent.AsyncGroq", client)
    return requests


def test_assistant_uses_signed_in_mailbox_and_minimizes_model_input(setup, monkeypatch):
    store, gmail = setup
    now = int(time.time())
    clock = SimpleNamespace(time=lambda: now)
    monkeypatch.setattr("app.auth.rate_limit.time", clock)
    monkeypatch.setattr("app.auth.security.time", clock)
    gmail.inbox.return_value = {
        "messages": [
            {
                "id": "abc123",
                "from": "Lee <lee@example.com>",
                "subject": "Review",
                "date": "",
                "snippet": "Please review.",
                "unread": True,
            }
        ],
        "nextPageToken": "private-page-token",
    }
    gmail.message.return_value = {
        "id": "abc123",
        "from": "Lee <lee@example.com>",
        "subject": "Review",
        "body": "x" * 7_000,
        "html": "<b>private-html</b>",
        "inlineImages": {"cid": {"data": "attachment-data"}},
    }
    gmail.labels.return_value = [{"id": "Label_1", "name": "Work"}]
    gmail.search.return_value = gmail.inbox.return_value
    requests = mock_groq(
        monkeypatch,
        completion(
            tool_calls=[tool_call("list_recent_messages", "{}", "list")],
            finish_reason="tool_calls",
        ),
        completion(
            tool_calls=[tool_call("search_mail", '{"query":"is:unread"}', "search")],
            finish_reason="tool_calls",
        ),
        completion(
            tool_calls=[tool_call("read_message", '{"message_id":"abc123"}')],
            finish_reason="tool_calls",
        ),
        completion(
            tool_calls=[tool_call("list_labels", "{}", "labels")],
            finish_reason="tool_calls",
        ),
        completion("Draft: Thanks for the review."),
    )
    secret = Mock()
    secret.get_secret_value.return_value = {
        "SecretString": '{"GROQ_API_KEY":"fake-groq-key"}'
    }
    session = Mock()
    session.client.return_value = secret
    monkeypatch.setattr(
        "integrations.aws_secret.boto3.session.Session", lambda: session
    )
    settings = Settings(
        _env_file=None,
        groq_api_key_secret_arn="secret",
        assistant_requests_per_minute=1,
    )
    with TestClient(create_app(settings, store=store)) as client:
        assert client.get("/health").status_code == 200
        assert post(client).status_code == 401
        client.cookies.set(SESSION_COOKIE, "session")
        assert (
            client.post(
                PATH,
                headers=HEADERS | {"Origin": "https://other.example"},
                json={"prompt": "Test"},
            ).status_code
            == 403
        )
        secret.get_secret_value.assert_not_called()
        assert requests == []

        response = post(client)
        assert response.status_code == 200
        assert response.json() == {"answer": "Draft: Thanks for the review."}
        assert response.headers["Cache-Control"] == "no-store"
        assert post(client).status_code == 429

    secret.get_secret_value.assert_called_once_with(SecretId="secret")
    gmail.inbox.assert_called_once_with("alice", limit=8)
    gmail.search.assert_called_once_with("alice", "is:unread", limit=8)
    gmail.message.assert_called_once_with("alice", "abc123")
    gmail.labels.assert_called_once_with("alice")
    assert {tool["function"]["name"] for tool in requests[0]["tools"]} == {
        "list_recent_messages",
        "read_message",
        "list_labels",
        "search_mail",
        "prepare_draft",
    }
    inbox = json.loads(requests[1]["messages"][-1]["content"])
    assert inbox["messages"][0]["id"] == "abc123"
    assert inbox["older_messages_available"] is True
    assert "private-page-token" not in json.dumps(inbox)
    search = json.loads(requests[2]["messages"][-1]["content"])
    assert search["messages"][0]["id"] == "abc123"
    assert search["more_results_available"] is True
    result = requests[3]["messages"][-1]
    assert result["tool_call_id"] == "call-1"
    data = json.loads(result["content"])
    assert data["body_truncated"] is True
    assert len(data["body"]) == 6_000
    assert "html" not in data and "inlineImages" not in data
    assert json.loads(requests[4]["messages"][-1]["content"])["labels"] == ["Work"]
    assert requests[4]["tool_choice"] == "none"


def test_chat_uses_open_email_history_and_returns_review_actions(setup, monkeypatch):
    store, gmail = setup
    gmail.message.return_value = {
        "id": "abc123",
        "from": "Lee <lee@example.com>",
        "subject": "Review",
        "body": "Can you review this today?",
    }
    gmail.labels.return_value = [{"id": "Label_1", "name": "Work"}]
    requests = mock_groq(
        monkeypatch,
        completion(
            tool_calls=[tool_call("read_open_message", "{}", "read")],
            finish_reason="tool_calls",
        ),
        completion(
            tool_calls=[tool_call("list_labels", "{}", "list")],
            finish_reason="tool_calls",
        ),
        completion(
            tool_calls=[
                tool_call(
                    "prepare_draft",
                    '{"to":"lee@example.com","subject":"Re: Review","body":"I can review it today."}',
                    "draft",
                ),
                tool_call("suggest_label", '{"label_name":"Work"}', "label"),
            ],
            finish_reason="tool_calls",
        ),
        completion("Your reply and label suggestion are ready."),
    )
    settings = Settings(_env_file=None, groq_api_key=SecretStr("fake-groq-key"))
    with TestClient(create_app(settings, store=store)) as client:
        client.cookies.set(SESSION_COOKIE, "session")
        response = client.post(
            PATH,
            headers=HEADERS,
            json={
                "prompt": "Draft that reply and suggest a label.",
                "message_id": "abc123",
                "history": [{"prompt": "Can I reply today?", "answer": "Yes."}],
            },
        )
    assert response.status_code == 200
    assert response.json() == {
        "answer": "Your reply and label suggestion are ready.",
        "actions": [
            {
                "kind": "draft",
                "to": "lee@example.com",
                "subject": "Re: Review",
                "body": "I can review it today.",
            },
            {
                "kind": "label",
                "message_id": "abc123",
                "label_id": "Label_1",
                "label_name": "Work",
            },
        ],
    }
    assert gmail.mock_calls == [call.message("alice", "abc123"), call.labels("alice")]
    assert requests[0]["messages"][1:3] == [
        {"role": "user", "content": "Can I reply today?"},
        {"role": "assistant", "content": "Yes."},
    ]


def test_invalid_mcp_arguments_and_write_tools_cannot_touch_mail(setup, monkeypatch):
    store, gmail = setup
    requests = mock_groq(
        monkeypatch,
        completion(
            tool_calls=[
                tool_call("send_mail", "{}", "write"),
                tool_call("read_message", '{"message_id":"../abc"}', "invalid-id"),
                tool_call("list_labels", "[]", "invalid-json-object"),
            ],
            finish_reason="tool_calls",
        ),
        completion("No mailbox changes were made."),
    )
    settings = Settings(_env_file=None, groq_api_key=SecretStr("fake-groq-key"))
    with TestClient(create_app(settings, store=store)) as client:
        client.cookies.set(SESSION_COOKIE, "session")
        assert post(client).status_code == 200
    assert not gmail.mock_calls
    for message in requests[1]["messages"][-3:]:
        assert message["role"] == "tool"
        assert "error" in json.loads(message["content"])


def test_disabled_and_provider_failure_responses(setup, monkeypatch):
    store, _ = setup
    with TestClient(create_app(Settings(_env_file=None), store=store)) as client:
        client.cookies.set(SESSION_COOKIE, "session")
        response = post(client)
        assert response.status_code == 503
        assert response.json()["detail"] == "Mail assistant is disabled."

    mock_groq(monkeypatch, httpx.Response(500, json={"error": "private provider text"}))
    settings = Settings(_env_file=None, groq_api_key=SecretStr("fake-groq-key"))
    with TestClient(create_app(settings, store=store)) as client:
        client.cookies.set(SESSION_COOKIE, "session")
        response = post(client)
        assert response.status_code == 502
        assert response.json() == {
            "detail": "Mail assistant could not complete the request."
        }
