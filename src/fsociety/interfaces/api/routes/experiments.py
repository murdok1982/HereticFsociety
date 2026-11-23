# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Experiment history and report retrieval."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from ....errors import NotFoundError
from ....security.redaction import redact_obj
from ..deps import get_ctx

router = APIRouter(tags=["experiments"])


@router.get("/experiments")
def list_experiments(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    kind: str | None = Query(default=None, max_length=32),
    status_filter: str | None = Query(default=None, alias="status", max_length=32),
) -> dict:
    items = get_ctx(request).store.list_experiments(
        kind=kind, status=status_filter, limit=limit, offset=offset
    )
    return {
        "items": [item.model_dump(mode="json") for item in items],
        "total": len(items),
    }


@router.get("/experiments/{experiment_id}")
def get_experiment(request: Request, experiment_id: str) -> dict:
    return get_ctx(request).store.get_experiment(experiment_id).model_dump(mode="json")


@router.get("/experiments/{experiment_id}/trials")
def list_trials(request: Request, experiment_id: str) -> dict:
    ctx = get_ctx(request)
    ctx.store.get_experiment(experiment_id)  # 404 when unknown
    items = ctx.store.list_trials(experiment_id)
    return {
        "items": [item.model_dump(mode="json") for item in items],
        "total": len(items),
    }


@router.get("/experiments/{experiment_id}/artifacts")
def list_artifacts(request: Request, experiment_id: str) -> dict:
    ctx = get_ctx(request)
    ctx.store.get_experiment(experiment_id)
    items = ctx.store.list_artifacts(experiment_id)
    return {
        "items": [item.model_dump(mode="json") for item in items],
        "total": len(items),
    }


def _report_candidates(request: Request, experiment_id: str) -> list[Path]:
    ctx = get_ctx(request)
    reports_root = ctx.workspace.reports.resolve()
    candidates: list[Path] = []
    for artifact in ctx.store.list_artifacts(experiment_id):
        for key in ("json_path", "markdown_path", "path"):
            value = artifact.metadata.get(key) or (
                artifact.path if key == "path" else None
            )
            if value:
                candidates.append(Path(str(value)))
    if not candidates:
        candidates.extend(reports_root.rglob(f"*{experiment_id}*"))
    safe: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        if resolved == reports_root or reports_root in resolved.parents:
            safe.append(resolved)
    return safe


@router.get("/experiments/{experiment_id}/report")
def get_report(
    request: Request,
    experiment_id: str,
    format: str = Query(default="json", pattern="^(json|markdown)$"),
):
    ctx = get_ctx(request)
    ctx.store.get_experiment(experiment_id)
    candidates = _report_candidates(request, experiment_id)
    suffix = ".md" if format == "markdown" else ".json"
    matches = [path for path in candidates if path.suffix == suffix and path.is_file()]
    if not matches:
        raise NotFoundError(
            f"No {format} report is stored for experiment {experiment_id}.",
            hint=(
                "Run an evaluation job first; reports are written to the workspace "
                "reports directory."
            ),
        )
    path = matches[0]
    if format == "markdown":
        return FileResponse(path, media_type="text/markdown")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise NotFoundError("The stored report is not valid JSON.") from exc
    return JSONResponse(redact_obj(payload))
