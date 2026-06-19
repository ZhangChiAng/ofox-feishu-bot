"""Report builders for model summaries sent to Feishu."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol
from zoneinfo import ZoneInfo

from app.models import OfoxModel, provider_counts
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

    def __init__(self, client: ModelSource, repository: ModelRepository) -> None:
        """Initializes the report service.

        Args:
            client: Source used to fetch the current Ofox model catalog.
            repository: Repository used to persist and diff model snapshots.
        """

        self.client = client
        self.repository = repository

    def build_model_report(self, limit: int = 12) -> BotReply:
        """Builds the main model report card and persists the latest snapshot.

        Args:
            limit: Maximum number of new models to include inline.

        Returns:
            Feishu-ready interactive card reply.
        """

        models = self.client.fetch_models()
        result = self.repository.sync_models(models)
        status = "发现新增模型" if result.new_models else "无新增模型"
        if result.baseline_created:
            # The first sync seeds the database; every model would otherwise look new.
            status = "首次运行，已建立本地模型基线"

        summary = markdown_table(
            ["指标", "值"],
            [
                ["检测时间", format_time(result.checked_at)],
                ["模型总数", str(result.total_count)],
                ["新增模型", str(len(result.new_models))],
                ["状态", status],
            ],
        )

        new_models = markdown_table(
            ["名称", "提供商", "输入", "输出", "缓存读取"],
            format_new_model_rows(
                result.new_models,
                limit=limit,
                baseline_created=result.baseline_created,
            ),
        )
        if len(result.new_models) > limit:
            new_models = f"{new_models}\n还有 {len(result.new_models) - limit} 个新增模型未展示。"

        top_providers = markdown_table(
            ["提供商", "模型数"],
            format_provider_count_rows(result.provider_counts, limit=10),
        )

        markdown = "\n\n".join(
            [
                f"**摘要**\n{summary}",
                f"**新增模型**\n{new_models}",
                f"**提供商 Top 10**\n{top_providers}",
                "**操作提示**\n点击菜单“可用提供商”查看提供商/模型数表格；发送“provider <提供商>”查看指定提供商的模型表格。",
            ]
        )
        return BotReply.interactive(build_card("模型报告", markdown))

    def build_provider_report(self) -> BotReply:
        """Builds a card that lists model counts for all providers.

        Returns:
            Feishu-ready interactive card reply.
        """

        models = self.client.fetch_models()
        counts = provider_counts(models)
        summary = markdown_table(
            ["指标", "值"],
            [
                ["模型总数", str(len(models))],
                ["提供商数", str(len(counts))],
            ],
        )
        providers = markdown_table(
            ["提供商", "模型数"],
            format_provider_count_rows(counts, limit=None),
        )
        markdown = "\n\n".join(
            [
                f"**摘要**\n{summary}",
                f"**提供商模型数**\n{providers}",
                "**查询示例**\n发送“provider <提供商>”查看模型列表，例如：provider openai",
            ]
        )
        return BotReply.interactive(build_card("可用提供商", markdown))

    def build_provider_models_report(
        self,
        provider: str,
        limit: int = 30,
    ) -> BotReply:
        """Builds a card listing models for a single provider.

        Args:
            provider: Provider name requested by the user.
            limit: Maximum number of provider models to include inline.

        Returns:
            Feishu-ready interactive card reply or a validation message.
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
        summary = markdown_table(
            ["指标", "值"],
            [
                ["提供商", models[0].provider],
                ["模型数", str(len(models))],
                ["展示数量", f"{shown_count}/{len(models)}"],
            ],
        )
        provider_models = markdown_table(
            ["#", "模型", "发布时间", "输入", "输出", "缓存读取"],
            [
                [
                    str(index),
                    model.name or model.id,
                    format_released_at(model.released_at),
                    price_per_million(model.input_price),
                    price_per_million(model.output_price),
                    price_per_million(model.cache_read_price),
                ]
                for index, model in enumerate(models[:limit], start=1)
            ],
        )
        if len(models) > limit:
            provider_models = f"{provider_models}\n仅展示最新 {limit} 条，还有 {len(models) - limit} 个模型未展示。"

        markdown = "\n\n".join(
            [
                f"**提供商摘要**\n{summary}",
                f"**模型列表**\n{provider_models}",
            ]
        )
        return BotReply.interactive(
            build_card(f"提供商：{models[0].provider}", markdown)
        )


def format_provider_count_rows(
    counts: dict[str, int],
    limit: int | None,
) -> list[list[str]]:
    """Formats provider counts as Markdown table rows.

    Args:
        counts: Provider counts already ordered for display.
        limit: Optional maximum number of providers to include.

    Returns:
        Table rows for provider counts.
    """

    items = list(counts.items())
    if limit is not None:
        items = items[:limit]
    if not items:
        return [["暂无提供商", "0"]]
    return [[provider, str(count)] for provider, count in items]


def format_new_model_rows(
    models: list[OfoxModel],
    *,
    limit: int,
    baseline_created: bool,
) -> list[list[str]]:
    """Formats newly detected models as Markdown table rows.

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


def build_card(title: str, markdown: str) -> dict[str, object]:
    """Builds a Feishu interactive card with Markdown content.

    Args:
        title: Card title shown in the header.
        markdown: Markdown body content.

    Returns:
        Feishu card content.
    """

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": [
            {
                "tag": "markdown",
                "content": markdown,
            }
        ],
    }


def markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    """Formats a Markdown table for Feishu card content.

    Args:
        headers: Table header labels.
        rows: Table rows.

    Returns:
        Markdown table string.
    """

    header = "| " + " | ".join(markdown_cell(item) for item in headers) + " |"
    separator = "| " + " | ".join("---" for _ in headers) + " |"
    body = [
        "| " + " | ".join(markdown_cell(item) for item in row) + " |" for row in rows
    ]
    return "\n".join([header, separator, *body])


def markdown_cell(value: object) -> str:
    """Escapes a value for use inside a Markdown table cell.

    Args:
        value: Cell value.

    Returns:
        Markdown-safe cell text.
    """

    text = str(value).strip()
    if not text:
        return "-"
    return text.replace("\r", " ").replace("\n", " ").replace("|", "\\|")


def format_released_at(value: int | None) -> str:
    """Formats an upstream release timestamp for tables.

    Args:
        value: Optional release timestamp.

    Returns:
        Timestamp text or ``"-"``.
    """

    if value is None:
        return "-"
    return str(value)


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
        Timestamp with a Beijing time suffix.
    """

    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S 北京时间")
