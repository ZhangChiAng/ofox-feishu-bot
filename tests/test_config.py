from pathlib import Path

import pytest

from app.config import (
    ConfigurationError,
    OFOX_DB_PATH,
    OFOX_MODELS_API_URL,
    load_config,
)


def font_env(tmp_path: Path) -> dict[str, str]:
    font_path = tmp_path / "report.ttf"
    font_path.write_bytes(b"font")
    return {"CHINESE_FONT_PATH": str(font_path)}


def test_missing_feishu_config_reports_names_without_values() -> None:
    env = {
        "FEISHU_APP_ID": "placeholder_app_id",
        "FEISHU_APP_SECRET": "",
        "CHINESE_FONT_PATH": __file__,
    }

    with pytest.raises(ConfigurationError) as exc_info:
        load_config(environ=env)

    message = str(exc_info.value)
    assert "FEISHU_APP_SECRET" in message
    assert "placeholder_app_id" not in message


def test_defaults_are_loaded_without_requiring_feishu(tmp_path: Path) -> None:
    config = load_config(environ=font_env(tmp_path), require_feishu=False)

    assert config.feishu_app_id == ""
    assert config.feishu_app_secret == ""
    assert config.ofox_models_api_url == OFOX_MODELS_API_URL
    assert config.ofox_db_path == OFOX_DB_PATH
    assert config.chinese_font_path == tmp_path / "report.ttf"
    assert config.log_level == "INFO"


def test_ofox_api_and_db_settings_are_internal(tmp_path: Path) -> None:
    env = font_env(tmp_path) | {
        "OFOX_MODELS_API_URL": "https://example.test/models",
        "OFOX_DB_PATH": "var/models.sqlite3",
    }
    config = load_config(
        environ=env,
        require_feishu=False,
    )

    assert config.ofox_models_api_url == OFOX_MODELS_API_URL
    assert config.ofox_db_path == OFOX_DB_PATH


def test_invalid_log_level_reports_allowed_names(tmp_path: Path) -> None:
    env = font_env(tmp_path) | {"LOG_LEVEL": "verbose"}
    with pytest.raises(ConfigurationError) as exc_info:
        load_config(environ=env, require_feishu=False)

    assert "Invalid LOG_LEVEL" in str(exc_info.value)
    assert "verbose" not in str(exc_info.value)


def test_missing_chinese_font_path_is_invalid() -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        load_config(environ={}, require_feishu=False)

    assert "CHINESE_FONT_PATH" in str(exc_info.value)


def test_nonexistent_chinese_font_path_is_invalid(tmp_path: Path) -> None:
    missing_font = tmp_path / "missing.ttf"

    with pytest.raises(ConfigurationError) as exc_info:
        load_config(
            environ={"CHINESE_FONT_PATH": str(missing_font)},
            require_feishu=False,
        )

    assert "CHINESE_FONT_PATH" in str(exc_info.value)
