"""SQLite backed profile store for Agastya Assist."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agastya.config import get_settings
from agastya.profile.fields import FIELDS, masked, normalize_field, validate_field


class ProfileStore:
    """Persistent user profile store backed by SQLite."""

    def __init__(
        self,
        user_id: str = "default",
        db_path: Path | str | None = None,
        data_dir: Path | str | None = None,
    ) -> None:
        self.user_id = user_id
        if db_path is not None:
            self.db_path = Path(db_path)
        elif data_dir is not None:
            self.db_path = Path(data_dir) / "profile.db"
        else:
            self.db_path = Path(get_settings().DATA_DIR) / "profile.db"

        self._conn: sqlite3.Connection | None = None
        self._ensure_init()

    def _ensure_init(self) -> None:
        """Ensure parent directory exists and table schema is created."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._get_connection()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profile (
                user_id TEXT NOT NULL,
                field TEXT NOT NULL,
                value TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'user',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, field)
            );
            """
        )
        conn.commit()

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL;")
        return self._conn

    def get(self, field: str) -> str | None:
        """Get the value for a profile field for the current user."""
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT value FROM profile WHERE user_id = ? AND field = ?",
            (self.user_id, field),
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def get_record(self, field: str) -> dict[str, Any] | None:
        """Get full record (value, source, updated_at) for a field."""
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT value, source, updated_at FROM profile WHERE user_id = ? AND field = ?",
            (self.user_id, field),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "value": row[0],
            "source": row[1],
            "updated_at": row[2],
        }

    def set(self, field: str, value: Any, source: str = "user") -> None:
        """Set a profile field value.

        Validates fields that have validators in the field catalog and raises
        ValueError if the value is invalid.
        """
        if value is None:
            raise ValueError(f"Value for field '{field}' cannot be None.")

        val_str = normalize_field(field, value)
        if not val_str:
            raise ValueError(f"Value for field '{field}' cannot be empty.")

        # Validate against catalog if present
        validate_field(field, val_str)

        updated_at = datetime.now(timezone.utc).isoformat()
        conn = self._get_connection()
        conn.execute(
            """
            INSERT INTO profile (user_id, field, value, source, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, field) DO UPDATE SET
                value = excluded.value,
                source = excluded.source,
                updated_at = excluded.updated_at
            """,
            (self.user_id, field, val_str, source, updated_at),
        )
        conn.commit()

    def all(self) -> dict[str, str]:
        """Return all fields and values for the current user."""
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT field, value FROM profile WHERE user_id = ? ORDER BY field",
            (self.user_id,),
        )
        return {row[0]: row[1] for row in cursor.fetchall()}

    def all_records(self) -> dict[str, dict[str, Any]]:
        """Return full records for all fields belonging to current user."""
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT field, value, source, updated_at FROM profile WHERE user_id = ? ORDER BY field",
            (self.user_id,),
        )
        return {
            row[0]: {
                "value": row[1],
                "source": row[2],
                "updated_at": row[3],
            }
            for row in cursor.fetchall()
        }

    def delete(self, field: str) -> bool:
        """Delete a field for the current user. Returns True if deleted."""
        conn = self._get_connection()
        cursor = conn.execute(
            "DELETE FROM profile WHERE user_id = ? AND field = ?",
            (self.user_id, field),
        )
        conn.commit()
        return cursor.rowcount > 0

    def missing(self, required_fields: list[str]) -> list[str]:
        """Return list of fields from required_fields that are not set or empty."""
        current_data = self.all()
        return [
            f
            for f in required_fields
            if f not in current_data or not current_data[f].strip()
        ]

    def masked(self, field: str) -> str | None:
        """Return masked display string for a field value, or None if not set."""
        val = self.get(field)
        if val is None:
            return None
        return masked(field, val)

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "ProfileStore":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
