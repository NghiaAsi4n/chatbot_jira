"""
T06: SQLite-backed request store với idempotency claim nguyên tử.

- Mỗi request_id được lưu cùng content_hash, status và kết quả.
- Claim dùng INSERT OR IGNORE để nguyên tử: hai thread/process gửi cùng lúc
  chỉ một bên ghi được, bên kia đọc lại kết quả hiện có.
- Cùng ID + cùng hash → trả kết quả cũ.
- Cùng ID + khác hash → conflict.
- Pending còn lại sau restart → unknown.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    request_id   TEXT NOT NULL PRIMARY KEY,
    content_hash TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    issue_key    TEXT,
    issue_url    TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""

# Statuses stored in DB
STATUS_PENDING   = "pending"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED    = "failed"
STATUS_UNKNOWN   = "unknown"


class ConflictError(Exception):
    """Same request_id sent with different content hash."""


class PendingError(Exception):
    """Request is still pending (e.g. a concurrent inflight call)."""


@dataclass
class StoredRequest:
    request_id: str
    content_hash: str
    status: str
    issue_key: str | None = None
    issue_url: str | None = None


def _hash(content: dict[str, Any]) -> str:
    """Deterministic SHA-256 of the sorted JSON representation."""
    blob = json.dumps(content, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class RequestStore:
    """Thread-safe SQLite store for request idempotency.

    Usage:
        store = RequestStore(Path("data/requests.db"))
        # On inbound request:
        try:
            store.claim(request_id, content_dict)
        except ConflictError:
            ...  # same ID, different content
        # After successful Jira create:
        store.succeed(request_id, issue_key, issue_url)
        # On error:
        store.fail(request_id)
        # On unknown outcome:
        store.mark_unknown(request_id)
    """

    def __init__(self, db_path: Path | str = ":memory:") -> None:
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        # For :memory: databases, keep a single persistent connection so
        # the schema and data survive across calls.
        self._persistent_conn: sqlite3.Connection | None = None
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        if self._db_path == ":memory:":
            if self._persistent_conn is None:
                conn = sqlite3.connect(":memory:", check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys=ON")
                self._persistent_conn = conn
            return self._persistent_conn
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _close(self, conn: sqlite3.Connection) -> None:
        """Close a connection unless it is the persistent in-memory one."""
        if conn is not self._persistent_conn:
            conn.close()

    def _init_db(self) -> None:
        if self._db_path != ":memory:":
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            conn = self._connect()
            conn.executescript(DB_SCHEMA)
            conn.commit()
            # Heal any pending rows left from a previous crash/restart.
            conn.execute(
                "UPDATE requests SET status=? WHERE status=?",
                (STATUS_UNKNOWN, STATUS_PENDING),
            )
            conn.commit()
            self._close(conn)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def claim(self, request_id: str, content: dict[str, Any]) -> StoredRequest | None:
        """Try to claim a request slot.

        Returns:
            None  — claim succeeded; caller may proceed with the write.
            StoredRequest — a previous result exists; caller should return it.

        Raises:
            ConflictError — same ID, different content.
        """
        chash = _hash(content)
        with self._lock:
            conn = self._connect()
            try:
                # Atomic INSERT: succeeds only if request_id is new.
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO requests (request_id, content_hash, status) VALUES (?,?,?)",
                    (request_id, chash, STATUS_PENDING),
                )
                inserted = cursor.rowcount == 1
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM requests WHERE request_id=?", (request_id,)
                ).fetchone()
            finally:
                self._close(conn)

        stored = StoredRequest(
            request_id=row["request_id"],
            content_hash=row["content_hash"],
            status=row["status"],
            issue_key=row["issue_key"],
            issue_url=row["issue_url"],
        )
        if stored.content_hash != chash:
            raise ConflictError(
                f"request_id {request_id!r} already used with different content"
            )
        # Only the thread that actually inserted the row proceeds.
        if inserted:
            return None
        return stored

    def succeed(self, request_id: str, issue_key: str, issue_url: str | None) -> None:
        self._update(request_id, STATUS_SUCCEEDED, issue_key=issue_key, issue_url=issue_url)

    def fail(self, request_id: str) -> None:
        self._update(request_id, STATUS_FAILED)

    def mark_unknown(self, request_id: str) -> None:
        self._update(request_id, STATUS_UNKNOWN)

    def get(self, request_id: str) -> StoredRequest | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM requests WHERE request_id=?", (request_id,)
                ).fetchone()
            finally:
                self._close(conn)
        if row is None:
            return None
        return StoredRequest(
            request_id=row["request_id"],
            content_hash=row["content_hash"],
            status=row["status"],
            issue_key=row["issue_key"],
            issue_url=row["issue_url"],
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _update(self, request_id: str, status: str, *,
                issue_key: str | None = None, issue_url: str | None = None) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE requests SET status=?, issue_key=?, issue_url=? WHERE request_id=?",
                    (status, issue_key, issue_url, request_id),
                )
                conn.commit()
            finally:
                self._close(conn)
