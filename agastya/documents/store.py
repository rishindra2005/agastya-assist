"""Document persistence and management backed by SQLite and filesystem storage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import sqlite3
from typing import Any
import uuid

from agastya.config import get_settings
from agastya.documents.form16 import extract_form16

logger = logging.getLogger(__name__)


@dataclass
class Document:
    id: str
    user_id: str
    filename: str
    kind: str
    path: str
    uploaded_at: str
    extracted: dict[str, Any]


class DocumentStore:
    """Store and manage user uploaded documents and their extracted structured data."""

    def __init__(self, user_id: str = "default", data_dir: Path | None = None) -> None:
        self.user_id = user_id
        if data_dir is not None:
            self.data_dir = Path(data_dir)
        else:
            self.data_dir = Path(get_settings().DATA_DIR)

        self.db_path = self.data_dir / "profile.db"
        self.docs_dir = self.data_dir / "documents" / self.user_id
        self.docs_dir.mkdir(parents=True, exist_ok=True)

        self._conn: sqlite3.Connection | None = None
        self._ensure_init()

    def _ensure_init(self) -> None:
        """Create documents table if it does not exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._get_connection()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                kind TEXT NOT NULL,
                path TEXT NOT NULL,
                uploaded_at TEXT NOT NULL,
                extracted TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_documents_user
            ON documents (user_id);
            """
        )
        conn.commit()

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL;")
        return self._conn

    def add(self, filename: str, data: bytes) -> Document:
        """Detect document kind, extract structured data, persist file and metadata."""
        doc_id = f"doc_{uuid.uuid4().hex[:10]}"
        now_iso = datetime.now(timezone.utc).isoformat()

        # Check heuristics for Form 16
        is_f16_filename = bool(re.search(r"form[-_]?16", filename, re.IGNORECASE))
        data_lower = data[:4096].lower()
        is_f16_content = (
            b"form no. 16" in data_lower
            or b"form 16" in data_lower
            or b"certificate under section 203" in data_lower
        )

        extracted = extract_form16(data)
        if extracted.get("kind") == "form16" or is_f16_filename or is_f16_content:
            kind = "form16"
            if extracted.get("kind") != "form16":
                extracted["kind"] = "form16"
        else:
            kind = extracted.get("kind", "unknown")

        file_path = self.docs_dir / f"{doc_id}_{filename}"
        file_path.write_bytes(data)

        conn = self._get_connection()
        conn.execute(
            """
            INSERT INTO documents (id, user_id, filename, kind, path, uploaded_at, extracted)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc_id,
                self.user_id,
                filename,
                kind,
                str(file_path.resolve()),
                now_iso,
                json.dumps(extracted),
            ),
        )
        conn.commit()

        return Document(
            id=doc_id,
            user_id=self.user_id,
            filename=filename,
            kind=kind,
            path=str(file_path.resolve()),
            uploaded_at=now_iso,
            extracted=extracted,
        )

    def get(self, doc_id: str) -> Document | None:
        """Retrieve a document by its unique ID for the current user."""
        conn = self._get_connection()
        cursor = conn.execute(
            """
            SELECT id, user_id, filename, kind, path, uploaded_at, extracted
            FROM documents
            WHERE id = ? AND user_id = ?
            """,
            (doc_id, self.user_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        return Document(
            id=row[0],
            user_id=row[1],
            filename=row[2],
            kind=row[3],
            path=row[4],
            uploaded_at=row[5],
            extracted=json.loads(row[6]),
        )

    def list(self) -> list[Document]:
        """List all documents belonging to the current user, ordered by upload time descending."""
        conn = self._get_connection()
        cursor = conn.execute(
            """
            SELECT id, user_id, filename, kind, path, uploaded_at, extracted
            FROM documents
            WHERE user_id = ?
            ORDER BY uploaded_at DESC
            """,
            (self.user_id,),
        )
        rows = cursor.fetchall()
        return [
            Document(
                id=r[0],
                user_id=r[1],
                filename=r[2],
                kind=r[3],
                path=r[4],
                uploaded_at=r[5],
                extracted=json.loads(r[6]),
            )
            for r in rows
        ]
