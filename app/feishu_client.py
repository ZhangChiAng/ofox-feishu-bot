"""Feishu client helpers for sending bot messages."""

from __future__ import annotations

import json
import logging
from typing import Any

import lark_oapi as lark
from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

from app.replies import BotReply, ReplyMessageType


logger = logging.getLogger(__name__)


class FeishuMessenger:
    """Sends bot replies through the Feishu OpenAPI client."""

    def __init__(self, client: Any, *, logger_: logging.Logger | None = None) -> None:
        """Initializes the messenger.

        Args:
            client: Feishu OpenAPI client.
            logger_: Optional logger override for tests.
        """

        self.client = client
        self.logger = logger_ or logger

    def send_text(self, receive_id_type: str, receive_id: str, text: str) -> bool:
        """Sends a single Feishu text message.

        Args:
            receive_id_type: Feishu receiver id type, such as ``chat_id``.
            receive_id: Receiver id value.
            text: Message body.

        Returns:
            ``True`` when Feishu accepts the message, otherwise ``False``.
        """

        return self._send_message(
            receive_id_type,
            receive_id,
            "text",
            {"text": text},
        )

    def send_reply(
        self,
        receive_id_type: str,
        receive_id: str,
        reply: BotReply,
    ) -> bool:
        """Sends a unified bot reply.

        Args:
            receive_id_type: Feishu receiver id type, such as ``chat_id``.
            receive_id: Receiver id value.
            reply: Reply payload built by command handlers.

        Returns:
            ``True`` when Feishu accepts the message, otherwise ``False``.
        """

        if reply.msg_type == "text":
            # Text replies may exceed Feishu's per-message limit, so split them.
            text = str(reply.content.get("text", ""))
            return self.send_long_text(receive_id_type, receive_id, text)

        # Interactive cards and other structured replies can be sent as-is.
        return self._send_message(
            receive_id_type,
            receive_id,
            reply.msg_type,
            reply.content,
        )

    def send_long_text(self, receive_id_type: str, receive_id: str, text: str) -> bool:
        """Sends long text by splitting it into Feishu-sized messages.

        Args:
            receive_id_type: Feishu receiver id type, such as ``chat_id``.
            receive_id: Receiver id value.
            text: Message body that may exceed one message limit.

        Returns:
            ``True`` only when every chunk is sent successfully.
        """

        ok = True
        for chunk in chunk_text(text):
            # Keep sending remaining chunks so partial delivery failures are logged.
            ok = self.send_text(receive_id_type, receive_id, chunk) and ok
        return ok

    def _send_message(
        self,
        receive_id_type: str,
        receive_id: str,
        msg_type: ReplyMessageType,
        content: dict[str, Any],
    ) -> bool:
        """Sends one Feishu message with JSON-encoded content.

        Args:
            receive_id_type: Feishu receiver id type, such as ``chat_id``.
            receive_id: Receiver id value.
            msg_type: Feishu message type.
            content: JSON-serializable message content.

        Returns:
            ``True`` when Feishu accepts the message, otherwise ``False``.
        """

        # Feishu expects every message payload as a JSON-encoded content string.
        request = (
            CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type(msg_type)
                .content(json.dumps(content, ensure_ascii=False))
                .build()
            )
            .build()
        )

        response = self.client.im.v1.message.create(request)
        if not response.success():
            self.logger.error(
                "Send message failed, code=%s, msg=%s, log_id=%s",
                response.code,
                response.msg,
                response.get_log_id(),
            )
            return False

        self.logger.info("Send message success")
        return True


def build_message_client(
    app_id: str,
    app_secret: str,
    *,
    log_level: lark.LogLevel = lark.LogLevel.INFO,
) -> Any:
    """Builds the Feishu OpenAPI client used for outgoing messages.

    Args:
        app_id: Feishu app id.
        app_secret: Feishu app secret.
        log_level: Feishu SDK log level.

    Returns:
        Configured Feishu client.
    """

    return (
        lark.Client.builder()
        .app_id(app_id)
        .app_secret(app_secret)
        .log_level(log_level)
        .build()
    )


def chunk_text(text: str, max_chars: int = 3500) -> list[str]:
    """Splits text into chunks that fit Feishu message size limits.

    Args:
        text: Text to split.
        max_chars: Maximum characters per chunk.

    Returns:
        Ordered chunks that preserve line boundaries where possible.

    Raises:
        ValueError: If ``max_chars`` is less than 1.
    """

    if max_chars < 1:
        raise ValueError("max_chars must be greater than 0")

    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.splitlines():
        # Very long lines are split first so each segment can be packed safely.
        for segment in _split_long_line(line, max_chars):
            if not current:
                current = segment
                continue

            candidate = f"{current}\n{segment}"
            if len(candidate) <= max_chars:
                current = candidate
            else:
                chunks.append(current)
                current = segment

    if current:
        chunks.append(current)
    return chunks or [""]


def _split_long_line(line: str, max_chars: int) -> list[str]:
    """Splits a single line into fixed-size segments when needed.

    Args:
        line: Line to split.
        max_chars: Maximum characters per segment.

    Returns:
        One or more segments.
    """

    if len(line) <= max_chars:
        return [line]
    return [line[index : index + max_chars] for index in range(0, len(line), max_chars)]
