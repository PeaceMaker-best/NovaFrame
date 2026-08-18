from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class GenerationQueueFullError(RuntimeError):
    pass


class Repository:
    """Small SQLite repository with one short-lived connection per operation."""

    def __init__(self, database_path: Path):
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS canvases (
                    id TEXT PRIMARY KEY,
                    document_json TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS generation_jobs (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    command_json TEXT,
                    message TEXT NOT NULL DEFAULT '',
                    stdout TEXT NOT NULL DEFAULT '',
                    stderr TEXT NOT NULL DEFAULT '',
                    return_code INTEGER,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_generation_jobs_created_at
                ON generation_jobs(created_at DESC);

                CREATE INDEX IF NOT EXISTS idx_generation_jobs_status
                ON generation_jobs(status);

                CREATE TABLE IF NOT EXISTS generation_items (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES generation_jobs(id) ON DELETE CASCADE,
                    item_key TEXT NOT NULL,
                    product TEXT NOT NULL,
                    task TEXT NOT NULL,
                    shot TEXT NOT NULL,
                    candidate_index INTEGER NOT NULL,
                    prompt_filename TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'planned',
                    review_status TEXT NOT NULL DEFAULT 'pending',
                    storage_status TEXT NOT NULL DEFAULT 'staged',
                    relative_path TEXT,
                    filename TEXT NOT NULL DEFAULT '',
                    mime_type TEXT,
                    size_bytes INTEGER,
                    sha256 TEXT,
                    model TEXT,
                    quality TEXT,
                    estimated_cost REAL,
                    elapsed_seconds REAL,
                    error TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    generated_at TEXT,
                    reviewed_at TEXT,
                    updated_at TEXT NOT NULL,
                    UNIQUE(job_id, item_key)
                );

                CREATE INDEX IF NOT EXISTS idx_generation_items_job
                ON generation_items(job_id, status, candidate_index);

                CREATE INDEX IF NOT EXISTS idx_generation_items_review
                ON generation_items(review_status, created_at DESC);

                CREATE TABLE IF NOT EXISTS generation_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES generation_jobs(id) ON DELETE CASCADE,
                    seq INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    item_id TEXT REFERENCES generation_items(id) ON DELETE SET NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    UNIQUE(job_id, seq)
                );

                CREATE INDEX IF NOT EXISTS idx_generation_events_job
                ON generation_events(job_id, seq);

                CREATE TABLE IF NOT EXISTS provider_channels (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    endpoint TEXT NOT NULL DEFAULT '/images/edits',
                    api_key_encrypted TEXT NOT NULL,
                    api_key_hint TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT 'gpt-image-2',
                    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
                    currency TEXT NOT NULL DEFAULT 'CNY',
                    rates_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_used_at TEXT
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_provider_channels_name
                ON provider_channels(name COLLATE NOCASE);

                CREATE INDEX IF NOT EXISTS idx_provider_channels_active
                ON provider_channels(active, updated_at DESC);

                CREATE TABLE IF NOT EXISTS provider_routing_settings (
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    mode TEXT NOT NULL DEFAULT 'auto' CHECK(mode IN ('auto', 'fixed')),
                    fixed_channel_id TEXT REFERENCES provider_channels(id) ON DELETE SET NULL,
                    currency TEXT NOT NULL DEFAULT 'CNY',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS generation_provider_snapshots (
                    job_id TEXT PRIMARY KEY REFERENCES generation_jobs(id) ON DELETE CASCADE,
                    channel_id TEXT,
                    channel_name TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    api_key_encrypted TEXT NOT NULL,
                    model TEXT NOT NULL,
                    quality TEXT NOT NULL,
                    size TEXT NOT NULL,
                    unit_price REAL NOT NULL DEFAULT 0,
                    currency TEXT NOT NULL,
                    routing_mode TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'managed',
                    created_at TEXT NOT NULL
                );
                """
            )
            current_version = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
            if current_version < 3:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO provider_routing_settings(
                        id, mode, fixed_channel_id, currency, updated_at
                    ) VALUES (1, 'auto', NULL, 'CNY', ?)
                    """,
                    (utc_now(),),
                )
                connection.execute("PRAGMA user_version = 3")
        self.ensure_demo_job()

    def recover_interrupted_generation_runs(
        self,
        reason: str = "服务在任务完成前重启；为避免重复计费，任务未自动重试",
    ) -> int:
        """Fail stale in-process work so a restart never leaves queue slots stuck."""
        timestamp = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT id FROM generation_jobs
                WHERE action = 'generate' AND status IN ('queued', 'running')
                ORDER BY created_at ASC
                """
            ).fetchall()
            job_ids = [str(row["id"]) for row in rows]
            if not job_ids:
                return 0
            placeholders = ",".join("?" for _ in job_ids)
            connection.execute(
                f"""
                UPDATE generation_items
                SET status = 'failed', error = ?, updated_at = ?
                WHERE job_id IN ({placeholders})
                  AND status IN ('planned', 'running')
                """,
                (reason, timestamp, *job_ids),
            )
            connection.execute(
                f"""
                UPDATE generation_jobs
                SET status = 'failed', message = ?, stderr = ?,
                    finished_at = ?, updated_at = ?
                WHERE id IN ({placeholders})
                  AND status IN ('queued', 'running')
                """,
                (reason, reason, timestamp, timestamp, *job_ids),
            )
            for job_id in job_ids:
                next_seq = int(
                    connection.execute(
                        """
                        SELECT COALESCE(MAX(seq), 0) + 1
                        FROM generation_events WHERE job_id = ?
                        """,
                        (job_id,),
                    ).fetchone()[0]
                )
                connection.execute(
                    """
                    INSERT INTO generation_events(
                        job_id, seq, event_type, item_id, payload_json, created_at
                    ) VALUES (?, ?, 'run.recovered_as_failed', NULL, ?, ?)
                    """,
                    (
                        job_id,
                        next_seq,
                        json.dumps(
                            {"reason": reason},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        timestamp,
                    ),
                )
        return len(job_ids)

    @staticmethod
    def _decode_json(value: str | None, fallback: Any) -> Any:
        if not value:
            return fallback
        try:
            return json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return fallback

    def _canvas_from_row(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        document = self._decode_json(row["document_json"], {})
        if not isinstance(document, dict):
            document = {"value": document}
        result = dict(document)
        result.update(
            {
                "id": row["id"],
                "version": row["version"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
        return result

    def get_canvas(self, canvas_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM canvases WHERE id = ?", (canvas_id,)
            ).fetchone()
        return self._canvas_from_row(row)

    def upsert_canvas(self, canvas_id: str, document: dict[str, Any]) -> dict[str, Any]:
        serialized = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
        timestamp = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT version, created_at FROM canvases WHERE id = ?", (canvas_id,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO canvases(id, document_json, version, created_at, updated_at)
                    VALUES (?, ?, 1, ?, ?)
                    """,
                    (canvas_id, serialized, timestamp, timestamp),
                )
            else:
                connection.execute(
                    """
                    UPDATE canvases
                    SET document_json = ?, version = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (serialized, int(existing["version"]) + 1, timestamp, canvas_id),
                )
            row = connection.execute(
                "SELECT * FROM canvases WHERE id = ?", (canvas_id,)
            ).fetchone()
        result = self._canvas_from_row(row)
        if result is None:  # pragma: no cover - defensive guard around a single transaction
            raise RuntimeError("Canvas write did not produce a record")
        return result

    @staticmethod
    def _job_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        try:
            request = json.loads(row["request_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            request = {}
        try:
            command = json.loads(row["command_json"]) if row["command_json"] else None
        except (TypeError, ValueError, json.JSONDecodeError):
            command = None
        return {
            "id": row["id"],
            "kind": row["kind"],
            "action": row["action"],
            "status": row["status"],
            "ok": row["status"] == "completed",
            "request": request,
            "command": command,
            "message": row["message"],
            "stdout": row["stdout"],
            "stderr": row["stderr"],
            "return_code": row["return_code"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> dict[str, Any]:
        payload = Repository._decode_json(row["payload_json"], {})
        if not isinstance(payload, dict):
            payload = {"value": payload}
        return {
            "id": row["id"],
            "job_id": row["job_id"],
            "seq": row["seq"],
            "type": row["event_type"],
            "item_id": row["item_id"],
            "payload": payload,
            "created_at": row["created_at"],
        }

    @staticmethod
    def _candidate_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        metadata = Repository._decode_json(row["metadata_json"], {})
        if not isinstance(metadata, dict):
            metadata = {}
        relative_path = row["relative_path"]
        return {
            "id": row["id"],
            "job_id": row["job_id"],
            "item_key": row["item_key"],
            "product": row["product"],
            "task": row["task"],
            "shot": row["shot"],
            "candidate_index": row["candidate_index"],
            "prompt_filename": row["prompt_filename"],
            "status": row["status"],
            "review_status": row["review_status"],
            "storage_status": row["storage_status"],
            "relative_path": relative_path,
            "name": row["filename"] or (Path(relative_path).name if relative_path else ""),
            "url": f"/api/candidates/{row['id']}/image",
            "mime_type": row["mime_type"],
            "size_bytes": row["size_bytes"],
            "sha256": row["sha256"],
            "model": row["model"],
            "quality": row["quality"],
            "estimated_cost": row["estimated_cost"],
            "elapsed_seconds": row["elapsed_seconds"],
            "error": row["error"],
            "metadata": metadata,
            "created_at": row["created_at"],
            "generated_at": row["generated_at"],
            "reviewed_at": row["reviewed_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _provider_channel_from_row(
        row: sqlite3.Row | None, *, include_secrets: bool = False
    ) -> dict[str, Any] | None:
        if row is None:
            return None
        rates = Repository._decode_json(row["rates_json"], {})
        if not isinstance(rates, dict):
            rates = {}
        result = {
            "id": row["id"],
            "name": row["name"],
            "base_url": row["base_url"],
            "endpoint": row["endpoint"],
            "api_key_hint": row["api_key_hint"],
            "has_api_key": bool(row["api_key_encrypted"]),
            "model": row["model"],
            "active": bool(row["active"]),
            "currency": row["currency"],
            "rates": {
                quality: float(rates.get(quality, 0) or 0)
                for quality in ("low", "medium", "high")
            },
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_used_at": row["last_used_at"],
        }
        if include_secrets:
            result["api_key_encrypted"] = row["api_key_encrypted"]
        return result

    def list_provider_channels(
        self, *, include_secrets: bool = False
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM provider_channels
                ORDER BY active DESC, updated_at DESC, name COLLATE NOCASE ASC
                """
            ).fetchall()
        return [
            channel
            for row in rows
            if (
                channel := self._provider_channel_from_row(
                    row, include_secrets=include_secrets
                )
            )
            is not None
        ]

    def get_provider_channel(
        self, channel_id: str, *, include_secrets: bool = False
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM provider_channels WHERE id = ?", (channel_id,)
            ).fetchone()
        return self._provider_channel_from_row(row, include_secrets=include_secrets)

    def create_provider_channel(
        self,
        *,
        name: str,
        base_url: str,
        endpoint: str,
        api_key_encrypted: str,
        api_key_hint: str,
        model: str,
        active: bool,
        currency: str,
        rates: dict[str, float],
    ) -> dict[str, Any]:
        channel_id = f"provider-{uuid.uuid4().hex[:16]}"
        timestamp = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO provider_channels(
                    id, name, base_url, endpoint, api_key_encrypted,
                    api_key_hint, model, active, currency, rates_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    channel_id,
                    name,
                    base_url,
                    endpoint,
                    api_key_encrypted,
                    api_key_hint,
                    model,
                    int(active),
                    currency,
                    json.dumps(rates, ensure_ascii=False, separators=(",", ":")),
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM provider_channels WHERE id = ?", (channel_id,)
            ).fetchone()
        result = self._provider_channel_from_row(row)
        if result is None:  # pragma: no cover
            raise RuntimeError("Provider channel creation failed")
        return result

    def update_provider_channel(
        self, channel_id: str, patch: dict[str, Any]
    ) -> dict[str, Any] | None:
        allowed = {
            "name",
            "base_url",
            "endpoint",
            "api_key_encrypted",
            "api_key_hint",
            "model",
            "active",
            "currency",
            "rates",
        }
        values = {key: value for key, value in patch.items() if key in allowed}
        if not values:
            return self.get_provider_channel(channel_id)
        assignments: list[str] = []
        params: list[Any] = []
        for key, value in values.items():
            column = "rates_json" if key == "rates" else key
            if key == "rates":
                value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            elif key == "active":
                value = int(bool(value))
            assignments.append(f"{column} = ?")
            params.append(value)
        assignments.append("updated_at = ?")
        params.extend([utc_now(), channel_id])
        with self._connect() as connection:
            connection.execute(
                f"UPDATE provider_channels SET {', '.join(assignments)} WHERE id = ?",
                tuple(params),
            )
            row = connection.execute(
                "SELECT * FROM provider_channels WHERE id = ?", (channel_id,)
            ).fetchone()
        return self._provider_channel_from_row(row)

    def get_provider_routing(self) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM provider_routing_settings WHERE id = 1"
            ).fetchone()
        if row is None:  # pragma: no cover - initialize always seeds this row
            return {
                "mode": "auto",
                "fixed_channel_id": None,
                "currency": "CNY",
                "updated_at": utc_now(),
            }
        return {
            "mode": row["mode"],
            "fixed_channel_id": row["fixed_channel_id"],
            "currency": row["currency"],
            "updated_at": row["updated_at"],
        }

    def update_provider_routing(
        self, *, mode: str, fixed_channel_id: str | None, currency: str
    ) -> dict[str, Any]:
        timestamp = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO provider_routing_settings(
                    id, mode, fixed_channel_id, currency, updated_at
                ) VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    mode = excluded.mode,
                    fixed_channel_id = excluded.fixed_channel_id,
                    currency = excluded.currency,
                    updated_at = excluded.updated_at
                """,
                (mode, fixed_channel_id, currency, timestamp),
            )
        return self.get_provider_routing()

    def get_generation_provider_snapshot(
        self, job_id: str
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM generation_provider_snapshots WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def _generation_summary(self, job: dict[str, Any]) -> dict[str, Any]:
        if job.get("action") != "generate":
            return job
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS expected_count,
                    COALESCE(SUM(status = 'generated'), 0) AS generated_count,
                    COALESCE(SUM(status = 'failed'), 0) AS failed_count,
                    COALESCE(SUM(status IN ('generated', 'failed')), 0) AS completed_count,
                    COALESCE(SUM(status = 'generated' AND review_status = 'pending'), 0)
                        AS pending_review_count,
                    COALESCE(SUM(status = 'generated' AND review_status = 'selected'), 0)
                        AS selected_count
                FROM generation_items
                WHERE job_id = ?
                """,
                (job["id"],),
            ).fetchone()
        stored_expected = int(row["expected_count"] or 0)
        completed = int(row["completed_count"] or 0)
        request = job.get("request") if isinstance(job.get("request"), dict) else {}
        requested_tasks = request.get("tasks") or []
        requested_shots = request.get("shots") or []
        requested_items = request.get("items") or []
        requested_variants = int(request.get("variants") or 1)
        if requested_items:
            planned_expected = len(requested_items) * requested_variants
        elif requested_tasks and requested_shots:
            planned_expected = (
                len(requested_tasks) * len(requested_shots) * requested_variants
            )
        else:
            planned_expected = 0
        expected = planned_expected or stored_expected
        if expected and job.get("status") in {"completed", "failed"}:
            progress = 100.0
        else:
            progress = round((completed / expected) * 100, 1) if expected else 0.0
        result = dict(job)
        # The run API is presentation-facing. Keep local executable paths and raw
        # process streams in the legacy diagnostics endpoint only.
        for internal_field in ("command", "stdout", "stderr"):
            result.pop(internal_field, None)
        result.update(
            {
                "product": request.get("product"),
                "tasks": requested_tasks,
                "shots": requested_shots,
                "variants": requested_variants,
                "concurrency": int(request.get("concurrency") or 1),
                "provider": request.get("provider")
                if isinstance(request.get("provider"), dict)
                else None,
                "expected_candidate_count": expected,
                "candidate_count": int(row["generated_count"] or 0),
                "completed_count": completed,
                "failed_count": int(row["failed_count"] or 0),
                "pending_review_count": int(row["pending_review_count"] or 0),
                "selected_count": int(row["selected_count"] or 0),
                "progress": progress,
            }
        )
        return result

    def ensure_demo_job(self) -> dict[str, Any]:
        timestamp = utc_now()
        request = {
            "product": "MF-DEMO-001",
            "tasks": ["单品"],
            "shots": ["main", "lifestyle-scene"],
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO generation_jobs(
                    id, kind, action, status, request_json, message,
                    stdout, stderr, return_code, created_at, started_at,
                    finished_at, updated_at
                ) VALUES (?, 'demo', 'preview', 'completed', ?, ?, '', '', 0, ?, ?, ?, ?)
                """,
                (
                    "demo-product-workflow",
                    json.dumps(request, ensure_ascii=False),
                    "示例任务：商品主图与场景图工作流预览",
                    timestamp,
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM generation_jobs WHERE id = ?",
                ("demo-product-workflow",),
            ).fetchone()
        result = self._job_from_row(row)
        if result is None:  # pragma: no cover
            raise RuntimeError("Demo job initialization failed")
        return result

    def create_job(
        self,
        *,
        action: str,
        request: dict[str, Any],
        command: list[str],
    ) -> dict[str, Any]:
        job_id = f"job-{uuid.uuid4().hex[:16]}"
        timestamp = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO generation_jobs(
                    id, kind, action, status, request_json, command_json,
                    message, created_at, updated_at
                ) VALUES (?, 'workflow', ?, 'queued', ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    action,
                    json.dumps(request, ensure_ascii=False),
                    json.dumps(command, ensure_ascii=False),
                    "任务已进入本地工作流",
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM generation_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        result = self._job_from_row(row)
        if result is None:  # pragma: no cover
            raise RuntimeError("Job creation failed")
        return result

    @staticmethod
    def generation_item_key(
        product: str, task: str, shot: str, candidate_index: int
    ) -> str:
        return json.dumps(
            [product, task, shot, candidate_index],
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def create_generation_run(
        self,
        *,
        request: dict[str, Any],
        command: list[str],
        provider_snapshot: dict[str, Any] | None = None,
        max_active_runs: int | None = None,
    ) -> dict[str, Any]:
        job_id = f"job-{uuid.uuid4().hex[:16]}"
        product = str(request["product"])
        tasks = [str(value) for value in request.get("tasks") or []]
        shots = [str(value) for value in request.get("shots") or []]
        requested_items = request.get("items")
        if isinstance(requested_items, list) and requested_items:
            scope = [
                (str(item["task"]), str(item["shot"]))
                for item in requested_items
                if isinstance(item, dict)
            ]
        else:
            scope = [(task, shot) for task in tasks for shot in shots]
        variants = int(request.get("variants") or 1)
        timestamp = utc_now()
        rows: list[tuple[Any, ...]] = []
        for task, shot in scope:
            for candidate_index in range(1, variants + 1):
                item_key = self.generation_item_key(
                    product, task, shot, candidate_index
                )
                rows.append(
                    (
                        f"candidate-{uuid.uuid4().hex[:20]}",
                        job_id,
                        item_key,
                        product,
                        task,
                        shot,
                        candidate_index,
                        timestamp,
                        timestamp,
                    )
                )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if max_active_runs is not None:
                active_count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM generation_jobs
                        WHERE action = 'generate' AND status IN ('queued', 'running')
                        """
                    ).fetchone()[0]
                )
                if active_count >= max_active_runs:
                    raise GenerationQueueFullError(
                        f"Generation queue already has {active_count} active runs"
                    )
            connection.execute(
                """
                INSERT INTO generation_jobs(
                    id, kind, action, status, request_json, command_json,
                    message, created_at, updated_at
                ) VALUES (?, 'workflow', 'generate', 'queued', ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    json.dumps(request, ensure_ascii=False),
                    json.dumps(command, ensure_ascii=False),
                    "任务已进入本地工作流",
                    timestamp,
                    timestamp,
                ),
            )
            connection.executemany(
                """
                INSERT INTO generation_items(
                    id, job_id, item_key, product, task, shot, candidate_index,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            if provider_snapshot is not None:
                connection.execute(
                    """
                    INSERT INTO generation_provider_snapshots(
                        job_id, channel_id, channel_name, base_url, endpoint,
                        api_key_encrypted, model, quality, size, unit_price,
                        currency, routing_mode, source, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        provider_snapshot.get("channel_id"),
                        provider_snapshot["channel_name"],
                        provider_snapshot["base_url"],
                        provider_snapshot["endpoint"],
                        provider_snapshot["api_key_encrypted"],
                        provider_snapshot["model"],
                        provider_snapshot["quality"],
                        provider_snapshot["size"],
                        float(provider_snapshot.get("unit_price") or 0),
                        provider_snapshot["currency"],
                        provider_snapshot["routing_mode"],
                        provider_snapshot.get("source", "managed"),
                        timestamp,
                    ),
                )
                if provider_snapshot.get("channel_id"):
                    connection.execute(
                        "UPDATE provider_channels SET last_used_at = ? WHERE id = ?",
                        (timestamp, provider_snapshot["channel_id"]),
                    )
            connection.execute(
                """
                INSERT INTO generation_events(
                    job_id, seq, event_type, item_id, payload_json, created_at
                ) VALUES (?, 1, 'run.queued', NULL, ?, ?)
                """,
                (
                    job_id,
                    json.dumps(
                        {
                            "product": product,
                            "tasks": tasks,
                            "shots": shots,
                            "items": [
                                {"task": task, "shot": shot}
                                for task, shot in scope
                            ],
                            "variants": variants,
                            "total_items": len(rows),
                            "provider": request.get("provider"),
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    timestamp,
                ),
            )
        created = self.get_generation_run(job_id)
        if created is None:  # pragma: no cover
            raise RuntimeError("Generation run creation failed")
        return created

    def mark_job_running(self, job_id: str) -> None:
        timestamp = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE generation_jobs
                SET status = 'running', message = ?, started_at = ?, updated_at = ?
                WHERE id = ?
                """,
                ("工作流正在执行", timestamp, timestamp, job_id),
            )

    def finish_job(
        self,
        job_id: str,
        *,
        status: str,
        message: str,
        stdout: str = "",
        stderr: str = "",
        return_code: int | None = None,
    ) -> dict[str, Any]:
        timestamp = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE generation_jobs
                SET status = ?, message = ?, stdout = ?, stderr = ?,
                    return_code = ?, finished_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    message,
                    stdout[-30000:],
                    stderr[-30000:],
                    return_code,
                    timestamp,
                    timestamp,
                    job_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM generation_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        result = self._job_from_row(row)
        if result is None:  # pragma: no cover
            raise RuntimeError(f"Unknown job: {job_id}")
        return result

    def list_jobs(self, *, limit: int = 50, status: str | None = None) -> dict[str, Any]:
        where = " WHERE status = ?" if status else ""
        params: tuple[Any, ...] = (status,) if status else ()
        with self._connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM generation_jobs{where}", params
            ).fetchone()[0]
            rows = connection.execute(
                f"SELECT * FROM generation_jobs{where} ORDER BY created_at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return {
            "items": [self._job_from_row(row) for row in rows],
            "total": total,
        }

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM generation_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._job_from_row(row)

    def get_generation_run(self, job_id: str) -> dict[str, Any] | None:
        job = self.get_job(job_id)
        if job is None or job.get("action") != "generate":
            return None
        return self._generation_summary(job)

    def count_active_generation_runs(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) FROM generation_jobs
                WHERE action = 'generate' AND status IN ('queued', 'running')
                """
            ).fetchone()
        return int(row[0])

    def list_generation_runs(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> dict[str, Any]:
        clauses = ["kind = 'workflow'", "action = 'generate'"]
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = " WHERE " + " AND ".join(clauses)
        with self._connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM generation_jobs{where}", tuple(params)
            ).fetchone()[0]
            rows = connection.execute(
                f"SELECT * FROM generation_jobs{where} "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        jobs = [self._job_from_row(row) for row in rows]
        return {
            "items": [
                self._generation_summary(job) for job in jobs if job is not None
            ],
            "total": total,
        }

    def add_event(
        self,
        job_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        item_id: str | None = None,
    ) -> dict[str, Any]:
        timestamp = utc_now()
        serialized = json.dumps(
            payload or {}, ensure_ascii=False, separators=(",", ":")
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            next_seq = int(
                connection.execute(
                    "SELECT COALESCE(MAX(seq), 0) + 1 FROM generation_events WHERE job_id = ?",
                    (job_id,),
                ).fetchone()[0]
            )
            cursor = connection.execute(
                """
                INSERT INTO generation_events(
                    job_id, seq, event_type, item_id, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (job_id, next_seq, event_type, item_id, serialized, timestamp),
            )
            row = connection.execute(
                "SELECT * FROM generation_events WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        return self._event_from_row(row)

    def list_events(self, job_id: str, *, after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM generation_events
                WHERE job_id = ? AND seq > ?
                ORDER BY seq ASC LIMIT ?
                """,
                (job_id, after, limit),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def update_generation_item(
        self,
        job_id: str,
        *,
        product: str,
        task: str,
        shot: str,
        candidate_index: int,
        status: str,
        relative_path: str | None = None,
        filename: str = "",
        prompt_filename: str = "",
        mime_type: str | None = None,
        size_bytes: int | None = None,
        sha256: str | None = None,
        model: str | None = None,
        quality: str | None = None,
        estimated_cost: float | None = None,
        elapsed_seconds: float | None = None,
        error: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        item_key = self.generation_item_key(product, task, shot, candidate_index)
        timestamp = utc_now()
        generated_at = timestamp if status == "generated" else None
        if status == "running":
            transition_guard = "status = 'planned'"
        elif status in {"generated", "failed"}:
            transition_guard = "status IN ('planned', 'running')"
        else:
            transition_guard = "status IN ('planned', 'running')"
        with self._connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE generation_items
                SET status = ?, relative_path = COALESCE(?, relative_path),
                    filename = CASE WHEN ? <> '' THEN ? ELSE filename END,
                    prompt_filename = CASE WHEN ? <> '' THEN ? ELSE prompt_filename END,
                    mime_type = COALESCE(?, mime_type),
                    size_bytes = COALESCE(?, size_bytes),
                    sha256 = COALESCE(?, sha256),
                    model = COALESCE(?, model), quality = COALESCE(?, quality),
                    estimated_cost = COALESCE(?, estimated_cost),
                    elapsed_seconds = COALESCE(?, elapsed_seconds),
                    error = ?, metadata_json = ?,
                    generated_at = COALESCE(?, generated_at), updated_at = ?
                WHERE job_id = ? AND item_key = ? AND {transition_guard}
                """,
                (
                    status,
                    relative_path,
                    filename,
                    filename,
                    prompt_filename,
                    prompt_filename,
                    mime_type,
                    size_bytes,
                    sha256,
                    model,
                    quality,
                    estimated_cost,
                    elapsed_seconds,
                    error,
                    json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":")),
                    generated_at,
                    timestamp,
                    job_id,
                    item_key,
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute(
                "SELECT * FROM generation_items WHERE job_id = ? AND item_key = ?",
                (job_id, item_key),
            ).fetchone()
        return self._candidate_from_row(row)

    def fail_unfinished_items(self, job_id: str, error: str) -> int:
        timestamp = utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE generation_items
                SET status = 'failed', error = ?, updated_at = ?
                WHERE job_id = ? AND status IN ('planned', 'running')
                """,
                (error, timestamp, job_id),
            )
        return int(cursor.rowcount)

    def list_candidates(
        self,
        *,
        limit: int = 200,
        offset: int = 0,
        job_id: str | None = None,
        review_status: str | None = None,
    ) -> dict[str, Any]:
        clauses = ["status = 'generated'"]
        params: list[Any] = []
        if job_id:
            clauses.append("job_id = ?")
            params.append(job_id)
        if review_status:
            clauses.append("review_status = ?")
            params.append(review_status)
        where = " WHERE " + " AND ".join(clauses)
        with self._connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM generation_items{where}", tuple(params)
            ).fetchone()[0]
            rows = connection.execute(
                f"SELECT * FROM generation_items{where} "
                "ORDER BY created_at DESC, candidate_index ASC, id ASC "
                "LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        return {
            "items": [self._candidate_from_row(row) for row in rows],
            "total": total,
        }

    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM generation_items WHERE id = ? AND status = 'generated'",
                (candidate_id,),
            ).fetchone()
        return self._candidate_from_row(row)

    def select_candidate(self, candidate_id: str, relative_path: str) -> dict[str, Any] | None:
        timestamp = utc_now()
        filename = Path(relative_path).name
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE generation_items
                SET review_status = 'selected', storage_status = 'promoted',
                    relative_path = ?, filename = ?, reviewed_at = ?, updated_at = ?
                WHERE id = ? AND status = 'generated'
                """,
                (relative_path, filename, timestamp, timestamp, candidate_id),
            )
            row = connection.execute(
                "SELECT * FROM generation_items WHERE id = ?", (candidate_id,)
            ).fetchone()
        return self._candidate_from_row(row)

    def delete_candidate(self, candidate_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM generation_items WHERE id = ? AND status = 'generated'",
                (candidate_id,),
            )
        return bool(cursor.rowcount)
