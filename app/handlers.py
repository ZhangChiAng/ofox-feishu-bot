"""Feishu event handlers that translate callbacks into bot replies."""

from __future__ import annotations

import json
import logging
import threading
import time as time_module
from typing import Any

import lark_oapi as lark
from lark_oapi.api.application.v6 import P2ApplicationBotMenuV6
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

from app.commands import build_reply_for_menu_event, build_reply_for_text
from app.feishu_client import FeishuMessenger
from app.replies import BotReply
from app.reports import ReportService


logger = logging.getLogger(__name__)
DEFAULT_MESSAGE_MAX_AGE_SECONDS = 120


class MessageDeduplicator:
    """Tracks recently seen Feishu message ids within one worker process."""

    def __init__(self, ttl_seconds: int) -> None:
        """Initializes the in-memory dedupe cache.

        Args:
            ttl_seconds: Number of seconds to retain seen message ids.
        """

        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl_seconds = ttl_seconds
        self._seen_at: dict[str, float] = {}
        self._lock = threading.Lock()

    def check_and_mark_seen(self, message_id: str | None) -> bool:
        """Returns ``True`` when ``message_id`` was already seen."""

        if not message_id:
            return False

        now = time_module.monotonic()
        with self._lock:
            self._remove_expired(now)
            if message_id in self._seen_at:
                return True

            self._seen_at[message_id] = now
        return False

    def _remove_expired(self, now: float) -> None:
        """Drops ids outside the dedupe TTL."""

        expired = [
            message_id
            for message_id, seen_at in self._seen_at.items()
            if now - seen_at > self._ttl_seconds
        ]
        for message_id in expired:
            del self._seen_at[message_id]


def handle_message_event(
    data: P2ImMessageReceiveV1,
    reports: ReportService,
    messenger: FeishuMessenger,
    *,
    deduplicator: MessageDeduplicator | None = None,
    max_message_age_seconds: int = DEFAULT_MESSAGE_MAX_AGE_SECONDS,
) -> None:
    """Handles a typed Feishu message event from the SDK dispatcher.

    Args:
        data: SDK event object for ``im.message.receive_v1``.
        reports: Report builder used by command handlers.
        messenger: Feishu message sender.
    """

    handle_message_payload(
        _event_to_dict(data),
        reports,
        messenger,
        deduplicator=deduplicator,
        max_message_age_seconds=max_message_age_seconds,
    )


def handle_message_payload(
    raw: dict[str, Any],
    reports: ReportService,
    messenger: FeishuMessenger,
    *,
    deduplicator: MessageDeduplicator | None = None,
    max_message_age_seconds: int = DEFAULT_MESSAGE_MAX_AGE_SECONDS,
) -> None:
    """Handles a decoded Feishu message payload.

    Args:
        raw: JSON-like event payload from Feishu.
        reports: Report builder used to answer commands.
        messenger: Feishu message sender.
    """

    logger.info(
        "Received im.message.receive_v1: %s",
        json.dumps(raw, ensure_ascii=False),
    )

    event = raw.get("event", {})
    message = event.get("message", {}) if isinstance(event, dict) else {}
    message_id = message.get("message_id")
    if _is_stale_message(raw, max_message_age_seconds):
        logger.info("Drop stale message event, message_id=%s", message_id)
        return

    if deduplicator and deduplicator.check_and_mark_seen(message_id):
        logger.info("Drop duplicate message event, message_id=%s", message_id)
        return

    chat_id = message.get("chat_id")
    content_raw = message.get("content", "{}")

    try:
        content = json.loads(content_raw)
    except (TypeError, json.JSONDecodeError):
        # Feishu text content is a JSON string; malformed content is treated as empty.
        content = {}

    text = str(content.get("text", "")).strip() if isinstance(content, dict) else ""
    logger.info("Message text=%s, chat_id=%s", text, chat_id)

    if not chat_id:
        logger.warning("No chat_id found in message event")
        return

    try:
        reply = build_reply_for_text(text, reports)
    except Exception:
        # Keep event handling resilient so one bad command does not stop the worker.
        logger.exception("Build reply failed")
        reply = BotReply.text("处理命令时出错，请稍后再试。")

    messenger.send_reply("chat_id", chat_id, reply)


def handle_menu_event(
    data: P2ApplicationBotMenuV6,
    reports: ReportService,
    messenger: FeishuMessenger,
) -> None:
    """Handles a typed Feishu bot menu event from the SDK dispatcher.

    Args:
        data: SDK event object for ``application.bot.menu_v6``.
        reports: Report builder used by menu handlers.
        messenger: Feishu message sender.
    """

    handle_menu_payload(_event_to_dict(data), reports, messenger)


def handle_menu_payload(
    raw: dict[str, Any],
    reports: ReportService,
    messenger: FeishuMessenger,
) -> None:
    """Handles a decoded Feishu bot menu payload.

    Args:
        raw: JSON-like event payload from Feishu.
        reports: Report builder used to answer menu actions.
        messenger: Feishu message sender.
    """

    logger.info(
        "Received application.bot.menu_v6: %s",
        json.dumps(raw, ensure_ascii=False),
    )

    event = raw.get("event", {})
    event_key = event.get("event_key") if isinstance(event, dict) else None
    operator = event.get("operator", {}) if isinstance(event, dict) else {}
    operator_id = operator.get("operator_id", {}) if isinstance(operator, dict) else {}
    open_id = operator_id.get("open_id")
    user_id = operator_id.get("user_id")

    logger.info(
        "Menu clicked, event_key=%s, open_id=%s, user_id=%s",
        event_key,
        open_id,
        user_id,
    )

    try:
        reply = build_reply_for_menu_event(event_key, reports)
    except Exception:
        # Menu replies are generated synchronously, so report failures as chat text.
        logger.exception("Build menu reply failed")
        reply = BotReply.text("处理菜单事件时出错，请稍后再试。")

    try:
        if open_id:
            # Prefer open_id when available because menu events are user-scoped.
            messenger.send_reply("open_id", open_id, reply)
        elif user_id:
            messenger.send_reply("user_id", user_id, reply)
        else:
            logger.warning(
                "No open_id or user_id found in menu event, cannot send message"
            )
    except Exception:
        logger.exception("Handle menu event failed")


def _event_to_dict(data: Any) -> dict[str, Any]:
    """Converts a Feishu SDK event object into a plain dictionary.

    Args:
        data: SDK event object accepted by ``lark.JSON.marshal``.

    Returns:
        JSON-compatible dictionary representation of the event.
    """

    return json.loads(lark.JSON.marshal(data))


def _is_stale_message(raw: dict[str, Any], max_age_seconds: int) -> bool:
    """Checks whether a Feishu message event is older than the accepted window."""

    create_time, source = _get_message_create_time(raw)
    if create_time is None:
        logger.warning("No create_time found in message event; process anyway")
        return False

    created_at = _parse_timestamp_seconds(create_time)
    if created_at is None:
        logger.warning(
            "Invalid %s in message event; process anyway",
            source,
        )
        return False

    age_seconds = time_module.time() - created_at
    return age_seconds > max_age_seconds


def _get_message_create_time(raw: dict[str, Any]) -> tuple[Any | None, str]:
    """Returns message create time, preferring ``event.message.create_time``."""

    event = raw.get("event", {})
    message = event.get("message", {}) if isinstance(event, dict) else {}
    if isinstance(message, dict) and message.get("create_time") not in (None, ""):
        return message.get("create_time"), "event.message.create_time"

    header = raw.get("header", {})
    if isinstance(header, dict) and header.get("create_time") not in (None, ""):
        return header.get("create_time"), "header.create_time"

    return None, "create_time"


def _parse_timestamp_seconds(value: Any) -> float | None:
    """Parses Feishu second or millisecond timestamps into Unix seconds."""

    try:
        timestamp = float(str(value).strip())
    except (TypeError, ValueError):
        return None

    # Feishu event timestamps are commonly millisecond strings.
    if timestamp >= 10_000_000_000:
        timestamp /= 1000
    return timestamp
