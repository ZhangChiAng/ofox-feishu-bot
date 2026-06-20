"""Runtime entrypoint for the Feishu websocket worker."""

from __future__ import annotations

import logging
import threading
import time as time_module
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import lark_oapi as lark
from lark_oapi import ws

from app.config import AppConfig, load_config
from app.feishu_client import FeishuMessenger, build_message_client
from app.handlers import MessageDeduplicator, handle_menu_event, handle_message_event
from app.ofox_client import OfoxClient
from app.report_rendering import PillowReportRenderer
from app.reports import ReportService
from app.repository import ModelRepository


logger = logging.getLogger(__name__)
WATCH_OPERATION_PROMPT = (
    "发现新增模型。可使用以下命令维护关注列表：\n"
    "watch add <模型名称>\n"
    "watch remove <模型名称>\n"
    "watch list\n"
    "watch clear"
)


def main() -> None:
    """Starts the Feishu websocket worker."""

    config = load_config()
    setup_logging(config.log_level)

    logger.info("Starting Feishu websocket worker")
    reports = ReportService(
        OfoxClient(config.ofox_models_api_url),
        ModelRepository(config.ofox_db_path),
        PillowReportRenderer(config.chinese_font_path),
    )
    messenger = FeishuMessenger(
        build_message_client(
            config.feishu_app_id,
            config.feishu_app_secret,
            log_level=to_lark_log_level(config.log_level),
        )
    )
    start_daily_report_thread(config, reports, messenger)

    # Wire dependencies once so callbacks stay small and side-effect free.
    event_handler = build_event_handler(
        reports,
        messenger,
        config.feishu_message_max_age_seconds,
    )
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
    max_message_age_seconds: int,
) -> lark.EventDispatcherHandler:
    """Builds the Feishu SDK event dispatcher.

    Args:
        reports: Report service captured by event callbacks.
        messenger: Feishu messenger captured by event callbacks.
        max_message_age_seconds: Maximum accepted age for message callbacks.

    Returns:
        Event dispatcher registered for message and menu callbacks.
    """

    deduplicator = MessageDeduplicator(max_message_age_seconds + 60)
    return (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(
            lambda data: handle_message_event(
                data,
                reports,
                messenger,
                deduplicator=deduplicator,
                max_message_age_seconds=max_message_age_seconds,
            )
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


def start_daily_report_thread(
    config: AppConfig,
    reports: ReportService,
    messenger: FeishuMessenger,
) -> threading.Thread | None:
    """Starts the proactive daily report thread when a target is configured."""

    if not config.feishu_report_receive_id_type or not config.feishu_report_receive_id:
        logger.info("Daily report target is not configured; proactive push disabled")
        return None

    thread = threading.Thread(
        target=daily_report_loop,
        args=(
            config.daily_report_time,
            config.daily_report_timezone,
            config.feishu_report_receive_id_type,
            config.feishu_report_receive_id,
            reports,
            messenger,
        ),
        daemon=True,
        name="daily-report",
    )
    thread.start()
    logger.info(
        "Daily report thread started for %s %s",
        config.daily_report_timezone.key,
        config.daily_report_time.strftime("%H:%M"),
    )
    return thread


def daily_report_loop(
    report_time: time,
    timezone: ZoneInfo,
    receive_id_type: str,
    receive_id: str,
    reports: ReportService,
    messenger: FeishuMessenger,
    *,
    stop_event: threading.Event | None = None,
) -> None:
    """Runs the daily proactive report loop."""

    stop_event = stop_event or threading.Event()
    while not stop_event.is_set():
        next_run = next_daily_run(datetime.now(timezone), report_time, timezone)
        sleep_seconds = max(0.0, (next_run - datetime.now(timezone)).total_seconds())
        logger.info("Next daily report check at %s", next_run.isoformat())
        if stop_event.wait(sleep_seconds):
            return

        try:
            send_daily_report_if_needed(
                reports,
                messenger,
                receive_id_type,
                receive_id,
            )
        except Exception:
            logger.exception("Daily report check failed")
            # Avoid a hot loop if a clock adjustment or repeated failure occurs.
            time_module.sleep(1)


def next_daily_run(
    now: datetime,
    report_time: time,
    timezone: ZoneInfo,
) -> datetime:
    """Calculates the next scheduled daily run in the configured timezone."""

    local_now = now.astimezone(timezone) if now.tzinfo else now.replace(tzinfo=timezone)
    candidate = datetime.combine(local_now.date(), report_time, tzinfo=timezone)
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return candidate


def send_daily_report_if_needed(
    reports: ReportService,
    messenger: FeishuMessenger,
    receive_id_type: str,
    receive_id: str,
) -> bool:
    """Sends the model report only when a sync detects new models.

    Returns:
        ``True`` when both the image report and text prompt were attempted,
        otherwise ``False``.
    """

    payload = reports.build_model_report_payload()
    if not payload.sync_result.new_models:
        logger.info("Daily report found no new models; skip proactive push")
        return False

    messenger.send_reply(receive_id_type, receive_id, payload.reply)
    messenger.send_text(receive_id_type, receive_id, WATCH_OPERATION_PROMPT)
    return True


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
