import json
from types import SimpleNamespace
from typing import Any

from app.feishu_client import FeishuMessenger, chunk_text
from app.replies import BotReply


class FakeResponse:
    def __init__(self, code: int = 0, msg: str = "ok", data: Any = None) -> None:
        self.code = code
        self.msg = msg
        self.data = data

    def success(self) -> bool:
        return self.code == 0

    def get_log_id(self) -> str:
        return "log-id"


class FakeApi:
    def __init__(self, response: FakeResponse | None = None) -> None:
        self.requests: list[Any] = []
        self._response = response

    def create(self, request: Any) -> FakeResponse:
        self.requests.append(request)
        return self._response if self._response else FakeResponse()


class FakeV1:
    def __init__(self, image_response: FakeResponse | None = None) -> None:
        self.message = FakeApi()
        self.image = FakeApi(
            image_response or FakeResponse(data=SimpleNamespace(image_key="img-key"))
        )


class FakeIm:
    def __init__(self, image_response: FakeResponse | None = None) -> None:
        self.v1 = FakeV1(image_response)


class FakeClient:
    def __init__(self, image_response: FakeResponse | None = None) -> None:
        self.im = FakeIm(image_response)


def test_chunk_text_splits_long_single_line() -> None:
    chunks = chunk_text("abcdefghij", max_chars=3)

    assert chunks == ["abc", "def", "ghi", "j"]
    assert all(len(chunk) <= 3 for chunk in chunks)


def test_chunk_text_splits_on_line_boundaries_when_possible() -> None:
    chunks = chunk_text("alpha\nbeta\ngamma", max_chars=10)

    assert chunks == ["alpha\nbeta", "gamma"]
    assert all(len(chunk) <= 10 for chunk in chunks)


def test_chunk_text_rejects_invalid_limit() -> None:
    try:
        chunk_text("x", max_chars=0)
    except ValueError as exc:
        assert "max_chars" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_send_reply_serializes_interactive_card() -> None:
    client = FakeClient()
    card = {
        "schema": "2.0",
        "header": {"title": {"tag": "plain_text", "content": "模型报告"}},
        "body": {"elements": [{"tag": "markdown", "content": "| 提供商 | 模型数 |"}]},
    }
    messenger = FeishuMessenger(client)

    ok = messenger.send_reply("chat_id", "chat-id", BotReply.interactive(card))

    assert ok is True
    request = client.im.v1.message.requests[0]
    assert request.receive_id_type == "chat_id"
    assert request.request_body.receive_id == "chat-id"
    assert request.request_body.msg_type == "interactive"
    assert json.loads(request.request_body.content) == card


def test_send_reply_splits_text_replies(monkeypatch: Any) -> None:
    client = FakeClient()
    messenger = FeishuMessenger(client)

    monkeypatch.setattr("app.feishu_client.chunk_text", lambda text: ["alpha", "beta"])

    ok = messenger.send_reply("chat_id", "chat-id", BotReply.text("alpha\nbeta"))

    assert ok is True
    requests = client.im.v1.message.requests
    assert [request.request_body.msg_type for request in requests] == ["text", "text"]
    assert [json.loads(request.request_body.content) for request in requests] == [
        {"text": "alpha"},
        {"text": "beta"},
    ]


def test_send_reply_uploads_image_then_sends_image_key() -> None:
    client = FakeClient()
    messenger = FeishuMessenger(client)

    ok = messenger.send_reply("chat_id", "chat-id", BotReply.image(b"png bytes"))

    assert ok is True
    image_request = client.im.v1.image.requests[0]
    assert image_request.request_body.image_type == "message"
    assert image_request.request_body.image.read() == b"png bytes"

    message_request = client.im.v1.message.requests[0]
    assert message_request.request_body.msg_type == "image"
    assert json.loads(message_request.request_body.content) == {"image_key": "img-key"}


def test_send_reply_stops_when_image_upload_fails() -> None:
    client = FakeClient(FakeResponse(code=999, msg="failed"))
    messenger = FeishuMessenger(client)

    ok = messenger.send_reply("chat_id", "chat-id", BotReply.image(b"png bytes"))

    assert ok is False
    assert len(client.im.v1.image.requests) == 1
    assert client.im.v1.message.requests == []
