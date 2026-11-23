# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Durable experiment store (SQLite, WAL).

A single store backs the CLI, the API job manager and the evaluation reports.
All writes are serialized with an ``RLock`` and use a connection created with
``check_same_thread=False``; WAL keeps concurrent readers non-blocking. Every
row is validated through the Pydantic contracts in ``core.models``.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from ..core.models import (
    ArtifactRecord,
    EventRecord,
    ExperimentRecord,
    JobRecord,
    TrialRecord,
    utcnow_iso,
)
from ..errors import ConflictError, NotFoundError

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS experiments (
    experiment_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    model_id TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    params TEXT NOT NULL DEFAULT '{}',
    metrics TEXT NOT NULL DEFAULT '{}',
    tags TEXT NOT NULL DEFAULT '[]',
    seed INTEGER,
    config_hash TEXT,
    suite_hash TEXT,
    hardware_fingerprint TEXT,
    software TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS trials (
    trial_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id) ON DELETE CASCADE,
    number INTEGER NOT NULL,
    state TEXT NOT NULL,
    params TEXT NOT NULL DEFAULT '{}',
    objective_values TEXT NOT NULL DEFAULT '[]',
    objective_names TEXT NOT NULL DEFAULT '[]',
    duration_seconds REAL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trials_experiment ON trials(experiment_id, number);
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    experiment_id TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    progress REAL NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    request TEXT NOT NULL DEFAULT '{}',
    result TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    cancellable INTEGER NOT NULL DEFAULT 1,
    cancel_requested INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    level TEXT NOT NULL,
    category TEXT NOT NULL,
    message TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at DESC);
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    experiment_id TEXT,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_artifacts_experiment ON artifacts(experiment_id);
"""


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


class ExperimentStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(_SCHEMA)
            current = self._get_meta("schema_version")
            if current is None:
                self._set_meta("schema_version", str(SCHEMA_VERSION))
            elif int(current) != SCHEMA_VERSION:
                raise ConflictError(
                    f"Unsupported database schema version {current}; expected {SCHEMA_VERSION}.",
                    hint="Back up the database and run the migration tool or start a new workspace.",
                )
            self._set_meta("last_opened_at", utcnow_iso())
            self._conn.commit()

    # ---- low level ---------------------------------------------------------
    def _get_meta(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM schema_meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def _set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def vacuum(self) -> None:
        with self._lock:
            self._conn.execute("VACUUM")

    # ---- experiments -------------------------------------------------------
    def create_experiment(self, experiment: ExperimentRecord) -> ExperimentRecord:
        with self._lock:
            try:
                self._conn.execute(
                    """INSERT INTO experiments(
                        experiment_id, name, kind, status, model_id, created_at,
                        started_at, finished_at, params, metrics, tags, seed,
                        config_hash, suite_hash, hardware_fingerprint, software,
                        error, notes
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        experiment.experiment_id,
                        experiment.name,
                        experiment.kind,
                        experiment.status,
                        experiment.model_id,
                        experiment.created_at,
                        experiment.started_at,
                        experiment.finished_at,
                        _dumps(experiment.params),
                        _dumps(experiment.metrics),
                        _dumps(experiment.tags),
                        experiment.seed,
                        experiment.config_hash,
                        experiment.suite_hash,
                        experiment.hardware_fingerprint,
                        _dumps(experiment.software),
                        experiment.error,
                        experiment.notes,
                    ),
                )
                self._conn.commit()
            except sqlite3.IntegrityError as exc:
                raise ConflictError(
                    f"Experiment already exists: {experiment.experiment_id}"
                ) from exc
        return experiment

    def update_experiment(self, experiment_id: str, **fields: Any) -> ExperimentRecord:
        allowed = {
            "name",
            "status",
            "model_id",
            "started_at",
            "finished_at",
            "params",
            "metrics",
            "tags",
            "seed",
            "config_hash",
            "suite_hash",
            "hardware_fingerprint",
            "software",
            "error",
            "notes",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unknown experiment fields: {sorted(unknown)}")
        if not fields:
            return self.get_experiment(experiment_id)
        json_fields = {"params", "metrics", "tags", "software"}
        clauses = []
        values: list[Any] = []
        for key, value in fields.items():
            clauses.append(f"{key} = ?")
            values.append(_dumps(value) if key in json_fields else value)
        values.append(experiment_id)
        with self._lock:
            cursor = self._conn.execute(
                f"UPDATE experiments SET {', '.join(clauses)} WHERE experiment_id = ?",
                values,
            )
            self._conn.commit()
        if cursor.rowcount == 0:
            raise NotFoundError(f"Experiment not found: {experiment_id}")
        return self.get_experiment(experiment_id)

    def get_experiment(self, experiment_id: str) -> ExperimentRecord:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM experiments WHERE experiment_id = ?", (experiment_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Experiment not found: {experiment_id}")
        return self._row_to_experiment(row)

    def list_experiments(
        self,
        *,
        kind: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ExperimentRecord]:
        query = "SELECT * FROM experiments"
        conditions: list[str] = []
        values: list[Any] = []
        if kind:
            conditions.append("kind = ?")
            values.append(kind)
        if status:
            conditions.append("status = ?")
            values.append(status)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        values.extend([max(1, min(limit, 500)), max(0, offset)])
        with self._lock:
            rows = self._conn.execute(query, values).fetchall()
        return [self._row_to_experiment(row) for row in rows]

    def _row_to_experiment(self, row: sqlite3.Row) -> ExperimentRecord:
        return ExperimentRecord(
            experiment_id=row["experiment_id"],
            name=row["name"],
            kind=row["kind"],
            status=row["status"],
            model_id=row["model_id"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            params=_loads(row["params"], {}),
            metrics=_loads(row["metrics"], {}),
            tags=_loads(row["tags"], []),
            seed=row["seed"],
            config_hash=row["config_hash"],
            suite_hash=row["suite_hash"],
            hardware_fingerprint=row["hardware_fingerprint"],
            software=_loads(row["software"], {}),
            error=row["error"],
            notes=row["notes"],
        )

    # ---- trials ------------------------------------------------------------
    def add_trial(self, trial: TrialRecord) -> TrialRecord:
        with self._lock:
            self._conn.execute(
                """INSERT INTO trials(trial_id, experiment_id, number, state, params,
                objective_values, objective_names, duration_seconds, created_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    trial.trial_id,
                    trial.experiment_id,
                    trial.number,
                    trial.state,
                    _dumps(trial.params),
                    _dumps(trial.values),
                    _dumps(trial.objective_names),
                    trial.duration_seconds,
                    trial.created_at,
                ),
            )
            self._conn.commit()
        return trial

    def list_trials(self, experiment_id: str) -> list[TrialRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM trials WHERE experiment_id = ? ORDER BY number ASC",
                (experiment_id,),
            ).fetchall()
        return [
            TrialRecord(
                trial_id=row["trial_id"],
                experiment_id=row["experiment_id"],
                number=row["number"],
                state=row["state"],
                params=_loads(row["params"], {}),
                values=_loads(row["objective_values"], []),
                objective_names=_loads(row["objective_names"], []),
                duration_seconds=row["duration_seconds"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    # ---- jobs --------------------------------------------------------------
    def create_job(self, job: JobRecord) -> JobRecord:
        with self._lock:
            self._conn.execute(
                """INSERT INTO jobs(job_id, kind, status, experiment_id, created_at,
                started_at, finished_at, progress, message, request, result, error,
                cancellable, cancel_requested)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    job.job_id,
                    job.kind,
                    job.status,
                    job.experiment_id,
                    job.created_at,
                    job.started_at,
                    job.finished_at,
                    job.progress,
                    job.message,
                    _dumps(job.request),
                    _dumps(job.result),
                    job.error,
                    int(job.cancellable),
                    int(job.cancel_requested),
                ),
            )
            self._conn.commit()
        return job

    def update_job(self, job_id: str, **fields: Any) -> JobRecord:
        allowed = {
            "status",
            "experiment_id",
            "started_at",
            "finished_at",
            "progress",
            "message",
            "result",
            "error",
            "cancellable",
            "cancel_requested",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unknown job fields: {sorted(unknown)}")
        if "progress" in fields:
            fields["progress"] = max(0.0, min(1.0, float(fields["progress"])))
        if "message" in fields and fields["message"]:
            fields["message"] = str(fields["message"])[:2000]
        if not fields:
            return self.get_job(job_id)
        clauses = []
        values: list[Any] = []
        for key, value in fields.items():
            clauses.append(f"{key} = ?")
            if key == "result":
                value = _dumps(value)
            elif key in {"cancellable", "cancel_requested"}:
                value = int(bool(value))
            values.append(value)
        values.append(job_id)
        with self._lock:
            cursor = self._conn.execute(
                f"UPDATE jobs SET {', '.join(clauses)} WHERE job_id = ?", values
            )
            self._conn.commit()
        if cursor.rowcount == 0:
            raise NotFoundError(f"Job not found: {job_id}")
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> JobRecord:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Job not found: {job_id}")
        return self._row_to_job(row)

    def list_jobs(self, limit: int = 50, status: str | None = None) -> list[JobRecord]:
        query = "SELECT * FROM jobs"
        values: list[Any] = []
        if status:
            query += " WHERE status = ?"
            values.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        values.append(max(1, min(limit, 500)))
        with self._lock:
            rows = self._conn.execute(query, values).fetchall()
        return [self._row_to_job(row) for row in rows]

    def _row_to_job(self, row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            job_id=row["job_id"],
            kind=row["kind"],
            status=row["status"],
            experiment_id=row["experiment_id"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            progress=row["progress"],
            message=row["message"],
            request=_loads(row["request"], {}),
            result=_loads(row["result"], {}),
            error=row["error"],
            cancellable=bool(row["cancellable"]),
            cancel_requested=bool(row["cancel_requested"]),
        )

    def cancel_job(self, job_id: str) -> JobRecord:
        self.get_job(job_id)
        return self.update_job(job_id, cancel_requested=True)

    # ---- events ------------------------------------------------------------
    def add_event(self, event: EventRecord) -> EventRecord:
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO events(created_at, level, category, message, data)
                VALUES (?,?,?,?,?)""",
                (
                    event.created_at,
                    event.level,
                    event.category,
                    event.message[:4000],
                    _dumps(event.data),
                ),
            )
            self._conn.commit()
            event.event_id = int(cursor.lastrowid or 0)
        return event

    def list_events(
        self, limit: int = 100, category: str | None = None
    ) -> list[EventRecord]:
        query = "SELECT * FROM events"
        values: list[Any] = []
        if category:
            query += " WHERE category = ?"
            values.append(category)
        query += " ORDER BY event_id DESC LIMIT ?"
        values.append(max(1, min(limit, 1000)))
        with self._lock:
            rows = self._conn.execute(query, values).fetchall()
        return [
            EventRecord(
                event_id=row["event_id"],
                created_at=row["created_at"],
                level=row["level"],
                category=row["category"],
                message=row["message"],
                data=_loads(row["data"], {}),
            )
            for row in rows
        ]

    # ---- artifacts ---------------------------------------------------------
    def add_artifact(self, artifact: ArtifactRecord) -> ArtifactRecord:
        with self._lock:
            self._conn.execute(
                """INSERT INTO artifacts(artifact_id, experiment_id, kind, path, sha256,
                size_bytes, created_at, metadata) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    artifact.artifact_id,
                    artifact.experiment_id,
                    artifact.kind,
                    artifact.path,
                    artifact.sha256,
                    artifact.size_bytes,
                    artifact.created_at,
                    _dumps(artifact.metadata),
                ),
            )
            self._conn.commit()
        return artifact

    def list_artifacts(self, experiment_id: str | None = None) -> list[ArtifactRecord]:
        query = "SELECT * FROM artifacts"
        values: list[Any] = []
        if experiment_id:
            query += " WHERE experiment_id = ?"
            values.append(experiment_id)
        query += " ORDER BY created_at DESC"
        with self._lock:
            rows = self._conn.execute(query, values).fetchall()
        return [
            ArtifactRecord(
                artifact_id=row["artifact_id"],
                experiment_id=row["experiment_id"],
                kind=row["kind"],
                path=row["path"],
                sha256=row["sha256"],
                size_bytes=row["size_bytes"],
                created_at=row["created_at"],
                metadata=_loads(row["metadata"], {}),
            )
            for row in rows
        ]

    # ---- maintenance -------------------------------------------------------
    def export_json(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "exported_at": utcnow_iso(),
            "experiments": [
                e.model_dump(mode="json") for e in self.list_experiments(limit=500)
            ],
            "jobs": [j.model_dump(mode="json") for j in self.list_jobs(limit=500)],
            "events": [e.model_dump(mode="json") for e in self.list_events(limit=1000)],
            "artifacts": [a.model_dump(mode="json") for a in self.list_artifacts()],
        }

    def statistics(self) -> dict[str, Any]:
        with self._lock:
            experiments = self._conn.execute(
                "SELECT kind, status, COUNT(*) AS n FROM experiments GROUP BY kind, status"
            ).fetchall()
            jobs = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
            ).fetchall()
        return {
            "experiments": {f"{r['kind']}:{r['status']}": r["n"] for r in experiments},
            "jobs": {r["status"]: r["n"] for r in jobs},
        }


def store_for(workspace_root: str | Path) -> ExperimentStore:
    return ExperimentStore(Path(workspace_root) / "data" / "fsociety.sqlite3")


def iter_experiments(store: ExperimentStore) -> Iterable[ExperimentRecord]:
    yield from store.list_experiments(limit=500)
