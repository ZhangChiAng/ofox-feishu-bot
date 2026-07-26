"""Menu event parsing and reply dispatch for the Feishu bot."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.replies import BotReply
from app.reports import ReportService


MENU_EVENT_SEND_REPORT = "send_report"
MENU_EVENT_MANAGE_WATCHES = "manage_watches"
SUPPORTED_MENU_EVENTS = {
    MENU_EVENT_SEND_REPORT,
    MENU_EVENT_MANAGE_WATCHES,
}


class WatchCardReplyBuilder(Protocol):
    """Protocol for opening the interactive watch management card."""

    def open_management_card(self) -> BotReply:
        """Refreshes the catalog and builds the management card."""

        ...


class CommandKind(StrEnum):
    """Supported menu command categories."""

    MODEL_REPORT = "model_report"
    MANAGE_WATCHES = "manage_watches"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BotCommand:
    """Parsed menu command.

    Attributes:
        kind: Command category.
        menu_event_key: Original menu event key.
    """

    kind: CommandKind
    menu_event_key: str = ""


def build_reply_for_menu_event(
    event_key: str | None,
    reports: ReportService,
    watch_cards: WatchCardReplyBuilder,
) -> BotReply:
    """Builds a reply for a Feishu menu event key."""

    command = parse_menu_event(event_key)
    if command.kind is CommandKind.UNKNOWN:
        return BotReply.text(f"已收到未知菜单事件：{command.menu_event_key}")
    if command.kind is CommandKind.MANAGE_WATCHES:
        return watch_cards.open_management_card()
    return reports.build_model_report()


def parse_menu_event(event_key: str | None) -> BotCommand:
    """Parses a Feishu bot menu event key into a command."""

    event_key = (event_key or "").strip()
    if event_key == MENU_EVENT_SEND_REPORT:
        return BotCommand(CommandKind.MODEL_REPORT, menu_event_key=event_key)
    if event_key == MENU_EVENT_MANAGE_WATCHES:
        return BotCommand(CommandKind.MANAGE_WATCHES, menu_event_key=event_key)
    return BotCommand(CommandKind.UNKNOWN, menu_event_key=event_key)
