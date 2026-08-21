"""Small, restart-safe SQLite state store."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VALID_STATUSES = {"PENDING", "RUNNING", "WAITING", "FAILED", "COMPLETED"}
VALID_STAGES = {"DISCOVER", "CRAWL", "EXTRACT", "RESEARCH", "RESOLVE", "VALIDATE", "PUBLISH", "VERIFY"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize_database(self) -> None:
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS system_state (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_type TEXT NOT NULL,
                    product_name TEXT,
                    source_url TEXT,
                    status TEXT NOT NULL DEFAULT 'PENDING'
                        CHECK(status IN ('PENDING','RUNNING','WAITING','FAILED','COMPLETED')),
                    current_stage TEXT NOT NULL DEFAULT 'DISCOVER'
                        CHECK(current_stage IN ('DISCOVER','CRAWL','EXTRACT','RESEARCH','RESOLVE','VALIDATE','PUBLISH','VERIFY')),
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    max_retries INTEGER NOT NULL DEFAULT 3,
                    last_error TEXT,
                    last_action TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );
            """)

    def create_job(self, job_type: str, product_name: str | None = None,
                   source_url: str | None = None, max_retries: int = 3) -> int:
        now = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO jobs
                   (job_type, product_name, source_url, status, current_stage,
                    retry_count, max_retries, created_at, updated_at)
                   VALUES (?, ?, ?, 'PENDING', 'DISCOVER', 0, ?, ?, ?)""",
                (job_type, product_name, source_url, max_retries, now, now),
            )
            return int(cursor.lastrowid)

    def update_job_status(self, job_id: int, status: str, *, stage: str | None = None,
                          last_error: str | None = None, last_action: str | None = None) -> None:
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid job status: {status}")
        if stage is not None and stage not in VALID_STAGES:
            raise ValueError(f"Invalid job stage: {stage}")
        now = _now()
        fields = ["status = ?", "updated_at = ?", "last_error = ?", "last_action = ?"]
        values: list[Any] = [status, now, last_error, last_action]
        if stage:
            fields.append("current_stage = ?")
            values.append(stage)
        if status == "RUNNING":
            fields.append("started_at = COALESCE(started_at, ?)")
            values.append(now)
        if status in {"FAILED", "COMPLETED"}:
            fields.append("finished_at = ?")
            values.append(now)
        values.append(job_id)
        with self._connect() as connection:
            cursor = connection.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", values)
            if cursor.rowcount == 0:
                raise KeyError(f"Job {job_id} not found")

    def get_pending_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs WHERE status = 'PENDING' ORDER BY id LIMIT ?", (limit,)
            ).fetchall()
            return [dict(row) for row in rows]

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return dict(row) if row else None

    def find_job(self, job_type: str, source_url: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_type = ? AND source_url = ? ORDER BY id LIMIT 1",
                (job_type, source_url),
            ).fetchone()
            return dict(row) if row else None

    def set_system_state(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO system_state(key, value, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (key, value, _now()),
            )

    def get_system_state(self, key: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute("SELECT value FROM system_state WHERE key = ?", (key,)).fetchone()
            return str(row["value"]) if row else None

