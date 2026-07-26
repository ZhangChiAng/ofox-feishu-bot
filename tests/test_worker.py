from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import load_config
from app.replies import BotReply
from app.reports import ModelReportPayload
from app.repository import SyncResult
from app.worker import (
    build_event_handler,
    next_daily_run,
    send_daily_report_if_needed,
    start_daily_report_thread,
)

from tests.helpers import model


class FakeReports:
    def __init__(self, new_count: int) -> None:
        self.new_count = new_count
        self.calls = 0

    def build_model_report_payload(self) -> ModelReportPayload:
        self.calls += 1
        new_models = [
            model(f"openai/gpt-4.{index}", released_at=index)
            for index in range(self.new_count)
        ]
        return ModelReportPayload(
            BotReply.image(b"daily report"),
            SyncResult(
                total_count=10,
                new_models=new_models,
                baseline_created=False,
                checked_at="2026-01-01T00:00:00+00:00",
            ),
        )


class FakeMessenger:
    def __init__(self) -> None:
        self.replies: list[tuple[str, str, BotReply]] = []

    def send_reply(
        self,
        receive_id_type: str,
        receive_id: str,
        reply: BotReply,
    ) -> bool:
        self.replies.append((receive_id_type, receive_id, reply))
        return True


class FakeWatchCards:
    def build_new_models_card(self, models) -> BotReply:
        return BotReply.interactive(
            {
                "schema": "2.0",
                "body": {
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": f"{len(models)} new models",
                        }
                    ]
                },
            }
        )

    def open_management_card(self) -> BotReply:
        return BotReply.interactive({"schema": "2.0", "body": {"elements": []}})


def base_env(tmp_path: Path) -> dict[str, str]:
    font_path = tmp_path / "report.ttf"
    font_path.write_bytes(b"font")
    return {
        "FEISHU_APP_ID": "test_app_id",
        "FEISHU_APP_SECRET": "test_secret",
        "CHINESE_FONT_PATH": str(font_path),
    }


def test_next_daily_run_uses_configured_timezone() -> None:
    timezone = ZoneInfo("Asia/Shanghai")

    before = next_daily_run(
        datetime(2026, 1, 1, 12, 0, tzinfo=timezone),
        [time(12, 30)],
        timezone,
    )
    after = next_daily_run(
        datetime(2026, 1, 1, 12, 31, tzinfo=timezone),
        [time(12, 30)],
        timezone,
    )

    assert before == datetime(2026, 1, 1, 12, 30, tzinfo=timezone)
    assert after == datetime(2026, 1, 2, 12, 30, tzinfo=timezone)


def test_next_daily_run_picks_soonest_slot() -> None:
    timezone = ZoneInfo("Asia/Shanghai")

    # Past one slot today, pick next slot later today, not tomorrow.
    next_run = next_daily_run(
        datetime(2026, 1, 1, 10, 0, tzinfo=timezone),
        [time(9, 30), time(14, 0)],
        timezone,
    )

    assert next_run == datetime(2026, 1, 1, 14, 0, tzinfo=timezone)


def test_next_daily_run_rolls_to_next_day_when_all_slots_passed() -> None:
    timezone = ZoneInfo("Asia/Shanghai")

    next_run = next_daily_run(
        datetime(2026, 1, 1, 20, 0, tzinfo=timezone),
        [time(9, 30), time(14, 0)],
        timezone,
    )

    assert next_run == datetime(2026, 1, 2, 9, 30, tzinfo=timezone)


def test_daily_report_skips_when_no_new_models() -> None:
    reports = FakeReports(new_count=0)
    messenger = FakeMessenger()

    sent = send_daily_report_if_needed(
        reports,
        FakeWatchCards(),
        messenger,
        "chat_id",
        "chat-id",
    )

    assert sent is False
    assert reports.calls == 1
    assert messenger.replies == []


def test_daily_report_sends_image_and_quick_card_for_new_models() -> None:
    reports = FakeReports(new_count=2)
    messenger = FakeMessenger()

    sent = send_daily_report_if_needed(
        reports,
        FakeWatchCards(),
        messenger,
        "chat_id",
        "chat-id",
    )

    assert sent is True
    assert messenger.replies == [
        ("chat_id", "chat-id", BotReply.image(b"daily report")),
        (
            "chat_id",
            "chat-id",
            BotReply.interactive(
                {
                    "schema": "2.0",
                    "body": {
                        "elements": [
                            {
                                "tag": "markdown",
                                "content": "2 new models",
                            }
                        ]
                    },
                }
            ),
        ),
    ]


def test_daily_report_thread_is_not_started_without_target(tmp_path: Path) -> None:
    config = load_config(environ=base_env(tmp_path))

    thread = start_daily_report_thread(
        config,
        FakeReports(0),
        FakeWatchCards(),
        FakeMessenger(),
    )

    assert thread is None


def test_event_dispatcher_registers_only_menu_and_card_action_callbacks() -> None:
    handler = build_event_handler(
        FakeReports(0),
        FakeWatchCards(),
        FakeMessenger(),
        120,
    )

    assert set(handler._processorMap) == {"p2.application.bot.menu_v6"}
    assert set(handler._callback_processor_map) == {"p2.card.action.trigger"}
