"""Report builders for model summaries sent to Feishu."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol
from zoneinfo import ZoneInfo

from app.models import OfoxModel, provider_counts
from app.report_rendering import ReportDocument, ReportRenderer, TableBlock, TextBlock
from app.replies import BotReply
from app.repository import ModelRepository

BEIJING_TZ = ZoneInfo("Asia/Shanghai")


class ModelSource(Protocol):
    """Protocol for objects that can fetch normalized Ofox models."""

    def fetch_models(self) -> list[OfoxModel]:
        """Fetches the current model catalog.

        Returns:
            List of normalized Ofox models.
        """

        ...


class ReportService:
    """Builds user-facing reports from model source and repository data."""

    def __init__(
        self,
        client: ModelSource,
        repository: ModelRepository,
        renderer: ReportRenderer,
    ) -> None:
        """Initializes the report service.

        Args:
            client: Source used to fetch the current Ofox model catalog.
            repository: Repository used to persist and diff model snapshots.
            renderer: Image renderer used for structured reports.
        """

        self.client = client
        self.repository = repository
        self.renderer = renderer

    def build_model_report(self, limit: int = 12) -> BotReply:
        """Builds the main model report image and persists the latest snapshot.

        Args:
            limit: Maximum number of new models to include inline.

        Returns:
            Feishu-ready image reply.
        """

        models = self.client.fetch_models()
        result = self.repository.sync_models(models)
        status = "发现新增模型" if result.new_models else "无新增模型"
        if result.baseline_created:
            # The first sync seeds the database; every model would otherwise look new.
            status = "首次运行，已建立本地模型基线"

        summary_rows = [
            [
                format_time(result.checked_at),
                str(result.total_count),
                str(len(result.new_models)),
                status,
            ]
        ]
        new_model_note = ""
        if len(result.new_models) > limit:
            new_model_note = f"还有 {len(result.new_models) - limit} 个新增模型未展示。"

        document = ReportDocument(
            title="模型报告",
            blocks=[
                TableBlock(
                    "摘要",
                    ["检测时间", "模型总数", "新增模型", "状态"],
                    summary_rows,
                ),
                TableBlock(
                    "新增模型",
                    ["模型", "提供商", "输入", "输出", "缓存"],
                    format_new_model_rows(
                        result.new_models,
                        limit=limit,
                        baseline_created=result.baseline_created,
                    ),
                    note=new_model_note,
                ),
                TableBlock(
                    "提供商 Top 10",
                    ["提供商", "模型数", "提供商", "模型数"],
                    format_provider_count_grid_rows(result.provider_counts, limit=10),
                ),
                TextBlock(
                    "操作提示",
                    [
                        "点击菜单“可用提供商”查看提供商/模型数表格。",
                        "发送“provider <提供商>”查看指定提供商的模型表格。",
                    ],
                ),
            ],
        )
        return self._image_reply(document)

    def build_provider_report(self) -> BotReply:
        """Builds an image that lists model counts for all providers.

        Returns:
            Feishu-ready image reply.
        """

        models = self.client.fetch_models()
        counts = provider_counts(models)
        document = ReportDocument(
            title="可用提供商",
            blocks=[
                TableBlock(
                    "摘要",
                    ["模型总数", "提供商数"],
                    [[str(len(models)), str(len(counts))]],
                ),
                TableBlock(
                    "提供商模型数",
                    ["提供商", "模型数", "提供商", "模型数"],
                    format_provider_count_grid_rows(counts, limit=None),
                ),
                TextBlock(
                    "查询示例",
                    ["发送“provider <提供商>”查看模型列表，例如：provider openai"],
                ),
            ],
        )
        return self._image_reply(document)

    def build_provider_models_report(
        self,
        provider: str,
        limit: int = 30,
    ) -> BotReply:
        """Builds an image listing models for a single provider.

        Args:
            provider: Provider name requested by the user.
            limit: Maximum number of provider models to include inline.

        Returns:
            Feishu-ready image reply or a validation message.
        """

        provider = provider.strip().lower()
        if not provider:
            return BotReply.text("请提供提供商名称，例如：provider openai")

        models = [
            item
            for item in self.client.fetch_models()
            if item.provider.lower() == provider
        ]
        if not models:
            return BotReply.text(
                f"未找到提供商：{provider}\n点击菜单“可用提供商”查看可用提供商。"
            )

        # Show the newest models first while keeping equal timestamps deterministic.
        models.sort(
            key=lambda item: (item.released_at or 0, item.id),
            reverse=True,
        )
        shown_count = min(len(models), limit)
        note = ""
        if len(models) > limit:
            note = f"仅展示最新 {limit} 条，还有 {len(models) - limit} 个模型未展示。"

        document = ReportDocument(
            title=f"提供商：{models[0].provider}",
            blocks=[
                TableBlock(
                    "提供商摘要",
                    ["提供商", "模型数", "展示数量"],
                    [
                        [
                            models[0].provider,
                            str(len(models)),
                            f"{shown_count}/{len(models)}",
                        ]
                    ],
                ),
                TableBlock(
                    "模型列表",
                    ["模型", "发布", "输入", "输出", "缓存"],
                    format_provider_models_rows(models[:limit]),
                    note=note,
                ),
            ],
        )
        return self._image_reply(document)

    def _image_reply(self, document: ReportDocument) -> BotReply:
        """Renders a structured report document as a bot image reply."""

        return BotReply.image(self.renderer.render(document))


def format_provider_models_rows(models: list[OfoxModel]) -> list[list[str]]:
    """Formats provider models as report table rows.

    Args:
        models: Provider models ordered for display.

    Returns:
        Table rows for a provider model report.
    """

    return [
        [
            model.name or model.id,
            format_released_at(model.released_at),
            price_per_million(model.input_price),
            price_per_million(model.output_price),
            price_per_million(model.cache_read_price),
        ]
        for model in models
    ]


def format_provider_count_grid_rows(
    counts: dict[str, int],
    limit: int | None,
) -> list[list[str]]:
    """Formats provider counts into two vertically sorted provider/count groups.

    Args:
        counts: Provider counts already ordered for display.
        limit: Optional maximum number of providers to include.

    Returns:
        Four-column table rows for provider counts.
    """

    items = list(counts.items())
    if limit is not None:
        items = items[:limit]
    if not items:
        return [["暂无提供商", "0", "", ""]]

    left_items = items[: (len(items) + 1) // 2]
    right_items = items[(len(items) + 1) // 2 :]

    rows: list[list[str]] = []
    for index, (left_provider, left_count) in enumerate(left_items):
        row = [left_provider, str(left_count)]
        if index < len(right_items):
            right_provider, right_count = right_items[index]
            row.extend([right_provider, str(right_count)])
        else:
            row.extend(["", ""])
        rows.append(row)
    return rows


def format_new_model_rows(
    models: list[OfoxModel],
    *,
    limit: int,
    baseline_created: bool,
) -> list[list[str]]:
    """Formats newly detected models as report table rows.

    Args:
        models: Newly detected models ordered for display.
        limit: Maximum number of models to include.
        baseline_created: Whether this report created the initial baseline.

    Returns:
        Table rows for the new model section.
    """

    if baseline_created:
        return [["首次运行", "-", "-", "-", "-"]]
    if not models:
        return [["无新增", "-", "-", "-", "-"]]
    return [
        [
            model.name or model.id,
            model.provider,
            price_per_million(model.input_price),
            price_per_million(model.output_price),
            price_per_million(model.cache_read_price),
        ]
        for model in models[:limit]
    ]


def format_released_at(value: int | None) -> str:
    """Formats an upstream release timestamp for tables.

    Args:
        value: Optional release timestamp.

    Returns:
        Date text or ``"-"``.
    """

    if value is None:
        return "-"
    released_at = datetime.fromtimestamp(value, UTC).astimezone(BEIJING_TZ)
    return released_at.strftime("%y-%m-%d")


def price_per_million(value: str | None) -> str:
    """Formats a per-token price as dollars per million tokens.

    Args:
        value: Raw per-token price from the upstream model payload.

    Returns:
        Human-readable price, ``"-"`` for missing values, or the raw value when
        it cannot be parsed as a decimal.
    """

    if value in (None, ""):
        return "-"
    try:
        # Upstream prices are per token; reports use the easier per-million unit.
        price = Decimal(str(value)) * Decimal("1000000")
    except InvalidOperation:
        return str(value)
    return f"${price.normalize():f}/M"


def format_time(value: str) -> str:
    """Formats an ISO timestamp as Beijing time for report output.

    Args:
        value: ISO timestamp string.

    Returns:
        Short timestamp in Beijing time.
    """

    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(BEIJING_TZ).strftime("%y-%m-%d %H:%M")
