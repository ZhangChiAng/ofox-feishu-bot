"""Runtime entrypoint for the Feishu websocket worker."""

from __future__ import annotations

import logging

import lark_oapi as lark
from lark_oapi import ws

from app.config import AppConfig, load_config
from app.feishu_client import FeishuMessenger, build_message_client
from app.handlers import handle_menu_event, handle_message_event
from app.ofox_client import OfoxClient
from app.reports import ReportService
from app.repository import ModelRepository


logger = logging.getLogger(__name__)


def main() -> None:
    """Starts the Feishu websocket worker."""

    config = load_config()
    setup_logging(config.log_level)

    logger.info("Starting Feishu websocket worker")
    reports = ReportService(
        OfoxClient(config.ofox_models_api_url),
        ModelRepository(config.ofox_db_path),
    )
    messenger = FeishuMessenger(
        build_message_client(
            config.feishu_app_id,
            config.feishu_app_secret,
            log_level=to_lark_log_level(config.log_level),
        )
    )

    # Wire dependencies once so callbacks stay small and side-effect free.
    event_handler = build_event_handler(reports, messenger)
    cli = build_ws_client(config, event_handler)
    cli.start()


def setup_logging(level: str) -> None:
    """Configures process logging.

    Args:
        level: Python logging level name.
    """

    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def build_event_handler(
    reports: ReportService,
    messenger: FeishuMessenger,
) -> lark.EventDispatcherHandler:
    """Builds the Feishu SDK event dispatcher.

    Args:
        reports: Report service captured by event callbacks.
        messenger: Feishu messenger captured by event callbacks.

    Returns:
        Event dispatcher registered for message and menu callbacks.
    """

    return (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(
            lambda data: handle_message_event(data, reports, messenger)
        )
        .register_p2_application_bot_menu_v6(
            lambda data: handle_menu_event(data, reports, messenger)
        )
        .build()
    )


def build_ws_client(
    config: AppConfig,
    event_handler: lark.EventDispatcherHandler,
) -> ws.Client:
    """Builds the Feishu websocket client.

    Args:
        config: Runtime configuration.
        event_handler: Feishu event dispatcher.

    Returns:
        Configured websocket client.
    """

    return ws.Client(
        config.feishu_app_id,
        config.feishu_app_secret,
        event_handler=event_handler,
        log_level=to_lark_log_level(config.log_level),
    )


def to_lark_log_level(level: str) -> lark.LogLevel:
    """Maps a Python log level name to the Feishu SDK log enum.

    Args:
        level: Python logging level name.

    Returns:
        Matching Feishu log level, defaulting to ``INFO``.
    """

    return getattr(lark.LogLevel, level.upper(), lark.LogLevel.INFO)


if __name__ == "__main__":
    main()
