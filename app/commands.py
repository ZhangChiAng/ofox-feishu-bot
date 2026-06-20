"""Command parsing and reply dispatch for the Feishu bot."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.replies import BotReply
from app.reports import ReportService, WATCH_COMMAND_HELP


MENU_EVENT_HELP = "help"
MENU_EVENT_LIST_PROVIDERS = "list_providers"
MENU_EVENT_SEND_REPORT = "send_report"
SUPPORTED_MENU_EVENTS = {
    MENU_EVENT_HELP,
    MENU_EVENT_LIST_PROVIDERS,
    MENU_EVENT_SEND_REPORT,
}

HELP_TEXT = (
    "可用命令：\n"
    "1. provider <提供商>\n"
    "2. watch add <模型名称>\n"
    "3. watch remove <模型名称>\n"
    "4. watch list\n"
    "5. watch clear\n\n"
)


class CommandKind(StrEnum):
    """Supported command categories."""

    HELP = "help"
    MODEL_REPORT = "model_report"
    LIST_PROVIDERS = "list_providers"
    PROVIDER_MODELS = "provider_models"
    WATCH_ADD = "watch_add"
    WATCH_REMOVE = "watch_remove"
    WATCH_LIST = "watch_list"
    WATCH_CLEAR = "watch_clear"
    WATCH_HELP = "watch_help"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BotCommand:
    """Parsed bot command.

    Attributes:
        kind: Command category.
        raw_text: Original user text, when parsed from a message.
        provider: Provider argument for provider model reports.
        model_name: Model name argument for watch commands.
        menu_event_key: Original menu event key, when parsed from a menu click.
    """

    kind: CommandKind
    raw_text: str = ""
    provider: str = ""
    model_name: str = ""
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
    if command.kind is CommandKind.WATCH_ADD:
        return reports.add_watched_model(command.model_name)
    if command.kind is CommandKind.WATCH_REMOVE:
        return reports.remove_watched_model(command.model_name)
    if command.kind is CommandKind.WATCH_LIST:
        return reports.build_watched_models_report()
    if command.kind is CommandKind.WATCH_CLEAR:
        return reports.clear_watched_models()
    if command.kind is CommandKind.WATCH_HELP:
        return BotReply.text(WATCH_COMMAND_HELP)
    if not command.raw_text:
        # Empty messages should be helpful rather than reported as unknown commands.
        return BotReply.text(HELP_TEXT)
    return BotReply.text(
        f"未知命令：{command.raw_text}\n"
        "支持的文本命令：\n"
        "1. provider <提供商>\n"
        "2. watch add <模型名称>\n"
        "3. watch remove <模型名称>\n"
        "4. watch list\n"
        "5. watch clear\n\n"
    )


def parse_text_command(text: str) -> BotCommand:
    """Parses a user text message into a bot command.

    Args:
        text: User message text.

    Returns:
        Parsed command.
    """

    text = (text or "").strip()

    watch_command = parse_watch_command(text)
    if watch_command:
        return watch_command

    provider = parse_provider_query(text)
    if provider:
        return BotCommand(
            CommandKind.PROVIDER_MODELS,
            raw_text=text,
            provider=provider,
        )

    return BotCommand(CommandKind.UNKNOWN, raw_text=text)


def parse_watch_command(text: str) -> BotCommand | None:
    """Parses ``watch`` text commands.

    Args:
        text: User message text.

    Returns:
        Parsed watch command, ``None`` when the text does not start with
        ``watch``.
    """

    text = (text or "").strip()
    if text != "watch" and not text.startswith("watch "):
        return None

    parts = text.split(maxsplit=2)
    if len(parts) == 1:
        return BotCommand(CommandKind.WATCH_HELP, raw_text=text)

    action = parts[1]
    if action == "list" and len(parts) == 2:
        return BotCommand(CommandKind.WATCH_LIST, raw_text=text)
    if action == "clear" and len(parts) == 2:
        return BotCommand(CommandKind.WATCH_CLEAR, raw_text=text)
    if action == "add":
        model_name = parts[2].strip() if len(parts) == 3 else ""
        return BotCommand(
            CommandKind.WATCH_ADD,
            raw_text=text,
            model_name=model_name,
        )
    if action == "remove":
        model_name = parts[2].strip() if len(parts) == 3 else ""
        return BotCommand(
            CommandKind.WATCH_REMOVE,
            raw_text=text,
            model_name=model_name,
        )

    return BotCommand(CommandKind.WATCH_HELP, raw_text=text)


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
