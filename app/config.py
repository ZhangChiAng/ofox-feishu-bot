"""Application configuration loading and validation."""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
OFOX_MODELS_API_URL = "https://api.ofox.ai/v1/models"
OFOX_DB_PATH = BASE_DIR / "var" / "ofox.sqlite3"
VALID_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
DAILY_REPORT_TIME_PATTERN = re.compile(r"^(?P<hour>[01]\d|2[0-3]):(?P<minute>[0-5]\d)$")


class ConfigurationError(RuntimeError):
    """Raised when required runtime configuration is missing or invalid."""


@dataclass(frozen=True)
class AppConfig:
    """Runtime configuration for the Feishu worker.

    Attributes:
        feishu_app_id: Feishu application id.
        feishu_app_secret: Feishu application secret.
        ofox_models_api_url: URL for the Ofox models API.
        ofox_db_path: SQLite database path.
        chinese_font_path: Chinese-capable font file used to render report images.
        log_level: Validated Python logging level name.
        daily_report_time: Local time for the daily proactive report check.
        daily_report_timezone: Timezone used to interpret ``daily_report_time``.
        feishu_report_receive_id_type: Feishu receiver id type for proactive pushes.
        feishu_report_receive_id: Feishu receiver id for proactive pushes.
        feishu_message_max_age_seconds: Maximum age for inbound message events.
    """

    feishu_app_id: str
    feishu_app_secret: str
    ofox_models_api_url: str
    ofox_db_path: Path
    chinese_font_path: Path
    log_level: str
    daily_report_time: time
    daily_report_timezone: ZoneInfo
    feishu_report_receive_id_type: str
    feishu_report_receive_id: str
    feishu_message_max_age_seconds: int


def load_config(
    *,
    env_file: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> AppConfig:
    """Loads and validates runtime configuration.

    Args:
        env_file: Optional dotenv file to load when ``environ`` is not provided.
        environ: Optional environment mapping for tests or custom callers.

    Returns:
        Validated application configuration.

    Raises:
        ConfigurationError: If required values are missing or invalid.
    """

    if environ is None:
        # Production reads from .env plus process env; tests pass an explicit mapping.
        load_dotenv(env_file or BASE_DIR / ".env", override=False)
        source: Mapping[str, str] = os.environ
    else:
        source = environ

    feishu_app_id = _get_env(source, "FEISHU_APP_ID")
    feishu_app_secret = _get_env(source, "FEISHU_APP_SECRET")

    missing: list[str] = []
    if not feishu_app_id:
        missing.append("FEISHU_APP_ID")
    if not feishu_app_secret:
        missing.append("FEISHU_APP_SECRET")
    if missing:
        # Group missing variables into one error so startup feedback is actionable.
        raise ConfigurationError(
            "Missing required environment variables: " + ", ".join(missing)
        )

    chinese_font_path = _get_required_file(source, "CHINESE_FONT_PATH")

    log_level = (_get_env(source, "LOG_LEVEL") or "INFO").upper()
    if log_level not in VALID_LOG_LEVELS:
        raise ConfigurationError(
            "Invalid LOG_LEVEL. Expected one of: " + ", ".join(sorted(VALID_LOG_LEVELS))
        )

    daily_report_time = _get_daily_report_time(source)
    daily_report_timezone = _get_daily_report_timezone(source)
    feishu_message_max_age_seconds = _get_positive_int(
        source,
        "FEISHU_MESSAGE_MAX_AGE_SECONDS",
        120,
    )

    return AppConfig(
        feishu_app_id=feishu_app_id,
        feishu_app_secret=feishu_app_secret,
        ofox_models_api_url=OFOX_MODELS_API_URL,
        ofox_db_path=OFOX_DB_PATH,
        chinese_font_path=chinese_font_path,
        log_level=log_level,
        daily_report_time=daily_report_time,
        daily_report_timezone=daily_report_timezone,
        feishu_report_receive_id_type=_get_env(source, "FEISHU_REPORT_RECEIVE_ID_TYPE"),
        feishu_report_receive_id=_get_env(source, "FEISHU_REPORT_RECEIVE_ID"),
        feishu_message_max_age_seconds=feishu_message_max_age_seconds,
    )


def _get_env(source: Mapping[str, str], key: str) -> str:
    """Reads and trims one environment value.

    Args:
        source: Environment mapping.
        key: Variable name.

    Returns:
        Trimmed string value, or an empty string when missing.
    """

    value = source.get(key, "")
    return str(value).strip() if value is not None else ""


def _get_required_file(source: Mapping[str, str], key: str) -> Path:
    """Reads and validates a required file path environment value.

    Args:
        source: Environment mapping.
        key: Variable name.

    Returns:
        Existing file path.

    Raises:
        ConfigurationError: If the value is missing or does not point to a file.
    """

    value = _get_env(source, key)
    if not value:
        raise ConfigurationError(f"Missing required environment variable: {key}")

    path = Path(value)
    if not path.is_absolute():
        path = BASE_DIR / path
    if not path.is_file():
        raise ConfigurationError(f"{key} must point to an existing file")
    return path


def _get_daily_report_time(source: Mapping[str, str]) -> time:
    """Reads and validates ``DAILY_REPORT_TIME`` as ``HH:MM``."""

    value = _get_env(source, "DAILY_REPORT_TIME") or "12:30"
    match = DAILY_REPORT_TIME_PATTERN.match(value)
    if not match:
        raise ConfigurationError(
            "Invalid DAILY_REPORT_TIME. Expected HH:MM, e.g. 12:30"
        )
    return time(
        hour=int(match.group("hour")),
        minute=int(match.group("minute")),
    )


def _get_daily_report_timezone(source: Mapping[str, str]) -> ZoneInfo:
    """Reads and validates ``DAILY_REPORT_TIMEZONE``."""

    value = _get_env(source, "DAILY_REPORT_TIMEZONE") or "Asia/Shanghai"
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ConfigurationError(f"Invalid DAILY_REPORT_TIMEZONE: {value}") from exc


def _get_positive_int(source: Mapping[str, str], key: str, default: int) -> int:
    """Reads and validates a positive integer environment value."""

    value = _get_env(source, key)
    if not value:
        return default

    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigurationError(f"{key} must be a positive integer") from exc

    if parsed <= 0:
        raise ConfigurationError(f"{key} must be a positive integer")
    return parsed
