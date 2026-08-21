"""Small, restart-safe SQLite state store."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

VALID_STATUSES = {"PENDING", "RUNNING", "WAITING", "FAILED", "COMPLETED"}
VALID_STAGES = {"DISCOVER", "CRAWL", "EXTRACT", "RESEARCH", "RESOLVE", "VALIDATE", "PUBLISH", "VERIFY"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

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
                CREATE TABLE IF NOT EXISTS training_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    canonical_url TEXT NOT NULL UNIQUE,
                    discovered_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'DISCOVERED',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS training_extractions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    training_source_id INTEGER NOT NULL UNIQUE REFERENCES training_sources(id),
                    canonical_url TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING'
                        CHECK(status IN ('PENDING','FETCHING','EXTRACTING','COMPLETED','PARTIAL','FAILED')),
                    fetch_method TEXT, http_status INTEGER, content_hash TEXT, template_type TEXT,
                    extracted_at TEXT, updated_at TEXT NOT NULL, retry_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT, facts_path TEXT, evidence_path TEXT, raw_snapshot_path TEXT
                );
                CREATE TABLE IF NOT EXISTS kb_resources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, resource_type TEXT NOT NULL, name TEXT NOT NULL,
                    url TEXT NOT NULL, canonical_key TEXT NOT NULL UNIQUE, content_hash TEXT NOT NULL,
                    status TEXT NOT NULL, last_seen_at TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS kb_products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, short_name TEXT, category TEXT,
                    detail_url TEXT NOT NULL UNIQUE, seo_url TEXT, content_hash TEXT NOT NULL, snapshot_path TEXT,
                    last_seen_at TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS resolved_products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, training_source_id INTEGER, slug TEXT NOT NULL UNIQUE,
                    source_url TEXT NOT NULL, status TEXT NOT NULL, resolution_hash TEXT NOT NULL,
                    resolved_path TEXT NOT NULL, publish_payload_path TEXT NOT NULL, completion REAL NOT NULL,
                    needs_review INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, training_source_id INTEGER, product_slug TEXT NOT NULL,
                    field_name TEXT NOT NULL, status TEXT NOT NULL, query TEXT NOT NULL, cache_key TEXT NOT NULL UNIQUE,
                    result_path TEXT, retry_count INTEGER NOT NULL DEFAULT 0, last_error TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
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

    def upsert_training_source(self, *, name: str, category: str, source_url: str,
                               canonical_url: str, discovered_at: str, source_hash: str) -> tuple[int, bool]:
        """Insert a canonical training URL or refresh its current metadata."""
        now = _now()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT id, name, category FROM training_sources WHERE canonical_url = ?",
                (canonical_url,),
            ).fetchone()
            if existing:
                connection.execute(
                    """UPDATE training_sources SET name=?, category=?, source_url=?, last_seen_at=?,
                       source_hash=?, updated_at=? WHERE canonical_url=?""",
                    (name, category, source_url, now, source_hash, now, canonical_url),
                )
                return int(existing["id"]), False
            cursor = connection.execute(
                """INSERT INTO training_sources
                   (name, category, source_url, canonical_url, discovered_at, last_seen_at,
                    source_hash, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'DISCOVERED', ?, ?)""",
                (name, category, source_url, canonical_url, discovered_at, now, source_hash, now, now),
            )
            return int(cursor.lastrowid), True

    def count_training_sources(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM training_sources").fetchone()[0])

    def list_training_sources(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM training_sources ORDER BY id")]

    def get_training_extraction(self, training_source_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM training_extractions WHERE training_source_id=?",
                                     (training_source_id,)).fetchone()
            return dict(row) if row else None

    def upsert_training_extraction(self, training_source_id: int, canonical_url: str, status: str, **values: Any) -> None:
        allowed = {"fetch_method", "http_status", "content_hash", "template_type", "extracted_at", "retry_count",
                   "last_error", "facts_path", "evidence_path", "raw_snapshot_path"}
        unknown = set(values) - allowed
        if unknown: raise ValueError(f"Unknown extraction columns: {sorted(unknown)}")
        now = _now()
        columns = ["training_source_id", "canonical_url", "status", "updated_at", *values]
        params = [training_source_id, canonical_url, status, now, *values.values()]
        updates = ["canonical_url=excluded.canonical_url", "status=excluded.status", "updated_at=excluded.updated_at"]
        updates += [f"{key}=excluded.{key}" for key in values]
        with self._connect() as connection:
            connection.execute(
                f"INSERT INTO training_extractions ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) "
                f"ON CONFLICT(training_source_id) DO UPDATE SET {','.join(updates)}", params)

    def list_training_extractions(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM training_extractions ORDER BY training_source_id")]

    def upsert_kb_resource(self, *, resource_type: str, name: str, url: str, canonical_key: str,
                           content_hash: str, status: str = "ACTIVE") -> str:
        now = _now()
        with self._connect() as connection:
            row = connection.execute("SELECT content_hash FROM kb_resources WHERE canonical_key=?", (canonical_key,)).fetchone()
            change = "new" if not row else "unchanged" if row["content_hash"] == content_hash else "updated"
            connection.execute("""INSERT INTO kb_resources(resource_type,name,url,canonical_key,content_hash,status,last_seen_at,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(canonical_key) DO UPDATE SET resource_type=excluded.resource_type,
                name=excluded.name,url=excluded.url,content_hash=excluded.content_hash,status=excluded.status,
                last_seen_at=excluded.last_seen_at,updated_at=excluded.updated_at""",
                (resource_type, name, url, canonical_key, content_hash, status, now, now, now))
            return change

    def upsert_kb_product(self, *, name: str, short_name: str | None, category: str | None, detail_url: str,
                          seo_url: str | None, content_hash: str, snapshot_path: str | None) -> str:
        now = _now()
        with self._connect() as connection:
            row = connection.execute("SELECT content_hash FROM kb_products WHERE detail_url=?", (detail_url,)).fetchone()
            change = "new" if not row else "unchanged" if row["content_hash"] == content_hash else "updated"
            connection.execute("""INSERT INTO kb_products(name,short_name,category,detail_url,seo_url,content_hash,snapshot_path,last_seen_at,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(detail_url) DO UPDATE SET name=excluded.name,short_name=excluded.short_name,
                category=excluded.category,seo_url=excluded.seo_url,content_hash=excluded.content_hash,
                snapshot_path=excluded.snapshot_path,last_seen_at=excluded.last_seen_at,updated_at=excluded.updated_at""",
                (name, short_name, category, detail_url, seo_url, content_hash, snapshot_path, now, now, now))
            return change

    def upsert_resolved_product(self, *, slug: str, status: str, resolution_hash: str, resolved_path: str,
                                publish_payload_path: str, completion: float, needs_review: bool, source_url: str) -> str:
        now=_now()
        with self._connect() as connection:
            row=connection.execute("SELECT resolution_hash FROM resolved_products WHERE slug=?",(slug,)).fetchone()
            change="new" if not row else "unchanged" if row["resolution_hash"]==resolution_hash else "updated"
            connection.execute("""INSERT INTO resolved_products(slug,source_url,status,resolution_hash,resolved_path,publish_payload_path,completion,needs_review,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(slug) DO UPDATE SET source_url=excluded.source_url,status=excluded.status,
                resolution_hash=excluded.resolution_hash,resolved_path=excluded.resolved_path,publish_payload_path=excluded.publish_payload_path,
                completion=excluded.completion,needs_review=excluded.needs_review,updated_at=excluded.updated_at""",
                (slug,source_url,status,resolution_hash,resolved_path,publish_payload_path,completion,int(needs_review),now,now));return change

    def list_resolved_products(self)->list[dict[str,Any]]:
        with self._connect() as connection:return [dict(x) for x in connection.execute("SELECT * FROM resolved_products ORDER BY slug")]

    def upsert_research_task(self, *, product_slug:str, field_name:str, status:str, query:str, cache_key:str,
                             result_path:str|None=None,retry_count:int=0,last_error:str|None=None)->None:
        now=_now()
        with self._connect() as connection:connection.execute("""INSERT INTO research_tasks(product_slug,field_name,status,query,cache_key,result_path,retry_count,last_error,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET status=excluded.status,result_path=excluded.result_path,
          retry_count=excluded.retry_count,last_error=excluded.last_error,updated_at=excluded.updated_at""",(product_slug,field_name,status,query,cache_key,result_path,retry_count,last_error,now,now))

    def research_metrics(self)->dict[str,int]:
        with self._connect() as c:
            rows=c.execute("SELECT status,COUNT(*) n FROM research_tasks GROUP BY status").fetchall();return {x["status"]:x["n"] for x in rows}
