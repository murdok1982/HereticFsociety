# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Durable job manager: lifecycle, failure isolation and cancellation."""

from __future__ import annotations

import time
from pathlib import Path

from fsociety.core.models import JobRecord
from fsociety.experiments.store import ExperimentStore
from fsociety.interfaces.api.jobs import JobManager
from fsociety.logging import AuditLogger


def _manager(tmp_path: Path) -> tuple[JobManager, ExperimentStore]:
    store = ExperimentStore(tmp_path / "jobs.sqlite3")
    manager = JobManager(store, AuditLogger(None, level="DEBUG"), max_workers=2)
    return manager, store


def _wait(store: ExperimentStore, job_id: str, timeout: float = 10.0) -> JobRecord:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = store.get_job(job_id)
        if job.status in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish")


def test_job_success_and_result(tmp_path: Path) -> None:
    manager, store = _manager(tmp_path)
    try:
        job = manager.submit(
            kind="test",
            request={"x": 1},
            runner=lambda reporter, token: {"answer": 42},
        )
        finished = _wait(store, job.job_id)
        assert finished.status == "succeeded"
        assert finished.result == {"answer": 42}
        assert finished.progress == 1.0
    finally:
        manager.shutdown(wait=True)
        store.close()


def test_job_failure_is_recorded(tmp_path: Path) -> None:
    manager, store = _manager(tmp_path)
    try:

        def runner(reporter, token):
            raise RuntimeError("boom")

        job = manager.submit(kind="test", request={}, runner=runner)
        finished = _wait(store, job.job_id)
        assert finished.status == "failed"
        assert "RuntimeError" in (finished.error or "")
        assert finished.finished_at
    finally:
        manager.shutdown(wait=True)
        store.close()


def test_job_cancellation_is_cooperative(tmp_path: Path) -> None:
    manager, store = _manager(tmp_path)
    try:

        def runner(reporter, token):
            reporter.progress(0.1, "waiting")
            for _ in range(500):
                if token.is_cancelled():
                    return {"should_not_persist": True}
                time.sleep(0.01)
            return {"finished": True}

        job = manager.submit(kind="test", request={}, runner=runner)
        manager.cancel(job.job_id)
        finished = _wait(store, job.job_id)
        assert finished.status == "cancelled"
        assert finished.cancel_requested is True
        assert finished.error
    finally:
        manager.shutdown(wait=True)
        store.close()


def test_interrupted_jobs_marked_failed_on_startup(tmp_path: Path) -> None:
    store = ExperimentStore(tmp_path / "jobs.sqlite3")
    store.create_job(JobRecord(job_id="orphan", kind="test", status="running"))
    manager = JobManager(store, AuditLogger(None, level="DEBUG"))
    try:
        assert store.get_job("orphan").status == "failed"
        assert "restart" in (store.get_job("orphan").error or "").lower()
    finally:
        manager.shutdown(wait=True)
        store.close()
