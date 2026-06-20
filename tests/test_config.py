from datetime import time
from pathlib import Path

import pytest

from app.config import (
    ConfigurationError,
    OFOX_DB_PATH,
    OFOX_MODELS_API_URL,
    load_config,
)


def base_env(tmp_path: Path) -> dict[str, str]:
    font_path = tmp_path / "report.ttf"
    font_path.write_bytes(b"font")
    return {
        "FEISHU_APP_ID": "test_app_id",
        "FEISHU_APP_SECRET": "test_secret",
        "CHINESE_FONT_PATH": str(font_path),
    }


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


def test_defaults_are_loaded(tmp_path: Path) -> None:
    config = load_config(environ=base_env(tmp_path))

    assert config.feishu_app_id == "test_app_id"
    assert config.feishu_app_secret == "test_secret"
    assert config.ofox_models_api_url == OFOX_MODELS_API_URL
    assert config.ofox_db_path == OFOX_DB_PATH
    assert config.chinese_font_path == tmp_path / "report.ttf"
    assert config.log_level == "INFO"
    assert config.daily_report_time == time(12, 30)
    assert config.daily_report_timezone.key == "Asia/Shanghai"
    assert config.feishu_report_receive_id_type == ""
    assert config.feishu_report_receive_id == ""
    assert config.feishu_message_max_age_seconds == 120


def test_daily_report_settings_are_loaded(tmp_path: Path) -> None:
    env = base_env(tmp_path) | {
        "DAILY_REPORT_TIME": "08:05",
        "DAILY_REPORT_TIMEZONE": "UTC",
        "FEISHU_REPORT_RECEIVE_ID_TYPE": "chat_id",
        "FEISHU_REPORT_RECEIVE_ID": "chat-id",
    }

    config = load_config(environ=env)

    assert config.daily_report_time == time(8, 5)
    assert config.daily_report_timezone.key == "UTC"
    assert config.feishu_report_receive_id_type == "chat_id"
    assert config.feishu_report_receive_id == "chat-id"


def test_message_max_age_setting_is_loaded(tmp_path: Path) -> None:
    env = base_env(tmp_path) | {"FEISHU_MESSAGE_MAX_AGE_SECONDS": "300"}

    config = load_config(environ=env)

    assert config.feishu_message_max_age_seconds == 300


@pytest.mark.parametrize("value", ["0", "-1", "secret-token"])
def test_invalid_message_max_age_is_rejected_without_value(
    tmp_path: Path,
    value: str,
) -> None:
    env = base_env(tmp_path) | {"FEISHU_MESSAGE_MAX_AGE_SECONDS": value}

    with pytest.raises(ConfigurationError) as exc_info:
        load_config(environ=env)

    message = str(exc_info.value)
    assert "FEISHU_MESSAGE_MAX_AGE_SECONDS" in message
    assert value not in message


@pytest.mark.parametrize("value", ["8:05", "24:00", "12:60", "noon"])
def test_invalid_daily_report_time_is_rejected(
    tmp_path: Path,
    value: str,
) -> None:
    env = base_env(tmp_path) | {"DAILY_REPORT_TIME": value}

    with pytest.raises(ConfigurationError) as exc_info:
        load_config(environ=env)

    assert "DAILY_REPORT_TIME" in str(exc_info.value)


def test_invalid_daily_report_timezone_is_rejected(tmp_path: Path) -> None:
    env = base_env(tmp_path) | {"DAILY_REPORT_TIMEZONE": "Not/AZone"}

    with pytest.raises(ConfigurationError) as exc_info:
        load_config(environ=env)

    assert "DAILY_REPORT_TIMEZONE" in str(exc_info.value)


def test_ofox_api_and_db_settings_are_internal(tmp_path: Path) -> None:
    env = base_env(tmp_path) | {
        "OFOX_MODELS_API_URL": "https://example.test/models",
        "OFOX_DB_PATH": "var/models.sqlite3",
    }
    config = load_config(environ=env)

    assert config.ofox_models_api_url == OFOX_MODELS_API_URL
    assert config.ofox_db_path == OFOX_DB_PATH


def test_invalid_log_level_reports_allowed_names(tmp_path: Path) -> None:
    env = base_env(tmp_path) | {"LOG_LEVEL": "verbose"}
    with pytest.raises(ConfigurationError) as exc_info:
        load_config(environ=env)

    assert "Invalid LOG_LEVEL" in str(exc_info.value)
    assert "verbose" not in str(exc_info.value)


def test_missing_chinese_font_path_is_invalid() -> None:
    env = {"FEISHU_APP_ID": "id", "FEISHU_APP_SECRET": "secret"}

    with pytest.raises(ConfigurationError) as exc_info:
        load_config(environ=env)

    assert "CHINESE_FONT_PATH" in str(exc_info.value)


def test_nonexistent_chinese_font_path_is_invalid(tmp_path: Path) -> None:
    missing_font = tmp_path / "missing.ttf"
    env = {
        "FEISHU_APP_ID": "id",
        "FEISHU_APP_SECRET": "secret",
        "CHINESE_FONT_PATH": str(missing_font),
    }

    with pytest.raises(ConfigurationError) as exc_info:
        load_config(environ=env)

    assert "CHINESE_FONT_PATH" in str(exc_info.value)
