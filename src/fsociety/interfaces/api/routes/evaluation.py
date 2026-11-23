# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Evaluation suites, stored reports and baseline comparison."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Query, Request

from ....core.models import BenchmarkReport
from ....errors import NotFoundError
from ....evaluation.regression import compare_reports
from ..deps import get_ctx, get_suite_registry
from ..schemas import CompareRequest

router = APIRouter(tags=["evaluation"])


@router.get("/evaluation/suites")
def list_suites(request: Request) -> dict:
    suites = get_suite_registry(request).list_suites()
    return {
        "items": [suite.model_dump(mode="json") for suite in suites],
        "total": len(suites),
    }


@router.get("/evaluation/suites/{suite_id}")
def get_suite(
    request: Request,
    suite_id: str,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    registry = get_suite_registry(request)
    suite = registry.get(suite_id)
    items = registry.load_items(suite_id)
    return {
        "suite": suite.model_dump(mode="json"),
        "items": [item.model_dump(mode="json") for item in items[:limit]],
        "total": len(items),
    }


def _find_report(request: Request, report_id: str) -> dict:
    ctx = get_ctx(request)
    reports_root = ctx.workspace.reports.resolve()
    # Artifact metadata is the fast path.
    for artifact in ctx.store.list_artifacts():
        if artifact.metadata.get("report_id") == report_id:
            candidate = Path(str(artifact.metadata.get("json_path") or artifact.path))
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if reports_root in resolved.parents and resolved.is_file():
                return json.loads(resolved.read_text(encoding="utf-8"))
    for path in reports_root.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("report_id") == report_id:
            return payload
    raise NotFoundError(
        f"Report {report_id} was not found.",
        hint="List reports from the experiment artifacts endpoint.",
    )


@router.get("/evaluation/reports/{report_id}")
def get_report(request: Request, report_id: str) -> dict:
    from ....security.redaction import redact_obj

    return redact_obj(_find_report(request, report_id))


@router.post("/evaluation/compare")
def compare(request: Request, body: CompareRequest) -> dict:
    baseline = BenchmarkReport.model_validate(
        _find_report(request, body.baseline_report_id)
    )
    candidate = BenchmarkReport.model_validate(
        _find_report(request, body.candidate_report_id)
    )
    result = compare_reports(baseline, candidate, threshold=body.threshold)
    get_ctx(request).log(
        f"Compared reports {body.baseline_report_id} → {body.candidate_report_id}: "
        f"{result.verdict}.",
        category="evaluation",
        data={"comparable": result.comparable},
    )
    return result.model_dump(mode="json")
