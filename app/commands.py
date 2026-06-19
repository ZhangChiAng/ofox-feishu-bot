"""Command parsing and reply dispatch for the Feishu bot."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.replies import BotReply
from app.reports import ReportService


MENU_EVENT_HELP = "help"
MENU_EVENT_LIST_PROVIDERS = "list_providers"
MENU_EVENT_SEND_REPORT = "send_report"
SUPPORTED_MENU_EVENTS = {
    MENU_EVENT_HELP,
    MENU_EVENT_LIST_PROVIDERS,
    MENU_EVENT_SEND_REPORT,
}

HELP_TEXT = "可用命令：\n1. provider <提供商>\n\n示例：provider openai"


class CommandKind(StrEnum):
    """Supported command categories."""

    HELP = "help"
    MODEL_REPORT = "model_report"
    LIST_PROVIDERS = "list_providers"
    PROVIDER_MODELS = "provider_models"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BotCommand:
    """Parsed bot command.

    Attributes:
        kind: Command category.
        raw_text: Original user text, when parsed from a message.
        provider: Provider argument for provider model reports.
        menu_event_key: Original menu event key, when parsed from a menu click.
    """

    kind: CommandKind
    raw_text: str = ""
    provider: str = ""
    menu_event_key: str = ""


def build_reply_for_text(text: str, reports: ReportService) -> BotReply:
    """Builds a reply for a raw text message.

    Args:
        text: User message text.
        reports: Report service used to generate command output.

    Returns:
        Reply payload for Feishu.
    """

    return build_reply_for_command(parse_text_command(text), reports)


def build_reply_for_menu_event(
    event_key: str | None,
    reports: ReportService,
) -> BotReply:
    """Builds a reply for a Feishu menu event key.

    Args:
        event_key: Menu event key from Feishu.
        reports: Report service used to generate command output.

    Returns:
        Reply payload for Feishu.
    """

    command = parse_menu_event(event_key)
    if command.kind is CommandKind.UNKNOWN:
        return BotReply.text(f"已收到未知菜单事件：{command.menu_event_key}")
    return build_reply_for_command(command, reports)


def build_reply_for_command(command: BotCommand, reports: ReportService) -> BotReply:
    """Dispatches a parsed command to the matching reply builder.

    Args:
        command: Parsed command.
        reports: Report service used to generate command output.

    Returns:
        Reply payload for Feishu.
    """

    if command.kind is CommandKind.HELP:
        return BotReply.text(HELP_TEXT)
    if command.kind is CommandKind.MODEL_REPORT:
        return reports.build_model_report()
    if command.kind is CommandKind.LIST_PROVIDERS:
        return reports.build_provider_report()
    if command.kind is CommandKind.PROVIDER_MODELS:
        return reports.build_provider_models_report(command.provider)
    if not command.raw_text:
        # Empty messages should be helpful rather than reported as unknown commands.
        return BotReply.text(HELP_TEXT)
    return BotReply.text(
        f"未知命令：{command.raw_text}\n支持的文本命令：provider <提供商>"
    )


def parse_text_command(text: str) -> BotCommand:
    """Parses a user text message into a bot command.

    Args:
        text: User message text.

    Returns:
        Parsed command.
    """

    text = (text or "").strip()

    provider = parse_provider_query(text)
    if provider:
        return BotCommand(
            CommandKind.PROVIDER_MODELS,
            raw_text=text,
            provider=provider,
        )

    return BotCommand(CommandKind.UNKNOWN, raw_text=text)


def parse_menu_event(event_key: str | None) -> BotCommand:
    """Parses a Feishu bot menu event key into a command.

    Args:
        event_key: Raw menu event key.

    Returns:
        Parsed command.
    """

    event_key = (event_key or "").strip()
    if event_key == MENU_EVENT_HELP:
        return BotCommand(CommandKind.HELP, menu_event_key=event_key)
    if event_key == MENU_EVENT_LIST_PROVIDERS:
        return BotCommand(CommandKind.LIST_PROVIDERS, menu_event_key=event_key)
    if event_key == MENU_EVENT_SEND_REPORT:
        return BotCommand(CommandKind.MODEL_REPORT, menu_event_key=event_key)
    return BotCommand(CommandKind.UNKNOWN, menu_event_key=event_key)


def parse_provider_query(text: str) -> str | None:
    """Parses the ``provider <provider>`` text command.

    Args:
        text: User message text.

    Returns:
        Provider name when the text matches, otherwise ``None``.
    """

    text = (text or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) != 2 or parts[0] != "provider":
        return None

    # Preserve provider spelling after the command keyword; reports normalize lookup.
    provider = parts[1].strip()
    return provider or None
