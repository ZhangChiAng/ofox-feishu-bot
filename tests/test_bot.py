import importlib
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
from app.handlers import handle_menu_payload, handle_message_payload
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
    assert table_titles(renderer.documents[-1]) == ["摘要", "新增模型", "提供商 Top 10"]
    baseline_summary = table_by_title(baseline_document, "摘要")
    assert baseline_summary.headers == ["检测时间", "模型总数", "新增模型", "状态"]
    assert len(baseline_summary.rows) == 1
    assert baseline_summary.rows[0][1:] == ["2", "0", "首次运行，已建立本地模型基线"]
    assert "指标" not in baseline_summary.headers
    assert "值" not in baseline_summary.headers
    baseline_provider_counts = table_by_title(baseline_document, "提供商 Top 10")
    assert baseline_provider_counts.headers == ["提供商", "模型数", "提供商", "模型数"]
    assert baseline_provider_counts.rows == [["anthropic", "1", "openai", "1"]]
    assert "首次运行" in baseline_text
    assert "模型总数\n新增模型" in baseline_text
    assert "提供商数" not in baseline_text
    assert "模型\n提供商\n输入\n输出\n缓存" in baseline_text

    client.models = [
        model("anthropic/claude-3.7", released_at=1710000000),
        model("deepseek/deepseek-r1", released_at=1776902400),
        model("openai/gpt-4.1", released_at=1710000000),
        model("openai/gpt-4.2", released_at=1776988800),
    ]
    update = reports.build_model_report()
    update_document = renderer.documents[-1]
    update_text = document_text(renderer.documents[-1])
    assert_image_reply(update, b"png-2")
    update_summary = table_by_title(update_document, "摘要")
    assert update_summary.headers == ["检测时间", "模型总数", "新增模型", "状态"]
    assert update_summary.rows[0][1:] == ["4", "2", "发现新增模型"]
    provider_top_10 = table_by_title(update_document, "提供商 Top 10")
    assert provider_top_10.headers == ["提供商", "模型数", "提供商", "模型数"]
    assert provider_top_10.rows == [
        ["openai", "2", "deepseek", "1"],
        ["anthropic", "1", "", ""],
    ]
    assert "新增模型\n状态" in update_text
    assert "openai/gpt-4.2\nopenai\n$2/M\n$8/M\n-" in update_text

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
    assert provider_models_summary.headers == ["提供商", "模型数", "展示数量"]
    assert provider_models_summary.rows == [["openai", "2", "2/2"]]
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
        {
            "event": {
                "message": {
                    "chat_id": "chat-id",
                    "content": '{"text": "provider openai"}',
                }
            }
        },
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
