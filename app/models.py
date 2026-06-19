"""Domain models and small helpers for normalized Ofox model data."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OfoxModel:
    """Normalized model metadata used throughout reports and persistence.

    Attributes:
        id: Stable model identifier returned by the Ofox API.
        name: Display name for Feishu reports.
        provider: Provider derived from the model id prefix.
        released_at: Model release timestamp from Ofox ``created``, if available.
        input_price: Prompt/input price per token as a string.
        output_price: Completion/output price per token as a string.
        cache_read_price: Cached input read price per token as a string.
    """

    id: str
    name: str
    provider: str
    released_at: int | None
    input_price: str | None
    output_price: str | None
    cache_read_price: str | None


def provider_from_model_id(model_id: str) -> str:
    """Extracts the provider prefix from a slash-delimited model id.

    Args:
        model_id: Upstream model id, commonly formatted as
            ``provider/model-name``.

    Returns:
        Provider prefix, or ``"unknown"`` when the id has no usable prefix.
    """

    cleaned = model_id.strip()
    if "/" not in cleaned:
        return "unknown"

    # Empty prefixes should not leak into grouping output.
    provider = cleaned.split("/", 1)[0].strip()
    return provider or "unknown"


def provider_counts(models: Iterable[OfoxModel]) -> dict[str, int]:
    """Counts models by provider in report-friendly order.

    Args:
        models: Normalized models to group.

    Returns:
        Provider counts sorted by descending count, then provider name.
    """

    counts: dict[str, int] = {}
    for model in models:
        counts[model.provider] = counts.get(model.provider, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def sort_key_released_at(model: OfoxModel) -> tuple[int, str]:
    """Builds a deterministic sort key for newest-first model lists.

    Args:
        model: Model to order.

    Returns:
        Tuple containing release timestamp and model id.
    """

    return (model.released_at or 0, model.id)
