"""Report builders for model summaries sent to Feishu."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol
from zoneinfo import ZoneInfo

from app.models import OfoxModel
from app.report_rendering import ReportDocument, ReportRenderer, TableBlock
from app.replies import BotReply
from app.repository import ModelRepository, SyncResult

BEIJING_TZ = ZoneInfo("Asia/Shanghai")


class ModelSource(Protocol):
    """Protocol for objects that can fetch normalized Ofox models."""

    def fetch_models(self) -> list[OfoxModel]:
        """Fetches the current model catalog.

        Returns:
            List of normalized Ofox models.
        """

        ...


@dataclass(frozen=True, slots=True)
class ModelReportPayload:
    """Rendered model report plus sync metadata for scheduled delivery."""

    reply: BotReply
    sync_result: SyncResult


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

        return self.build_model_report_payload(limit=limit).reply

    def build_model_report_payload(self, limit: int = 12) -> ModelReportPayload:
        """Builds the main model report and exposes sync metadata.

        Args:
            limit: Maximum number of new models to include inline.

        Returns:
            Rendered reply and repository sync result.
        """

        models = self.client.fetch_models()
        result = self.repository.sync_models(models)
        watched_names = self.repository.list_watched_models()
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
        watched_rows, watched_note = format_watched_model_rows(
            models,
            watched_names,
        )

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
                    "关注模型",
                    ["模型", "发布", "输入", "输出", "缓存"],
                    watched_rows,
                    note=watched_note,
                ),
            ],
        )
        return ModelReportPayload(self._image_reply(document), result)

    def _image_reply(self, document: ReportDocument) -> BotReply:
        """Renders a structured report document as a bot image reply."""

        return BotReply.image(self.renderer.render(document))


def sort_key_model_prices(
    model: OfoxModel,
) -> tuple[bool, Decimal, bool, Decimal, bool, Decimal, str, str]:
    """Builds a deterministic output, input, and cache-read price sort key."""

    output_price = parse_price(model.output_price)
    input_price = parse_price(model.input_price)
    cache_read_price = parse_price(model.cache_read_price)
    # Each missing flag keeps unusable values behind valid prices at that level.
    return (
        output_price is None,
        output_price if output_price is not None else Decimal(0),
        input_price is None,
        input_price if input_price is not None else Decimal(0),
        cache_read_price is None,
        cache_read_price if cache_read_price is not None else Decimal(0),
        (model.name or model.id).casefold(),
        model.id,
    )


def parse_price(value: str | None) -> Decimal | None:
    """Parses an upstream price for sorting, returning ``None`` when unusable."""

    if value in (None, ""):
        return None
    try:
        price = Decimal(str(value))
    except InvalidOperation:
        return None
    return price if price.is_finite() else None


def format_model_rows(models: list[OfoxModel]) -> list[list[str]]:
    """Formats models as report table rows.

    Args:
        models: Models ordered for display.

    Returns:
        Table rows for a model report.
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


def format_watched_model_rows(
    models: list[OfoxModel],
    watched_names: list[str],
) -> tuple[list[list[str]], str]:
    """Formats watched models for the model report table.

    Args:
        models: Latest catalog snapshot.
        watched_names: Stored watched model names.

    Returns:
        Table rows and optional note for missing watched names.
    """

    if not watched_names:
        return [["暂无关注模型", "-", "-", "-", "-"]], ""

    models_by_name = {model.name: model for model in models}
    available_models: list[OfoxModel] = []
    missing_names: list[str] = []
    for model_name in watched_names:
        model = models_by_name.get(model_name)
        if model is None:
            missing_names.append(model_name)
            continue
        available_models.append(model)

    available_models.sort(key=sort_key_model_prices)
    rows = format_model_rows(available_models)

    if not rows:
        rows = [["暂无当前可用的关注模型", "-", "-", "-", "-"]]

    note = ""
    if missing_names:
        shown = "、".join(missing_names[:5])
        extra = "" if len(missing_names) <= 5 else f" 等 {len(missing_names)} 个"
        note = f"未在当前 catalog 中找到：{shown}{extra}"
    return rows, note


def format_new_model_rows(
    models: list[OfoxModel],
    *,
    limit: int,
    baseline_created: bool,
) -> list[list[str]]:
    """Formats newly detected models as report table rows.

    Args:
        models: Newly detected models to order for display.
        limit: Maximum number of models to include.
        baseline_created: Whether this report created the initial baseline.

    Returns:
        Table rows for the new model section.
    """

    if baseline_created:
        return [["首次运行", "-", "-", "-", "-"]]
    if not models:
        return [["无新增", "-", "-", "-", "-"]]
    ordered_models = sorted(models, key=sort_key_model_prices)
    return [
        [
            model.name or model.id,
            model.provider,
            price_per_million(model.input_price),
            price_per_million(model.output_price),
            price_per_million(model.cache_read_price),
        ]
        for model in ordered_models[:limit]
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
