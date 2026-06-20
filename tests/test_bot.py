import importlib
import json
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from app.commands import (
    CommandKind,
    build_reply_for_menu_event,
    build_reply_for_text,
    parse_menu_event,
    parse_provider_query,
    parse_text_command,
)
from app.handlers import (
    MessageDeduplicator,
    handle_menu_payload,
    handle_message_payload,
)
from app.models import OfoxModel
from app.replies import BotReply
from app.report_rendering import (
    PillowReportRenderer,
    ReportDocument,
    TableBlock,
    TextBlock,
)
from app.reports import ReportService, format_released_at, format_time
from app.repository import ModelRepository

from tests.helpers import model


class FakeOfoxClient:
    def __init__(self, models: list[OfoxModel]) -> None:
        self.models = models

    def fetch_models(self) -> list[OfoxModel]:
        return list(self.models)


class FakeRenderer:
    def __init__(self) -> None:
        self.documents: list[ReportDocument] = []

    def render(self, document: ReportDocument) -> bytes:
        self.documents.append(document)
        return f"png-{len(self.documents)}".encode()


def service(
    tmp_path: Path, models: list[OfoxModel]
) -> tuple[ReportService, FakeOfoxClient, FakeRenderer]:
    client = FakeOfoxClient(models)
    renderer = FakeRenderer()
    return (
        ReportService(client, ModelRepository(tmp_path / "models.sqlite3"), renderer),
        client,
        renderer,
    )


def assert_image_reply(reply: BotReply, expected_image: bytes) -> None:
    assert reply.msg_type == "image"
    assert reply.content == {"image": expected_image}


def document_text(document: ReportDocument) -> str:
    parts = [document.title]
    for block in document.blocks:
        parts.append(block.title)
        if isinstance(block, TableBlock):
            parts.extend(block.headers)
            for row in block.rows:
                parts.extend(row)
            if block.note:
                parts.append(block.note)
        elif isinstance(block, TextBlock):
            parts.extend(block.lines)
    return "\n".join(parts)


def table_titles(document: ReportDocument) -> list[str]:
    return [block.title for block in document.blocks if isinstance(block, TableBlock)]


def table_by_title(document: ReportDocument, title: str) -> TableBlock:
    for block in document.blocks:
        if isinstance(block, TableBlock) and block.title == title:
            return block
    raise AssertionError(f"missing table: {title}")


class StubReports:
    def build_model_report(self) -> BotReply:
        return BotReply.image(b"model report")

    def build_provider_report(self) -> BotReply:
        return BotReply.image(b"provider report")

    def build_provider_models_report(self, provider: str) -> BotReply:
        return BotReply.image(f"provider models: {provider}".encode())

    def add_watched_model(self, model_name: str) -> BotReply:
        return BotReply.text(f"watch add: {model_name}")

    def remove_watched_model(self, model_name: str) -> BotReply:
        return BotReply.text(f"watch remove: {model_name}")

    def build_watched_models_report(self) -> BotReply:
        return BotReply.image(b"watched models")

    def clear_watched_models(self) -> BotReply:
        return BotReply.text("watch clear")


class StubMessenger:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, BotReply]] = []

    def send_reply(
        self,
        receive_id_type: str,
        receive_id: str,
        reply: BotReply,
    ) -> bool:
        self.messages.append((receive_id_type, receive_id, reply))
        return True


def message_payload(
    text: str,
    *,
    create_time: str | None = None,
    message_id: str = "message-id",
) -> dict[str, object]:
    message = {
        "message_id": message_id,
        "chat_id": "chat-id",
        "content": json_text(text),
    }
    if create_time is not None:
        message["create_time"] = create_time
    return {"event": {"message": message}}


def json_text(text: str) -> str:
    return json.dumps({"text": text})


# ---- command parsing ----


def test_parse_provider_query() -> None:
    assert parse_provider_query("provider openai") == "openai"
    assert parse_provider_query("provider Anthropic") == "Anthropic"
    assert parse_provider_query("查看分组 openai") is None
    assert parse_provider_query("帮助") is None


def test_parse_readme_command_contract() -> None:
    command = parse_text_command("provider openai")

    assert command.kind is CommandKind.PROVIDER_MODELS
    assert command.provider == "openai"

    watch_add = parse_text_command("watch add openai/gpt-4.1")
    watch_remove = parse_text_command("watch remove openai/gpt-4.1")
    watch_list = parse_text_command("watch list")
    watch_clear = parse_text_command("watch clear")
    watch_unknown = parse_text_command("watch something")

    assert watch_add.kind is CommandKind.WATCH_ADD
    assert watch_add.model_name == "openai/gpt-4.1"
    assert watch_remove.kind is CommandKind.WATCH_REMOVE
    assert watch_remove.model_name == "openai/gpt-4.1"
    assert watch_list.kind is CommandKind.WATCH_LIST
    assert watch_clear.kind is CommandKind.WATCH_CLEAR
    assert watch_unknown.kind is CommandKind.WATCH_HELP


def test_parse_menu_event_contract() -> None:
    assert parse_menu_event("help").kind is CommandKind.HELP
    assert parse_menu_event("list_providers").kind is CommandKind.LIST_PROVIDERS
    assert parse_menu_event("send_report").kind is CommandKind.MODEL_REPORT
    assert parse_menu_event("list_watched").kind is CommandKind.UNKNOWN


# ---- text / menu reply routing ----


def test_help_and_unknown_reply(tmp_path: Path) -> None:
    reports, _, _ = service(tmp_path, [])

    help_reply = build_reply_for_text("help", reports)
    unknown_reply = build_reply_for_text("unknown", reports)

    assert help_reply.msg_type == "text"
    assert "未知命令：help" in help_reply.content["text"]
    assert "provider <提供商>" in help_reply.content["text"]
    assert unknown_reply.msg_type == "text"
    assert "支持的文本命令" in unknown_reply.content["text"]


def test_reports_and_provider_commands(tmp_path: Path) -> None:
    reports, client, renderer = service(
        tmp_path,
        [
            model("anthropic/claude-3.7", released_at=1710000000),
            model("openai/gpt-4.1", released_at=1710000000),
        ],
    )

    baseline = reports.build_model_report()
    baseline_document = renderer.documents[-1]
    baseline_text = document_text(renderer.documents[-1])
    assert_image_reply(baseline, b"png-1")
    assert renderer.documents[-1].title == "模型报告"
    assert table_titles(renderer.documents[-1]) == ["摘要", "新增模型", "关注模型"]
    baseline_summary = table_by_title(baseline_document, "摘要")
    assert baseline_summary.headers == ["检测时间", "模型总数", "新增模型", "状态"]
    assert len(baseline_summary.rows) == 1
    assert baseline_summary.rows[0][1:] == ["2", "0", "首次运行，已建立本地模型基线"]
    assert "指标" not in baseline_summary.headers
    assert "值" not in baseline_summary.headers
    baseline_watched = table_by_title(baseline_document, "关注模型")
    assert baseline_watched.headers == ["模型", "发布", "输入", "输出", "缓存"]
    assert baseline_watched.rows == [["暂无关注模型", "-", "-", "-", "-"]]
    assert "首次运行" in baseline_text
    assert "模型总数\n新增模型" in baseline_text
    assert "提供商 Top 10" not in baseline_text
    assert "操作提示" not in baseline_text
    assert "模型\n提供商\n输入\n输出\n缓存" in baseline_text

    reports.add_watched_model("openai/gpt-4.1")
    client.models = [
        model("anthropic/claude-3.7", released_at=1710000000),
        model("deepseek/deepseek-r1", released_at=1776902400),
        model(
            "openai/gpt-4.1",
            released_at=1710000000,
            output_price="0.000020",
        ),
        model("openai/gpt-4.2", released_at=1776988800),
    ]
    update = reports.build_model_report()
    update_document = renderer.documents[-1]
    update_text = document_text(renderer.documents[-1])
    assert_image_reply(update, b"png-2")
    update_summary = table_by_title(update_document, "摘要")
    assert update_summary.headers == ["检测时间", "模型总数", "新增模型", "状态"]
    assert update_summary.rows[0][1:] == ["4", "2", "发现新增模型"]
    watched_table = table_by_title(update_document, "关注模型")
    assert watched_table.headers == ["模型", "发布", "输入", "输出", "缓存"]
    assert watched_table.rows == [["openai/gpt-4.1", "24-03-10", "$2/M", "$20/M", "-"]]
    assert "新增模型\n状态" in update_text
    assert "openai/gpt-4.2\nopenai\n$2/M\n$8/M\n-" in update_text
    assert "提供商 Top 10" not in update_text

    provider_report = build_reply_for_menu_event("list_providers", reports)
    provider_document = renderer.documents[-1]
    provider_report_text = document_text(renderer.documents[-1])
    assert_image_reply(provider_report, b"png-3")
    assert renderer.documents[-1].title == "可用提供商"
    provider_summary = table_by_title(provider_document, "摘要")
    assert provider_summary.headers == ["模型总数", "提供商数"]
    assert provider_summary.rows == [["4", "3"]]
    assert "指标" not in provider_summary.headers
    assert "值" not in provider_summary.headers
    provider_counts_table = table_by_title(provider_document, "提供商模型数")
    assert provider_counts_table.headers == ["提供商", "模型数", "提供商", "模型数"]
    assert provider_counts_table.rows == [
        ["openai", "2", "deepseek", "1"],
        ["anthropic", "1", "", ""],
    ]
    assert "提供商\n模型数\n提供商\n模型数" in provider_report_text
    assert "openai\n2" in provider_report_text
    assert "provider openai" in provider_report_text

    provider_models = build_reply_for_text("provider openai", reports)
    provider_models_document = renderer.documents[-1]
    provider_models_text = document_text(renderer.documents[-1])
    assert_image_reply(provider_models, b"png-4")
    assert renderer.documents[-1].title == "提供商：openai"
    provider_models_summary = table_by_title(provider_models_document, "提供商摘要")
    provider_models_table = table_by_title(provider_models_document, "模型列表")
    assert provider_models_summary.headers == ["提供商", "模型数", "展示数量"]
    assert provider_models_summary.rows == [["openai", "2", "2/2"]]
    assert provider_models_table.rows == [
        ["openai/gpt-4.2", "26-04-24", "$2/M", "$8/M", "-"],
        ["openai/gpt-4.1", "24-03-10", "$2/M", "$20/M", "-"],
    ]
    assert "模型\n发布\n输入\n输出\n缓存" in provider_models_text
    assert "openai/gpt-4.2\n26-04-24\n$2/M\n$8/M\n-" in provider_models_text


def test_format_time_displays_beijing_time() -> None:
    assert format_time("2026-01-01T00:00:00+00:00") == "26-01-01 08:00"


def test_format_released_at_displays_beijing_date() -> None:
    assert format_released_at(1776988800) == "26-04-24"
    assert format_released_at(None) == "-"


def test_text_validation_paths_stay_text(tmp_path: Path) -> None:
    reports, _, _ = service(tmp_path, [model("openai/gpt-4.1", released_at=1)])

    missing_provider = build_reply_for_text("provider missing", reports)
    empty_provider = reports.build_provider_models_report("")

    assert missing_provider.msg_type == "text"
    assert "未找到提供商：missing" in missing_provider.content["text"]
    assert empty_provider.msg_type == "text"
    assert "请提供提供商名称" in empty_provider.content["text"]


def test_watch_commands_manage_global_model_names(tmp_path: Path) -> None:
    reports, client, renderer = service(
        tmp_path,
        [
            model(
                "anthropic/claude-3.7",
                released_at=1710000000,
                output_price="0.000004",
            ),
            model("openai/gpt-4.1", released_at=1710000000),
        ],
    )

    missing = build_reply_for_text("watch add missing/model", reports)
    added = build_reply_for_text("watch add openai/gpt-4.1", reports)
    added_anthropic = build_reply_for_text("watch add anthropic/claude-3.7", reports)
    duplicate = build_reply_for_text("watch add openai/gpt-4.1", reports)
    watched = build_reply_for_text("watch list", reports)
    watched_document = renderer.documents[-1]
    watched_table = table_by_title(watched_document, "模型列表")

    assert missing.content["text"] == "未找到模型：missing/model"
    assert added.content["text"] == "已关注模型：openai/gpt-4.1"
    assert added_anthropic.content["text"] == "已关注模型：anthropic/claude-3.7"
    assert duplicate.content["text"] == "已在关注列表中：openai/gpt-4.1"
    assert_image_reply(watched, b"png-1")
    assert watched_document.title == "关注模型"
    assert watched_table.headers == ["模型", "发布", "输入", "输出", "缓存"]
    assert watched_table.rows == [
        ["anthropic/claude-3.7", "24-03-10", "$2/M", "$4/M", "-"],
        ["openai/gpt-4.1", "24-03-10", "$2/M", "$8/M", "-"],
    ]

    client.models = [
        model(
            "anthropic/claude-3.7",
            released_at=1710000000,
            output_price="0.000004",
        )
    ]
    watched_missing = build_reply_for_text("watch list", reports)
    watched_missing_table = table_by_title(renderer.documents[-1], "模型列表")
    assert_image_reply(watched_missing, b"png-2")
    assert watched_missing_table.rows == [
        ["anthropic/claude-3.7", "24-03-10", "$2/M", "$4/M", "-"],
        ["openai/gpt-4.1（未找到）", "-", "-", "-", "-"],
    ]
    assert "未在当前 catalog 中找到：openai/gpt-4.1" == watched_missing_table.note

    removed = build_reply_for_text("watch remove openai/gpt-4.1", reports)
    remove_again = build_reply_for_text("watch remove openai/gpt-4.1", reports)
    build_reply_for_text("watch add anthropic/claude-3.7", reports)
    clear = build_reply_for_text("watch clear", reports)

    assert removed.content["text"] == "已取消关注模型：openai/gpt-4.1"
    assert remove_again.content["text"] == "未关注模型：openai/gpt-4.1"
    assert clear.content["text"] == "已清空关注列表，共移除 1 个模型。"


def test_watch_command_help_text(tmp_path: Path) -> None:
    reports, _, _ = service(tmp_path, [])

    missing_name = build_reply_for_text("watch add", reports)
    unknown_action = build_reply_for_text("watch unknown", reports)

    assert missing_name.msg_type == "text"
    assert "请提供模型名称" in missing_name.content["text"]
    assert unknown_action.msg_type == "text"
    assert "watch add <模型名称>" in unknown_action.content["text"]


def test_menu_events_route_to_readme_actions(tmp_path: Path) -> None:
    reports, _, renderer = service(tmp_path, [model("openai/gpt-4.1", released_at=1)])

    help_reply = build_reply_for_menu_event("help", reports)
    provider_reply = build_reply_for_menu_event("list_providers", reports)
    report_reply = build_reply_for_menu_event("send_report", reports)
    unknown_reply = build_reply_for_menu_event("other", reports)

    assert help_reply.msg_type == "text"
    assert "可用命令" in help_reply.content["text"]
    assert provider_reply.msg_type == "image"
    assert renderer.documents[-2].title == "可用提供商"
    assert report_reply.msg_type == "image"
    assert renderer.documents[-1].title == "模型报告"
    assert unknown_reply.msg_type == "text"
    assert "未知菜单事件" in unknown_reply.content["text"]


# ---- report rendering ----


def test_pillow_report_renderer_outputs_bounded_png() -> None:
    font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    if not font_path.is_file():
        pytest.skip("test font is not available")

    renderer = PillowReportRenderer(font_path, max_width=620)
    document = ReportDocument(
        title="模型报告",
        blocks=[
            TextBlock("摘要", ["包含中文标题和很长的模型名称。"]),
            TableBlock(
                "模型列表",
                ["模型", "提供商", "输入", "输出", "缓存"],
                [
                    [
                        "provider/very-long-model-name-with-many-segments-and-suffix",
                        "openai",
                        "$2/M",
                        "$8/M",
                        "-",
                    ]
                ],
            ),
        ],
    )

    png_bytes = renderer.render(document)
    image = Image.open(BytesIO(png_bytes))

    assert image.format == "PNG"
    assert image.width <= 620
    assert image.width > 0
    assert image.height > 0


def test_pillow_report_renderer_keeps_text_blocks_compact() -> None:
    font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    if not font_path.is_file():
        pytest.skip("test font is not available")

    renderer = PillowReportRenderer(font_path, max_width=900)
    document = ReportDocument(
        title="可用提供商",
        blocks=[
            TableBlock(
                "摘要",
                ["指标", "值"],
                [["模型总数", "12"], ["提供商数", "2"]],
            ),
            TableBlock(
                "提供商模型数",
                ["提供商", "模型数"],
                [["openai", "10"], ["anthropic", "2"]],
            ),
            TextBlock("查询示例", ["provider openai"]),
        ],
    )

    png_bytes = renderer.render(document)
    image = Image.open(BytesIO(png_bytes))

    assert image.width < 900
    assert image.width >= 520


# ---- handlers ----


def test_message_payload_routes_text_command() -> None:
    messenger = StubMessenger()

    handle_message_payload(
        message_payload("provider openai"),
        StubReports(),
        messenger,
    )

    assert messenger.messages == [
        (
            "chat_id",
            "chat-id",
            BotReply.image(b"provider models: openai"),
        )
    ]


def test_fresh_message_payload_routes_text_command(monkeypatch) -> None:
    now = 1_800_000_000.0
    messenger = StubMessenger()
    monkeypatch.setattr("app.handlers.time_module.time", lambda: now)

    handle_message_payload(
        message_payload("provider openai", create_time=str(int((now - 30) * 1000))),
        StubReports(),
        messenger,
        max_message_age_seconds=120,
    )

    assert messenger.messages == [
        (
            "chat_id",
            "chat-id",
            BotReply.image(b"provider models: openai"),
        )
    ]


def test_stale_message_payload_is_not_replied_to(monkeypatch) -> None:
    now = 1_800_000_000.0
    messenger = StubMessenger()
    monkeypatch.setattr("app.handlers.time_module.time", lambda: now)

    handle_message_payload(
        message_payload("provider openai", create_time=str(int((now - 121) * 1000))),
        StubReports(),
        messenger,
        max_message_age_seconds=120,
    )

    assert messenger.messages == []


def test_duplicate_message_payload_is_only_replied_to_once(monkeypatch) -> None:
    now = 1_800_000_000.0
    messenger = StubMessenger()
    deduplicator = MessageDeduplicator(ttl_seconds=180)
    payload = message_payload(
        "provider openai",
        create_time=str(int((now - 30) * 1000)),
        message_id="same-message-id",
    )
    monkeypatch.setattr("app.handlers.time_module.time", lambda: now)

    handle_message_payload(
        payload,
        StubReports(),
        messenger,
        deduplicator=deduplicator,
        max_message_age_seconds=120,
    )
    handle_message_payload(
        payload,
        StubReports(),
        messenger,
        deduplicator=deduplicator,
        max_message_age_seconds=120,
    )

    assert messenger.messages == [
        (
            "chat_id",
            "chat-id",
            BotReply.image(b"provider models: openai"),
        )
    ]


def test_invalid_message_create_time_processes_anyway() -> None:
    messenger = StubMessenger()

    handle_message_payload(
        message_payload("provider openai", create_time="not-a-time"),
        StubReports(),
        messenger,
    )

    assert messenger.messages == [
        (
            "chat_id",
            "chat-id",
            BotReply.image(b"provider models: openai"),
        )
    ]


def test_menu_payload_keeps_only_supported_menu_events() -> None:
    messenger = StubMessenger()

    handle_menu_payload(
        {
            "event": {
                "event_key": "list_watched",
                "operator": {"operator_id": {"open_id": "open-id"}},
            }
        },
        StubReports(),
        messenger,
    )

    assert messenger.messages == [
        (
            "open_id",
            "open-id",
            BotReply.text("已收到未知菜单事件：list_watched"),
        )
    ]


def test_worker_import_requires_no_runtime_config(monkeypatch) -> None:
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)

    module = importlib.import_module("app.worker")

    assert hasattr(module, "main")
