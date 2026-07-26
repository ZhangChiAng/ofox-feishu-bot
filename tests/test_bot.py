import importlib
import json
from dataclasses import replace
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from app.commands import (
    SUPPORTED_MENU_EVENTS,
    CommandKind,
    build_reply_for_menu_event,
    build_reply_for_text,
    parse_menu_event,
    parse_provider_query,
    parse_text_command,
)
from app.handlers import (
    MENU_RECEIVED_ACKNOWLEDGMENT,
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
from app.reports import (
    ReportService,
    format_released_at,
    format_time,
    sort_key_model_prices,
)
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


class StubWatchCards:
    def open_management_card(self) -> BotReply:
        return BotReply.interactive({"schema": "2.0", "body": {"elements": []}})


STUB_WATCH_CARDS = StubWatchCards()


class StubMessenger:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, BotReply]] = []
        self.reactions: list[tuple[str, str]] = []

    def add_reaction(self, message_id: str, emoji_type: str) -> bool:
        self.reactions.append((message_id, emoji_type))
        return True

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
    message_type: str = "text",
    chat_type: str = "p2p",
) -> dict[str, object]:
    message = {
        "message_id": message_id,
        "chat_id": "chat-id",
        "content": json_text(text),
        "message_type": message_type,
        "chat_type": chat_type,
    }
    if create_time is not None:
        message["create_time"] = create_time
    return {"event": {"message": message}}


def menu_payload(
    event_key: str,
    *,
    event_id: str = "event-id",
    timestamp: str | None = None,
    open_id: str | None = "open-id",
    user_id: str | None = None,
) -> dict[str, object]:
    operator_id = {}
    if open_id is not None:
        operator_id["open_id"] = open_id
    if user_id is not None:
        operator_id["user_id"] = user_id
    event: dict[str, object] = {
        "event_key": event_key,
        "operator": {"operator_id": operator_id},
    }
    if timestamp is not None:
        event["timestamp"] = timestamp
    return {"header": {"event_id": event_id}, "event": event}


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

    for text in (
        "watch add openai/gpt-4.1",
        "watch remove openai/gpt-4.1",
        "watch list",
        "watch clear",
        "watch something",
    ):
        assert parse_text_command(text).kind is CommandKind.UNKNOWN


def test_parse_menu_event_contract() -> None:
    assert SUPPORTED_MENU_EVENTS == {
        "list_providers",
        "send_report",
        "manage_watches",
    }
    assert parse_menu_event("help").kind is CommandKind.UNKNOWN
    assert parse_menu_event("list_providers").kind is CommandKind.LIST_PROVIDERS
    assert parse_menu_event("send_report").kind is CommandKind.MODEL_REPORT
    assert parse_menu_event("manage_watches").kind is CommandKind.MANAGE_WATCHES
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

    reports.repository.add_watched_model("openai/gpt-4.1")
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

    provider_report = build_reply_for_menu_event(
        "list_providers",
        reports,
        STUB_WATCH_CARDS,
    )
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


def test_model_price_sort_uses_output_then_input_then_cache_read() -> None:
    models = [
        model(
            "openai/a-last",
            output_price="0.000002",
            input_price="0.000002",
            cache_read_price="0.000002",
        ),
        model(
            "openai/x-cache-first",
            output_price="0.000002",
            input_price="0.000002",
            cache_read_price="0.000001",
        ),
        model(
            "openai/y-input-first",
            output_price="0.000002",
            input_price="0.000001",
            cache_read_price="0.000009",
        ),
        model(
            "openai/z-output-first",
            output_price="0.000001",
            input_price="0.000009",
            cache_read_price="0.000009",
        ),
    ]

    ordered = sorted(models, key=sort_key_model_prices)

    assert [item.id for item in ordered] == [
        "openai/z-output-first",
        "openai/y-input-first",
        "openai/x-cache-first",
        "openai/a-last",
    ]


@pytest.mark.parametrize("field", ["output_price", "input_price", "cache_read_price"])
@pytest.mark.parametrize(
    "invalid_price",
    [None, "", "not-a-number", "NaN", "Infinity"],
)
def test_model_price_sort_places_unusable_price_after_valid_price(
    field: str,
    invalid_price: str | None,
) -> None:
    valid = model(
        "openai/z-valid",
        output_price="0.000001",
        input_price="0.000001",
        cache_read_price="0.000001",
    )
    unusable = replace(
        model(
            "openai/a-unusable",
            output_price="0.000001",
            input_price="0.000001",
            cache_read_price="0.000001",
        ),
        **{field: invalid_price},
    )

    assert sorted([unusable, valid], key=sort_key_model_prices) == [valid, unusable]


def test_model_price_sort_uses_model_name_then_id_for_equal_prices() -> None:
    alpha_z = replace(model("openai/z-id"), name="Alpha")
    zulu = replace(model("openai/a-id"), name="Zulu")
    alpha_a = replace(model("openai/a-id"), name="Alpha")

    ordered = sorted([zulu, alpha_z, alpha_a], key=sort_key_model_prices)

    assert [(item.name, item.id) for item in ordered] == [
        ("Alpha", "openai/a-id"),
        ("Alpha", "openai/z-id"),
        ("Zulu", "openai/a-id"),
    ]


def test_all_model_tables_share_price_sorting_before_limits(tmp_path: Path) -> None:
    price_ordered_models = [
        model(
            "openai/z-output-first",
            released_at=1,
            output_price="0.000001",
            input_price="0.000009",
            cache_read_price="0.000009",
        ),
        model(
            "openai/y-input-first",
            released_at=2,
            output_price="0.000002",
            input_price="0.000001",
            cache_read_price="0.000009",
        ),
        model(
            "openai/x-cache-first",
            released_at=3,
            output_price="0.000002",
            input_price="0.000002",
            cache_read_price="0.000001",
        ),
        model(
            "openai/a-last",
            released_at=4,
            output_price="0.000002",
            input_price="0.000002",
            cache_read_price="0.000002",
        ),
    ]
    expected_order = [
        "openai/z-output-first",
        "openai/y-input-first",
        "openai/x-cache-first",
        "openai/a-last",
    ]
    reports, _, renderer = service(tmp_path / "catalog", price_ordered_models[::-1])

    reports.build_provider_models_report("openai", limit=3)
    provider_table = table_by_title(renderer.documents[-1], "模型列表")
    for item in price_ordered_models:
        reports.repository.add_watched_model(item.name)
    reports.build_model_report()
    watched_table = table_by_title(renderer.documents[-1], "关注模型")

    assert [row[0] for row in provider_table.rows] == expected_order[:3]
    assert [row[0] for row in watched_table.rows] == expected_order

    new_reports, new_client, new_renderer = service(
        tmp_path / "new-models",
        [model("baseline/original")],
    )
    new_reports.build_model_report(limit=3)
    new_client.models = [model("baseline/original"), *price_ordered_models[::-1]]
    new_reports.build_model_report(limit=3)
    new_table = table_by_title(new_renderer.documents[-1], "新增模型")

    assert [row[0] for row in new_table.rows] == expected_order[:3]
    assert new_table.note == "还有 1 个新增模型未展示。"


def test_text_validation_paths_stay_text(tmp_path: Path) -> None:
    reports, _, _ = service(tmp_path, [model("openai/gpt-4.1", released_at=1)])

    missing_provider = build_reply_for_text("provider missing", reports)
    empty_provider = reports.build_provider_models_report("")

    assert missing_provider.msg_type == "text"
    assert missing_provider.content["text"] == (
        "未找到提供商：missing\n建议指令：provider openai"
    )
    assert empty_provider.msg_type == "text"
    assert "请提供提供商名称" in empty_provider.content["text"]


def test_provider_name_matching_ignores_case_and_has_stable_suggestion(
    tmp_path: Path,
) -> None:
    reports, _, renderer = service(
        tmp_path,
        [
            model("OpenAI/gpt-4.1"),
            model("anthropic/claude-3.7"),
        ],
    )

    matched = build_reply_for_text("provider openai", reports)
    suggested = build_reply_for_text("provider z", reports)

    assert_image_reply(matched, b"png-1")
    assert renderer.documents[-1].title == "提供商：OpenAI"
    assert suggested.content["text"] == (
        "未找到提供商：z\n建议指令：provider anthropic"
    )


def test_provider_without_candidates_keeps_catalog_hint(tmp_path: Path) -> None:
    reports, _, _ = service(tmp_path, [])

    reply = build_reply_for_text("provider missing", reports)

    assert reply.content["text"] == (
        "未找到提供商：missing\n点击菜单“可用提供商”查看可用提供商。"
    )


@pytest.mark.parametrize(
    "text",
    [
        "watch",
        "watch add",
        "watch add openai/gpt-4.1",
        "watch remove openai/gpt-4.1",
        "watch list",
        "watch clear",
    ],
)
def test_watch_text_commands_are_unknown(tmp_path: Path, text: str) -> None:
    reports, _, _ = service(tmp_path, [])

    reply = build_reply_for_text(text, reports)

    assert reply.msg_type == "text"
    assert f"未知命令：{text}" in reply.content["text"]
    assert "关注管理" in reply.content["text"]
    supported_commands = reply.content["text"].split("支持的文本命令：", 1)[1]
    assert "watch" not in supported_commands


def test_menu_events_route_to_readme_actions(tmp_path: Path) -> None:
    reports, _, renderer = service(tmp_path, [model("openai/gpt-4.1", released_at=1)])

    provider_reply = build_reply_for_menu_event(
        "list_providers",
        reports,
        STUB_WATCH_CARDS,
    )
    report_reply = build_reply_for_menu_event(
        "send_report",
        reports,
        STUB_WATCH_CARDS,
    )
    watch_reply = build_reply_for_menu_event(
        "manage_watches",
        reports,
        STUB_WATCH_CARDS,
    )
    unknown_reply = build_reply_for_menu_event("other", reports, STUB_WATCH_CARDS)

    assert provider_reply.msg_type == "image"
    assert renderer.documents[-2].title == "可用提供商"
    assert report_reply.msg_type == "image"
    assert renderer.documents[-1].title == "模型报告"
    assert watch_reply.msg_type == "interactive"
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

    assert messenger.reactions == [("message-id", "Typing")]
    assert messenger.messages == [
        (
            "chat_id",
            "chat-id",
            BotReply.image(b"provider models: openai"),
        ),
    ]


def test_message_payload_processes_command_when_acknowledgment_raises() -> None:
    class AcknowledgmentFailingMessenger(StubMessenger):
        def add_reaction(self, message_id: str, emoji_type: str) -> bool:
            raise RuntimeError("temporary acknowledgment failure")

    messenger = AcknowledgmentFailingMessenger()

    handle_message_payload(
        message_payload("watch clear"),
        StubReports(),
        messenger,
        deduplicator=MessageDeduplicator(ttl_seconds=180),
    )

    assert messenger.reactions == []
    assert messenger.messages == [
        (
            "chat_id",
            "chat-id",
            BotReply.text(
                "未知命令：watch clear\n"
                "支持的文本命令：\n"
                "1. provider <提供商>\n\n"
                "关注列表请通过机器人菜单“关注管理”维护。"
            ),
        ),
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

    assert messenger.reactions == [("message-id", "Typing")]
    assert messenger.messages == [
        (
            "chat_id",
            "chat-id",
            BotReply.image(b"provider models: openai"),
        ),
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
    assert messenger.reactions == []


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

    assert messenger.reactions == [("same-message-id", "Typing")]
    assert messenger.messages == [
        (
            "chat_id",
            "chat-id",
            BotReply.image(b"provider models: openai"),
        ),
    ]


def test_invalid_message_create_time_processes_anyway() -> None:
    messenger = StubMessenger()

    handle_message_payload(
        message_payload("provider openai", create_time="not-a-time"),
        StubReports(),
        messenger,
    )

    assert messenger.reactions == [("message-id", "Typing")]
    assert messenger.messages == [
        (
            "chat_id",
            "chat-id",
            BotReply.image(b"provider models: openai"),
        ),
    ]


@pytest.mark.parametrize(
    ("message_type", "chat_type"),
    [
        ("image", "p2p"),
        ("file", "p2p"),
        ("text", "group"),
    ],
)
def test_message_payload_ignores_unsupported_events(
    message_type: str,
    chat_type: str,
) -> None:
    messenger = StubMessenger()

    handle_message_payload(
        message_payload(
            "provider openai",
            message_type=message_type,
            chat_type=chat_type,
        ),
        StubReports(),
        messenger,
    )

    assert messenger.reactions == []
    assert messenger.messages == []


def test_menu_payload_keeps_only_supported_menu_events() -> None:
    messenger = StubMessenger()

    handle_menu_payload(
        menu_payload("list_watched"),
        StubReports(),
        STUB_WATCH_CARDS,
        messenger,
    )

    assert messenger.messages == [
        (
            "open_id",
            "open-id",
            BotReply.text(MENU_RECEIVED_ACKNOWLEDGMENT),
        ),
        (
            "open_id",
            "open-id",
            BotReply.text("已收到未知菜单事件：list_watched"),
        ),
    ]


def test_fresh_menu_payload_routes_menu_command(monkeypatch) -> None:
    now = 1_800_000_000.0
    messenger = StubMessenger()
    monkeypatch.setattr("app.handlers.time_module.time", lambda: now)

    handle_menu_payload(
        menu_payload("send_report", timestamp=str(int(now - 30))),
        StubReports(),
        STUB_WATCH_CARDS,
        messenger,
        max_message_age_seconds=120,
    )

    assert messenger.messages == [
        (
            "open_id",
            "open-id",
            BotReply.text(MENU_RECEIVED_ACKNOWLEDGMENT),
        ),
        (
            "open_id",
            "open-id",
            BotReply.image(b"model report"),
        ),
    ]


def test_stale_menu_payload_is_not_replied_to(monkeypatch) -> None:
    now = 1_800_000_000.0
    messenger = StubMessenger()
    monkeypatch.setattr("app.handlers.time_module.time", lambda: now)

    handle_menu_payload(
        menu_payload("send_report", timestamp=str(int(now - 121))),
        StubReports(),
        STUB_WATCH_CARDS,
        messenger,
        max_message_age_seconds=120,
    )

    assert messenger.messages == []


def test_duplicate_menu_payload_is_only_replied_to_once(monkeypatch) -> None:
    now = 1_800_000_000.0
    messenger = StubMessenger()
    deduplicator = MessageDeduplicator(ttl_seconds=180)
    payload = menu_payload(
        "send_report",
        event_id="same-event-id",
        timestamp=str(int((now - 30) * 1000)),
    )
    monkeypatch.setattr("app.handlers.time_module.time", lambda: now)

    handle_menu_payload(
        payload,
        StubReports(),
        STUB_WATCH_CARDS,
        messenger,
        deduplicator=deduplicator,
        max_message_age_seconds=120,
    )
    handle_menu_payload(
        payload,
        StubReports(),
        STUB_WATCH_CARDS,
        messenger,
        deduplicator=deduplicator,
        max_message_age_seconds=120,
    )

    assert messenger.messages == [
        (
            "open_id",
            "open-id",
            BotReply.text(MENU_RECEIVED_ACKNOWLEDGMENT),
        ),
        (
            "open_id",
            "open-id",
            BotReply.image(b"model report"),
        ),
    ]


def test_menu_payload_falls_back_to_user_id() -> None:
    messenger = StubMessenger()

    handle_menu_payload(
        menu_payload("list_providers", open_id=None, user_id="user-id"),
        StubReports(),
        STUB_WATCH_CARDS,
        messenger,
    )

    assert messenger.messages == [
        (
            "user_id",
            "user-id",
            BotReply.text(MENU_RECEIVED_ACKNOWLEDGMENT),
        ),
        (
            "user_id",
            "user-id",
            build_reply_for_menu_event(
                "list_providers",
                StubReports(),
                STUB_WATCH_CARDS,
            ),
        ),
    ]


def test_menu_payload_without_receiver_is_not_acknowledged() -> None:
    messenger = StubMessenger()

    handle_menu_payload(
        menu_payload("list_providers", open_id=None),
        StubReports(),
        STUB_WATCH_CARDS,
        messenger,
    )

    assert messenger.messages == []


def test_menu_payload_processes_command_when_acknowledgment_raises() -> None:
    class AcknowledgmentFailingMessenger(StubMessenger):
        def send_reply(
            self,
            receive_id_type: str,
            receive_id: str,
            reply: BotReply,
        ) -> bool:
            if reply == BotReply.text(MENU_RECEIVED_ACKNOWLEDGMENT):
                raise RuntimeError("temporary acknowledgment failure")
            return super().send_reply(receive_id_type, receive_id, reply)

    messenger = AcknowledgmentFailingMessenger()

    handle_menu_payload(
        menu_payload("list_providers"),
        StubReports(),
        STUB_WATCH_CARDS,
        messenger,
    )

    assert messenger.messages == [
        (
            "open_id",
            "open-id",
            build_reply_for_menu_event(
                "list_providers",
                StubReports(),
                STUB_WATCH_CARDS,
            ),
        ),
    ]


def test_worker_import_requires_no_runtime_config(monkeypatch) -> None:
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)

    module = importlib.import_module("app.worker")

    assert hasattr(module, "main")
