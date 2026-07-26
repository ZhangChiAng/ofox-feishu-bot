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
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTrigger,
    P2CardActionTriggerResponse,
)

from app.commands import build_reply_for_menu_event, build_reply_for_text
from app.feishu_client import FeishuMessenger
from app.replies import BotReply
from app.reports import ReportService
from app.watch_cards import CardActionResult, WatchCardService


logger = logging.getLogger(__name__)
DEFAULT_MESSAGE_MAX_AGE_SECONDS = 120
MESSAGE_RECEIVED_REACTION = "Typing"
MENU_RECEIVED_ACKNOWLEDGMENT = "⌨️ 正在处理…"


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
    message_type = message.get("message_type")
    chat_type = message.get("chat_type")
    if message_type != "text" or chat_type != "p2p":
        logger.info(
            "Ignore unsupported message event, message_id=%s, message_type=%s, "
            "chat_type=%s",
            message_id,
            message_type,
            chat_type,
        )
        return

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

    if message_id:
        try:
            messenger.add_reaction(message_id, MESSAGE_RECEIVED_REACTION)
        except Exception:
            # Acknowledgment is best-effort; its failure must not skip the command.
            logger.exception("Add message acknowledgment reaction failed")

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
    watch_cards: WatchCardService,
    messenger: FeishuMessenger,
    *,
    deduplicator: MessageDeduplicator | None = None,
    max_message_age_seconds: int = DEFAULT_MESSAGE_MAX_AGE_SECONDS,
) -> None:
    """Handles a typed Feishu bot menu event from the SDK dispatcher.

    Args:
        data: SDK event object for ``application.bot.menu_v6``.
        reports: Report builder used by menu handlers.
        watch_cards: Interactive watch-card service used by the menu.
        messenger: Feishu message sender.
    """

    handle_menu_payload(
        _event_to_dict(data),
        reports,
        watch_cards,
        messenger,
        deduplicator=deduplicator,
        max_message_age_seconds=max_message_age_seconds,
    )


def handle_menu_payload(
    raw: dict[str, Any],
    reports: ReportService,
    watch_cards: WatchCardService,
    messenger: FeishuMessenger,
    *,
    deduplicator: MessageDeduplicator | None = None,
    max_message_age_seconds: int = DEFAULT_MESSAGE_MAX_AGE_SECONDS,
) -> None:
    """Handles a decoded Feishu bot menu payload.

    Args:
        raw: JSON-like event payload from Feishu.
        reports: Report builder used to answer menu actions.
        watch_cards: Interactive watch-card service used by the menu.
        messenger: Feishu message sender.
    """

    logger.info(
        "Received application.bot.menu_v6: %s",
        json.dumps(raw, ensure_ascii=False),
    )

    event_id = _get_header_event_id(raw)
    event = raw.get("event", {})
    event_key = event.get("event_key") if isinstance(event, dict) else None
    operator = event.get("operator", {}) if isinstance(event, dict) else {}
    operator_id = operator.get("operator_id", {}) if isinstance(operator, dict) else {}
    open_id = operator_id.get("open_id")
    user_id = operator_id.get("user_id")

    if _is_stale_menu(raw, max_message_age_seconds):
        logger.info(
            "Drop stale menu event, event_id=%s, event_key=%s",
            event_id,
            event_key,
        )
        return

    if deduplicator and deduplicator.check_and_mark_seen(event_id):
        logger.info(
            "Drop duplicate menu event, event_id=%s, event_key=%s",
            event_id,
            event_key,
        )
        return

    logger.info(
        "Menu clicked, event_key=%s, open_id=%s, user_id=%s",
        event_key,
        open_id,
        user_id,
    )

    if open_id:
        # Menu events are user-scoped, so prefer the stable application open_id.
        receive_id_type = "open_id"
        receive_id = open_id
    elif user_id:
        receive_id_type = "user_id"
        receive_id = user_id
    else:
        logger.warning("No open_id or user_id found in menu event, cannot send message")
        return

    try:
        messenger.send_reply(
            receive_id_type,
            receive_id,
            BotReply.text(MENU_RECEIVED_ACKNOWLEDGMENT),
        )
    except Exception:
        # Acknowledgment is best-effort; its failure must not skip the menu command.
        logger.exception("Send menu acknowledgment failed")

    try:
        reply = build_reply_for_menu_event(event_key, reports, watch_cards)
    except Exception:
        # Menu replies are generated synchronously, so report failures as chat text.
        logger.exception("Build menu reply failed")
        reply = BotReply.text("处理菜单事件时出错，请稍后再试。")

    try:
        messenger.send_reply(receive_id_type, receive_id, reply)
    except Exception:
        logger.exception("Handle menu event failed")


def handle_card_action_event(
    data: P2CardActionTrigger,
    watch_cards: WatchCardService,
    *,
    deduplicator: MessageDeduplicator | None = None,
) -> P2CardActionTriggerResponse:
    """Handles a typed ``card.action.trigger`` callback."""

    return handle_card_action_payload(
        _event_to_dict(data),
        watch_cards,
        deduplicator=deduplicator,
    )


def handle_card_action_payload(
    raw: dict[str, Any],
    watch_cards: WatchCardService,
    *,
    deduplicator: MessageDeduplicator | None = None,
) -> P2CardActionTriggerResponse:
    """Handles a decoded card action and returns a raw replacement card."""

    logger.info(
        "Received card.action.trigger: %s",
        json.dumps(raw, ensure_ascii=False),
    )
    event_id = _get_header_event_id(raw)
    event = raw.get("event", {})
    action = event.get("action", {}) if isinstance(event, dict) else {}
    value = action.get("value") if isinstance(action, dict) else None
    form_value = action.get("form_value") if isinstance(action, dict) else None

    try:
        if deduplicator and deduplicator.check_and_mark_seen(event_id):
            result = watch_cards.render_duplicate_action(
                value,
                form_value=form_value,
            )
        else:
            result = watch_cards.handle_action(
                value,
                form_value=form_value,
            )
    except Exception:
        # Card callbacks have a short deadline, so always return a renderable result.
        logger.exception("Handle card action failed, event_id=%s", event_id)
        result = watch_cards.handle_action(None)

    return _card_action_response(result)


def _card_action_response(result: CardActionResult) -> P2CardActionTriggerResponse:
    """Converts an internal card result to the SDK callback response."""

    response: dict[str, Any] = {
        "card": {
            "type": "raw",
            "data": result.card,
        }
    }
    if result.toast_type is not None and result.toast is not None:
        response["toast"] = {
            "type": result.toast_type,
            "content": result.toast,
        }
    return P2CardActionTriggerResponse(response)


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


def _is_stale_menu(raw: dict[str, Any], max_age_seconds: int) -> bool:
    """Checks whether a Feishu menu event is older than the accepted window."""

    create_time, source = _get_menu_create_time(raw)
    if create_time is None:
        logger.warning("No create_time found in menu event; process anyway")
        return False

    created_at = _parse_timestamp_seconds(create_time)
    if created_at is None:
        logger.warning(
            "Invalid %s in menu event; process anyway",
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


def _get_menu_create_time(raw: dict[str, Any]) -> tuple[Any | None, str]:
    """Returns menu event create time, preferring ``event.timestamp``."""

    event = raw.get("event", {})
    if isinstance(event, dict) and event.get("timestamp") not in (None, ""):
        return event.get("timestamp"), "event.timestamp"

    header = raw.get("header", {})
    if isinstance(header, dict) and header.get("create_time") not in (None, ""):
        return header.get("create_time"), "header.create_time"

    return None, "create_time"


def _get_header_event_id(raw: dict[str, Any]) -> str | None:
    """Returns the Feishu header event id when present."""

    header = raw.get("header", {})
    if not isinstance(header, dict):
        return None

    event_id = header.get("event_id")
    return str(event_id).strip() if event_id not in (None, "") else None


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
