"""Application configuration loading and validation."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
OFOX_MODELS_API_URL = "https://api.ofox.ai/v1/models"
OFOX_DB_PATH = BASE_DIR / "data" / "ofox.sqlite3"
VALID_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}


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
        log_level: Validated Python logging level name.
    """

    feishu_app_id: str
    feishu_app_secret: str
    ofox_models_api_url: str
    ofox_db_path: Path
    log_level: str


def load_config(
    *,
    env_file: Path | None = None,
    environ: Mapping[str, str] | None = None,
    require_feishu: bool = True,
) -> AppConfig:
    """Loads and validates runtime configuration.

    Args:
        env_file: Optional dotenv file to load when ``environ`` is not provided.
        environ: Optional environment mapping for tests or custom callers.
        require_feishu: Whether Feishu credentials must be present.

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
    if require_feishu and not feishu_app_id:
        missing.append("FEISHU_APP_ID")
    if require_feishu and not feishu_app_secret:
        missing.append("FEISHU_APP_SECRET")
    if missing:
        # Group missing variables into one error so startup feedback is actionable.
        raise ConfigurationError(
            "Missing required environment variables: " + ", ".join(missing)
        )

    log_level = (_get_env(source, "LOG_LEVEL") or "INFO").upper()
    if log_level not in VALID_LOG_LEVELS:
        raise ConfigurationError(
            "Invalid LOG_LEVEL. Expected one of: " + ", ".join(sorted(VALID_LOG_LEVELS))
        )

    return AppConfig(
        feishu_app_id=feishu_app_id,
        feishu_app_secret=feishu_app_secret,
        ofox_models_api_url=OFOX_MODELS_API_URL,
        ofox_db_path=OFOX_DB_PATH,
        log_level=log_level,
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
