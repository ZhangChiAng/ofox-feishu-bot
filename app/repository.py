"""SQLite persistence for Ofox model snapshots."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.models import OfoxModel, provider_counts, sort_key_released_at


@dataclass(frozen=True)
class SyncResult:
    """Result of synchronizing a model snapshot.

    Attributes:
        total_count: Number of models in the current snapshot.
        provider_counts: Current model counts grouped by provider.
        new_models: Models not seen before this sync.
        baseline_created: Whether this sync created the initial baseline.
        checked_at: UTC timestamp for this check, formatted by the report layer.
    """

    total_count: int
    provider_counts: dict[str, int]
    new_models: list[OfoxModel]
    baseline_created: bool
    checked_at: str


class ModelRepository:
    """Stores model snapshots and detects newly seen models."""

    def __init__(self, db_path: Path | str) -> None:
        """Initializes the repository.

        Args:
            db_path: SQLite database path, or ``:memory:`` for tests.
        """

        self.db_path = Path(db_path)

    def sync_models(
        self,
        models: Iterable[OfoxModel],
        *,
        checked_at: str | None = None,
    ) -> SyncResult:
        """Synchronizes the current model list into SQLite.

        Args:
            models: Current normalized model snapshot.
            checked_at: Optional timestamp override for deterministic tests.

        Returns:
            Summary of the sync, including models newly seen after the baseline.
        """

        model_list = sorted(list(models), key=lambda item: item.id)
        checked_at = checked_at or utc_now()

        conn = self.connect()
        try:
            self.init_db(conn)
            known_ids = self.get_known_model_ids(conn)
            baseline_created = not known_ids
            # The first observed catalog becomes the baseline instead of an alert.
            new_models = (
                []
                if baseline_created
                else [item for item in model_list if item.id not in known_ids]
            )
            self.upsert_models(conn, model_list)
        finally:
            conn.close()

        return SyncResult(
            total_count=len(model_list),
            provider_counts=provider_counts(model_list),
            new_models=sorted(new_models, key=sort_key_released_at, reverse=True),
            baseline_created=baseline_created,
            checked_at=checked_at,
        )

    def connect(self) -> sqlite3.Connection:
        """Opens a SQLite connection configured for row access by name.

        Returns:
            SQLite connection with ``sqlite3.Row`` row factory.
        """

        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def init_db(conn: sqlite3.Connection) -> None:
        """Creates repository tables if they do not exist.

        Args:
            conn: Open SQLite connection.
        """

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS models (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                provider TEXT NOT NULL,
                released_at INTEGER,
                input_price TEXT,
                output_price TEXT,
                cache_read_price TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS watched_models (
                model_name TEXT PRIMARY KEY
            )
            """
        )
        conn.commit()

    @staticmethod
    def get_known_model_ids(conn: sqlite3.Connection) -> set[str]:
        """Reads all model ids already stored in the repository.

        Args:
            conn: Open SQLite connection.

        Returns:
            Set of known model ids.
        """

        return {row["id"] for row in conn.execute("SELECT id FROM models")}

    @staticmethod
    def upsert_models(
        conn: sqlite3.Connection,
        models: list[OfoxModel],
    ) -> None:
        """Inserts or updates model rows for the latest snapshot.

        Args:
            conn: Open SQLite connection.
            models: Models to persist.
        """

        conn.executemany(
            """
            INSERT INTO models (
                id, name, provider, released_at, input_price,
                output_price, cache_read_price
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                provider = excluded.provider,
                released_at = excluded.released_at,
                input_price = excluded.input_price,
                output_price = excluded.output_price,
                cache_read_price = excluded.cache_read_price
            """,
            [
                (
                    model.id,
                    model.name,
                    model.provider,
                    model.released_at,
                    model.input_price,
                    model.output_price,
                    model.cache_read_price,
                )
                for model in models
            ],
        )
        conn.commit()

    def add_watched_model(self, model_name: str) -> bool:
        """Adds a model name to the global watch list.

        Args:
            model_name: Exact ``OfoxModel.name`` value to watch.

        Returns:
            ``True`` when inserted, ``False`` when it was already watched.
        """

        conn = self.connect()
        try:
            self.init_db(conn)
            cursor = conn.execute(
                "INSERT OR IGNORE INTO watched_models (model_name) VALUES (?)",
                (model_name,),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def remove_watched_model(self, model_name: str) -> bool:
        """Removes a model name from the global watch list.

        Args:
            model_name: Exact ``OfoxModel.name`` value to remove.

        Returns:
            ``True`` when a row was removed, otherwise ``False``.
        """

        conn = self.connect()
        try:
            self.init_db(conn)
            cursor = conn.execute(
                "DELETE FROM watched_models WHERE model_name = ?",
                (model_name,),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def list_watched_models(self) -> list[str]:
        """Lists watched model names in deterministic display order."""

        conn = self.connect()
        try:
            self.init_db(conn)
            return [
                row["model_name"]
                for row in conn.execute(
                    "SELECT model_name FROM watched_models "
                    "ORDER BY model_name COLLATE NOCASE, model_name"
                )
            ]
        finally:
            conn.close()

    def clear_watched_models(self) -> int:
        """Removes all watched model names.

        Returns:
            Number of removed watch entries.
        """

        conn = self.connect()
        try:
            self.init_db(conn)
            cursor = conn.execute("DELETE FROM watched_models")
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()


def utc_now() -> str:
    """Returns the current UTC timestamp for repository records.

    The returned ISO-8601 string includes a ``+00:00`` UTC offset.

    Returns:
        Timezone-aware ISO-8601 UTC timestamp without fractional seconds.
    """

    return datetime.now(UTC).isoformat(timespec="seconds")
