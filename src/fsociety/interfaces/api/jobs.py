# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Background job executor.

Jobs are durable (SQLite) and cancellable cooperatively. Each job runs in a
thread with its own ``CancellationToken``; the token is also mirrored into the
store so a cancellation survives an API restart (the job itself cannot survive
a process restart and is marked as failed on startup).
"""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

from ...core.models import JobRecord, new_id, utcnow_iso
from ...errors import FSocietyError, JobCancelledError
from ...experiments.store import ExperimentStore
from ...logging import AuditLogger


class CancellationToken:
    def __init__(self, event: threading.Event | None = None) -> None:
        self._event = event or threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise JobCancelledError("Job was cancelled by the operator.")


class JobReporter:
    """Progress callback handed to job runners."""

    def __init__(
        self, store: ExperimentStore, job_id: str, token: CancellationToken
    ) -> None:
        self._store = store
        self._job_id = job_id
        self._token = token
        self._last = -1.0
        self._lock = threading.Lock()

    @property
    def job_id(self) -> str:
        return self._job_id

    def progress(self, fraction: float, message: str = "") -> None:
        with self._lock:
            fraction = max(0.0, min(1.0, float(fraction)))
            if abs(fraction - self._last) < 0.001 and not message:
                return
            self._last = fraction
        fields: dict[str, Any] = {"progress": fraction}
        if message:
            fields["message"] = message
        self._store.update_job(self._job_id, **fields)

    def cancel(self) -> bool:
        return self._token.is_cancelled()

    def raise_if_cancelled(self) -> None:
        self._token.raise_if_cancelled()


JobRunner = Callable[[JobReporter, CancellationToken], dict[str, Any]]


class JobManager:
    def __init__(
        self,
        store: ExperimentStore,
        logger: AuditLogger,
        *,
        max_workers: int = 2,
    ) -> None:
        self._store = store
        self._logger = logger
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, max_workers), thread_name_prefix="fsociety-job"
        )
        self._tokens: dict[str, CancellationToken] = {}
        self._futures: dict[str, Future[Any]] = {}
        self._lock = threading.RLock()
        self._closed = False
        self._recover_interrupted_jobs()

    # ---- lifecycle ---------------------------------------------------------
    def _recover_interrupted_jobs(self) -> None:
        for job in self._store.list_jobs(limit=500):
            if job.status in {"queued", "running"}:
                self._store.update_job(
                    job.job_id,
                    status="failed",
                    finished_at=utcnow_iso(),
                    error="Interrupted by API restart.",
                )
                self._logger.warning(
                    f"Job {job.job_id} was interrupted by an API restart.",
                    category="jobs",
                )

    def shutdown(self, *, wait: bool = False) -> None:
        with self._lock:
            self._closed = True
            for token in self._tokens.values():
                token.cancel()
        self._executor.shutdown(wait=wait, cancel_futures=True)

    @property
    def active_count(self) -> int:
        with self._lock:
            return sum(1 for f in self._futures.values() if not f.done())

    # ---- submission --------------------------------------------------------
    def submit(
        self,
        *,
        kind: str,
        request: dict[str, Any],
        runner: JobRunner,
        experiment_id: str | None = None,
    ) -> JobRecord:
        if self._closed:
            raise RuntimeError("Job manager is shutting down.")
        job = JobRecord(
            job_id=new_id("job"),
            kind=kind,
            status="queued",
            request=request,
            experiment_id=experiment_id,
            message="Queued",
        )
        self._store.create_job(job)
        token = CancellationToken()
        with self._lock:
            self._tokens[job.job_id] = token
            future = self._executor.submit(self._run, job.job_id, runner, token)
            self._futures[job.job_id] = future
        self._logger.info(
            f"Job {job.job_id} queued ({kind}).",
            category="jobs",
            data={"job_id": job.job_id},
        )
        return job

    def _run(self, job_id: str, runner: JobRunner, token: CancellationToken) -> None:
        reporter = JobReporter(self._store, job_id, token)
        try:
            self._store.update_job(
                job_id,
                status="running",
                started_at=utcnow_iso(),
                progress=0.01,
                message="Running",
            )
            result = runner(reporter, token)
            if token.is_cancelled():
                raise JobCancelledError("Job was cancelled by the operator.")
            self._store.update_job(
                job_id,
                status="succeeded",
                finished_at=utcnow_iso(),
                progress=1.0,
                result=result,
                message="Completed",
            )
            self._logger.info(
                f"Job {job_id} succeeded.", category="jobs", data={"job_id": job_id}
            )
        except JobCancelledError as exc:
            self._store.update_job(
                job_id,
                status="cancelled",
                finished_at=utcnow_iso(),
                error=str(exc),
                message="Cancelled",
            )
            self._logger.info(f"Job {job_id} cancelled.", category="jobs")
        except FSocietyError as exc:
            self._store.update_job(
                job_id,
                status="failed",
                finished_at=utcnow_iso(),
                error=f"{exc.code}: {exc.message}",
                message="Failed",
            )
            self._logger.error(
                f"Job {job_id} failed: {exc.message}",
                category="jobs",
                data=exc.to_dict(),
            )
        except Exception as exc:  # noqa: BLE001 - job failures must not kill the worker
            self._store.update_job(
                job_id,
                status="failed",
                finished_at=utcnow_iso(),
                error=f"{type(exc).__name__}: {str(exc)[:500]}",
                message="Failed",
            )
            self._logger.error(
                f"Job {job_id} crashed: {type(exc).__name__}", category="jobs"
            )
        finally:
            with self._lock:
                self._tokens.pop(job_id, None)

    # ---- control -----------------------------------------------------------
    def cancel(self, job_id: str) -> JobRecord:
        job = self._store.get_job(job_id)
        if job.status in {"succeeded", "failed", "cancelled"}:
            return job
        with self._lock:
            token = self._tokens.get(job_id)
            if token:
                token.cancel()
        return self._store.update_job(
            job_id, cancel_requested=True, message="Cancellation requested"
        )
