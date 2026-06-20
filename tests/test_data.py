import sqlite3
from pathlib import Path

import pytest

from app.ofox_client import (
    normalize_model,
    normalize_models_payload,
    provider_from_model_id,
)
from app.reports import price_per_million
from app.repository import ModelRepository

from tests.helpers import model


# ---- ofox_client ----


def test_normalize_models_payload_sorts_and_extracts_fields() -> None:
    models = normalize_models_payload(
        {
            "data": [
                {
                    "id": "openai/gpt-4.1",
                    "name": "",
                    "created": "1710000000",
                    "pricing": {
                        "prompt": "0.000002",
                        "completion": "0.000008",
                        "input_cache_read": "0.0000005",
                    },
                },
                {
                    "id": "anthropic/claude",
                    "created": None,
                },
            ]
        }
    )

    assert [model.id for model in models] == ["anthropic/claude", "openai/gpt-4.1"]
    openai = models[1]
    assert openai.name == "openai/gpt-4.1"
    assert openai.provider == "openai"
    assert openai.released_at == 1710000000
    assert openai.input_price == "0.000002"
    assert openai.output_price == "0.000008"
    assert openai.cache_read_price == "0.0000005"


def test_normalize_payload_requires_data_list() -> None:
    with pytest.raises(ValueError, match="missing data list"):
        normalize_models_payload({"data": {}})


def test_normalize_model_requires_id() -> None:
    with pytest.raises(ValueError, match="model id is empty"):
        normalize_model({"name": "missing id"})


def test_provider_and_price_formatting() -> None:
    assert provider_from_model_id("openai/gpt-4.1") == "openai"
    assert provider_from_model_id("no-provider") == "unknown"
    assert price_per_million("0.000002") == "$2/M"
    assert price_per_million("0.0000005") == "$0.5/M"
    assert price_per_million(None) == "-"
    assert price_per_million("not-a-number") == "not-a-number"


# ---- repository ----


def test_sync_models_creates_baseline_and_marks_only_new_later(tmp_path: Path) -> None:
    db_path = tmp_path / "ofox.sqlite3"
    repo = ModelRepository(db_path)
    first = model("openai/gpt-4.1", released_at=1, input_price=None, output_price=None)
    second = model("openai/gpt-4.2", released_at=2, input_price=None, output_price=None)

    result = repo.sync_models([first], checked_at="2026-01-01T00:00:00+00:00")
    assert result.baseline_created is True
    assert result.new_models == []
    assert result.total_count == 1
    assert result.provider_counts == {"openai": 1}

    result = repo.sync_models([first], checked_at="2026-01-02T00:00:00+00:00")
    assert result.baseline_created is False
    assert result.new_models == []

    result = repo.sync_models([first, second], checked_at="2026-01-03T00:00:00+00:00")
    assert result.baseline_created is False
    assert [item.id for item in result.new_models] == ["openai/gpt-4.2"]

    result = repo.sync_models([first, second], checked_at="2026-01-04T00:00:00+00:00")
    assert result.new_models == []

    with sqlite3.connect(db_path) as conn:
        model_count = conn.execute("SELECT COUNT(*) FROM models").fetchone()[0]
        table_names = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            ).fetchall()
        ]
        model_columns = [
            row[1] for row in conn.execute("PRAGMA table_info(models)").fetchall()
        ]

    assert model_count == 2
    assert table_names == ["models", "watched_models"]
    assert model_columns == [
        "id",
        "name",
        "provider",
        "released_at",
        "input_price",
        "output_price",
        "cache_read_price",
    ]


def test_watched_models_crud_is_global_and_sorted(tmp_path: Path) -> None:
    repo = ModelRepository(tmp_path / "ofox.sqlite3")

    assert repo.list_watched_models() == []
    assert repo.add_watched_model("openai/gpt-4.1") is True
    assert repo.add_watched_model("anthropic/claude-3.7") is True
    assert repo.add_watched_model("openai/gpt-4.1") is False
    assert repo.list_watched_models() == [
        "anthropic/claude-3.7",
        "openai/gpt-4.1",
    ]

    assert repo.remove_watched_model("missing/model") is False
    assert repo.remove_watched_model("openai/gpt-4.1") is True
    assert repo.list_watched_models() == ["anthropic/claude-3.7"]
    assert repo.clear_watched_models() == 1
    assert repo.list_watched_models() == []
