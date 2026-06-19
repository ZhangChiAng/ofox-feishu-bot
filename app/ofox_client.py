"""Ofox API client and payload normalization helpers."""

from __future__ import annotations

from typing import Any

import httpx

from app.config import OFOX_MODELS_API_URL
from app.models import OfoxModel, provider_from_model_id


class OfoxClient:
    """Fetches model metadata from the Ofox models API."""

    def __init__(
        self,
        api_url: str = OFOX_MODELS_API_URL,
        *,
        timeout: float = 20.0,
        http_client: Any | None = None,
    ) -> None:
        """Initializes the client.

        Args:
            api_url: Ofox models API URL.
            timeout: Request timeout in seconds.
            http_client: Optional object with a ``get`` method for tests.
        """

        self.api_url = api_url
        self.timeout = timeout
        self._http_client = http_client

    def fetch_models(self) -> list[OfoxModel]:
        """Fetches and normalizes models from Ofox.

        Returns:
            Sorted list of normalized models.

        Raises:
            httpx.HTTPStatusError: If Ofox returns an error status.
            ValueError: If the response payload is malformed.
        """

        # A lightweight injectable client keeps tests from needing network access.
        request = self._http_client.get if self._http_client is not None else httpx.get
        response = request(
            self.api_url,
            headers={"Accept": "application/json", "User-Agent": "ofox-feishu-bot/0.1"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return normalize_models_payload(response.json())


def normalize_models_payload(payload: dict[str, Any]) -> list[OfoxModel]:
    """Normalizes the top-level Ofox models API response.

    Args:
        payload: Decoded JSON response from Ofox.

    Returns:
        Models sorted by id for deterministic reports and database writes.

    Raises:
        ValueError: If the payload does not contain a list at ``data``.
    """

    raw_models = payload.get("data")
    if not isinstance(raw_models, list):
        raise ValueError("Ofox models API response missing data list")

    # Ignore non-object entries because they cannot be normalized into a model.
    models = [normalize_model(item) for item in raw_models if isinstance(item, dict)]
    return sorted(models, key=lambda item: item.id)


def normalize_model(raw: dict[str, Any]) -> OfoxModel:
    """Normalizes one Ofox model payload.

    Args:
        raw: Raw model dictionary from the Ofox API.

    Returns:
        Normalized Ofox model.

    Raises:
        ValueError: If the model has no usable id.
    """

    model_id = str(raw.get("id") or "").strip()
    if not model_id:
        raise ValueError("model id is empty")

    pricing = _dict_value(raw, "pricing")
    return OfoxModel(
        id=model_id,
        name=str(raw.get("name") or model_id).strip() or model_id,
        provider=provider_from_model_id(model_id),
        released_at=_to_int(raw.get("created")),
        input_price=_to_optional_str(pricing.get("prompt")),
        output_price=_to_optional_str(pricing.get("completion")),
        cache_read_price=_to_optional_str(pricing.get("input_cache_read")),
    )


def _dict_value(raw: dict[str, Any], key: str) -> dict[str, Any]:
    """Returns a nested dictionary value or an empty dictionary.

    Args:
        raw: Source dictionary.
        key: Key to read.

    Returns:
        Nested dictionary for ``key`` when present and typed correctly.
    """

    value = raw.get(key)
    return value if isinstance(value, dict) else {}


def _to_int(value: Any) -> int | None:
    """Converts a value to int while treating invalid input as missing.

    Args:
        value: Value to convert.

    Returns:
        Integer value, or ``None`` when conversion is not possible.
    """

    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_optional_str(value: Any) -> str | None:
    """Converts a value to string while preserving missing values.

    Args:
        value: Value to convert.

    Returns:
        String value, or ``None`` when the input is missing.
    """

    if value is None:
        return None
    return str(value)
