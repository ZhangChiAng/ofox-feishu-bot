"""Feishu event handlers that translate callbacks into bot replies."""

from __future__ import annotations

import json
import logging
from typing import Any

import lark_oapi as lark
from lark_oapi.api.application.v6 import P2ApplicationBotMenuV6
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

from app.commands import build_reply_for_menu_event, build_reply_for_text
from app.feishu_client import FeishuMessenger
from app.replies import BotReply
from app.reports import ReportService


logger = logging.getLogger(__name__)


def handle_message_event(
    data: P2ImMessageReceiveV1,
    reports: ReportService,
    messenger: FeishuMessenger,
) -> None:
    """Handles a typed Feishu message event from the SDK dispatcher.

    Args:
        data: SDK event object for ``im.message.receive_v1``.
        reports: Report builder used by command handlers.
        messenger: Feishu message sender.
    """

    handle_message_payload(_event_to_dict(data), reports, messenger)


def handle_message_payload(
    raw: dict[str, Any],
    reports: ReportService,
    messenger: FeishuMessenger,
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
