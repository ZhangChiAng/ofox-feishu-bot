from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import load_config
from app.replies import BotReply
from app.reports import ModelReportPayload
from app.repository import SyncResult
from app.worker import (
    WATCH_OPERATION_PROMPT,
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
                provider_counts={"openai": 10},
                new_models=new_models,
                baseline_created=False,
                checked_at="2026-01-01T00:00:00+00:00",
            ),
        )


class FakeMessenger:
    def __init__(self) -> None:
        self.replies: list[tuple[str, str, BotReply]] = []
        self.texts: list[tuple[str, str, str]] = []

    def send_reply(
        self,
        receive_id_type: str,
        receive_id: str,
        reply: BotReply,
    ) -> bool:
        self.replies.append((receive_id_type, receive_id, reply))
        return True

    def send_text(self, receive_id_type: str, receive_id: str, text: str) -> bool:
        self.texts.append((receive_id_type, receive_id, text))
        return True


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
        time(12, 30),
        timezone,
    )
    after = next_daily_run(
        datetime(2026, 1, 1, 12, 31, tzinfo=timezone),
        time(12, 30),
        timezone,
    )

    assert before == datetime(2026, 1, 1, 12, 30, tzinfo=timezone)
    assert after == datetime(2026, 1, 2, 12, 30, tzinfo=timezone)


def test_daily_report_skips_when_no_new_models() -> None:
    reports = FakeReports(new_count=0)
    messenger = FakeMessenger()

    sent = send_daily_report_if_needed(reports, messenger, "chat_id", "chat-id")

    assert sent is False
    assert reports.calls == 1
    assert messenger.replies == []
    assert messenger.texts == []


def test_daily_report_sends_image_and_watch_prompt_for_new_models() -> None:
    reports = FakeReports(new_count=2)
    messenger = FakeMessenger()

    sent = send_daily_report_if_needed(reports, messenger, "chat_id", "chat-id")

    assert sent is True
    assert messenger.replies == [
        ("chat_id", "chat-id", BotReply.image(b"daily report"))
    ]
    assert messenger.texts == [("chat_id", "chat-id", WATCH_OPERATION_PROMPT)]


def test_daily_report_thread_is_not_started_without_target(tmp_path: Path) -> None:
    config = load_config(environ=base_env(tmp_path))

    thread = start_daily_report_thread(config, FakeReports(0), FakeMessenger())

    assert thread is None
