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
from app.handlers import (
    EventDeduplicator,
    handle_card_action_event,
    handle_menu_event,
)
from app.ofox_client import OfoxClient
from app.report_rendering import PillowReportRenderer
from app.reports import ReportService
from app.repository import ModelRepository
from app.watch_cards import WatchCardService


logger = logging.getLogger(__name__)


def main() -> None:
    """Starts the Feishu websocket worker."""

    config = load_config()
    setup_logging(config.log_level)

    logger.info("Starting Feishu websocket worker")
    source = OfoxClient(config.ofox_models_api_url)
    repository = ModelRepository(config.ofox_db_path)
    reports = ReportService(
        source,
        repository,
        PillowReportRenderer(config.chinese_font_path),
    )
    watch_cards = WatchCardService(source, repository)
    messenger = FeishuMessenger(
        build_message_client(
            config.feishu_app_id,
            config.feishu_app_secret,
            log_level=to_lark_log_level(config.log_level),
        )
    )
    start_daily_report_thread(config, reports, watch_cards, messenger)

    # Wire dependencies once so websocket callbacks stay small and synchronous.
    event_handler = build_event_handler(
        reports,
        watch_cards,
        messenger,
        config.feishu_event_max_age_seconds,
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
    watch_cards: WatchCardService,
    messenger: FeishuMessenger,
    max_event_age_seconds: int,
) -> lark.EventDispatcherHandler:
    """Builds the Feishu SDK event dispatcher.

    Args:
        reports: Report service captured by event callbacks.
        watch_cards: Interactive watch-card service captured by callbacks.
        messenger: Feishu messenger captured by event callbacks.
        max_event_age_seconds: Maximum accepted age for menu event callbacks.

    Returns:
        Event dispatcher registered for menu and card-action callbacks.
    """

    deduplicator = EventDeduplicator(max_event_age_seconds + 60)
    return (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_application_bot_menu_v6(
            lambda data: handle_menu_event(
                data,
                reports,
                watch_cards,
                messenger,
                deduplicator=deduplicator,
                max_event_age_seconds=max_event_age_seconds,
            )
        )
        .register_p2_card_action_trigger(
            lambda data: handle_card_action_event(
                data,
                watch_cards,
                deduplicator=deduplicator,
            )
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
    watch_cards: WatchCardService,
    messenger: FeishuMessenger,
) -> threading.Thread | None:
    """Starts the proactive daily report thread when a target is configured."""

    if not config.feishu_report_receive_id_type or not config.feishu_report_receive_id:
        logger.info("Daily report target is not configured; proactive push disabled")
        return None

    thread = threading.Thread(
        target=daily_report_loop,
        args=(
            config.daily_report_times,
            config.daily_report_timezone,
            config.feishu_report_receive_id_type,
            config.feishu_report_receive_id,
            reports,
            watch_cards,
            messenger,
        ),
        daemon=True,
        name="daily-report",
    )
    thread.start()
    logger.info(
        "Daily report thread started for %s at %s",
        config.daily_report_timezone.key,
        ", ".join(t.strftime("%H:%M") for t in config.daily_report_times),
    )
    return thread


def daily_report_loop(
    report_times: list[time],
    timezone: ZoneInfo,
    receive_id_type: str,
    receive_id: str,
    reports: ReportService,
    watch_cards: WatchCardService,
    messenger: FeishuMessenger,
    *,
    stop_event: threading.Event | None = None,
) -> None:
    """Runs the daily proactive report loop."""

    stop_event = stop_event or threading.Event()
    while not stop_event.is_set():
        next_run = next_daily_run(datetime.now(timezone), report_times, timezone)
        sleep_seconds = max(0.0, (next_run - datetime.now(timezone)).total_seconds())
        logger.info("Next daily report check at %s", next_run.isoformat())
        if stop_event.wait(sleep_seconds):
            return

        try:
            send_daily_report_if_needed(
                reports,
                watch_cards,
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
    report_times: list[time],
    timezone: ZoneInfo,
) -> datetime:
    """Calculates the next scheduled daily run in the configured timezone."""

    local_now = now.astimezone(timezone) if now.tzinfo else now.replace(tzinfo=timezone)
    candidates = []
    for report_time in report_times:
        candidate = datetime.combine(local_now.date(), report_time, tzinfo=timezone)
        if candidate <= local_now:
            # This slot already passed today; roll it to the same time tomorrow.
            candidate += timedelta(days=1)
        candidates.append(candidate)
    return min(candidates)


def send_daily_report_if_needed(
    reports: ReportService,
    watch_cards: WatchCardService,
    messenger: FeishuMessenger,
    receive_id_type: str,
    receive_id: str,
) -> bool:
    """Sends the model report only when a sync detects new models.

    Returns:
        ``True`` when both the image report and quick-action card were attempted,
        otherwise ``False``.
    """

    payload = reports.build_model_report_payload()
    if not payload.sync_result.new_models:
        logger.info("Daily report found no new models; skip proactive push")
        return False

    messenger.send_reply(receive_id_type, receive_id, payload.reply)
    messenger.send_reply(
        receive_id_type,
        receive_id,
        watch_cards.build_new_models_card(payload.sync_result.new_models),
    )
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
