# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Background job endpoints: resource planning, optimization and evaluation.

There is deliberately **no** endpoint that executes arbitrary Python. The
optimizer objective is a fixed analytical memory model; evaluation is a fixed
pipeline over the bundled suites and a supported model backend.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request, status

from ....core.models import JobRecord
from ....errors import ValidationError
from ....evaluation.backends import load_backend
from ....evaluation.runner import EvaluationConfig
from ....optimizer import OptimizationRequest, Optimizer, SearchSpace
from ....optimizer.resource_objective import (
    OBJECTIVE_DIRECTIONS,
    OBJECTIVE_NAMES,
    build_resource_objective,
    validate_plan_space,
)
from ..deps import (
    get_ctx,
    get_evaluation_runner,
    get_jobs,
    get_model_manager,
    get_suite_registry,
    resolve_workspace_path,
    validate_model_reference,
)
from ..schemas import EvaluationJobRequest, OptimizationJobRequest

router = APIRouter(tags=["jobs"])


@router.post(
    "/optimization/jobs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobRecord,
)
def create_optimization_job(
    request: Request, body: OptimizationJobRequest
) -> JobRecord:
    ctx = get_ctx(request)
    jobs = get_jobs(request)
    manager = get_model_manager(request)
    hardware = ctx.hardware()

    validate_model_reference(request, body.model_id)
    info = manager.inspect(body.model_id)
    if not info.parameter_count:
        raise ValidationError(
            "The selected model has no known parameter count; resource planning cannot run.",
            details={"model_id": info.model_id},
            hint="Inspect the model files or choose a model with safetensors weights.",
        )
    space = SearchSpace(**body.space.model_dump())
    dimension_names = {dimension.name for dimension in space.dimensions}
    validate_plan_space(dimension_names)
    objective = build_resource_objective(info, hardware, ctx.settings, base=body.base)

    optimization_request = OptimizationRequest(
        name=body.name,
        space=space,
        n_trials=body.n_trials,
        seed=body.seed,
        sampler=body.sampler,
        directions=list(OBJECTIVE_DIRECTIONS),
        timeout_seconds=body.timeout_seconds,
        tags=["resource-plan", "estimate"],
        model_id=info.model_id,
    )

    def runner(reporter: Any, token: Any) -> dict[str, Any]:
        optimizer = Optimizer(
            store=ctx.store,
            settings=ctx.settings,
            logger=ctx.logger,
            hardware=hardware,
        )
        result = optimizer.run(
            optimization_request,
            objective,
            progress=lambda number, total, best: reporter.progress(
                number / max(1, total), f"Trial {number}/{total}"
            ),
            cancel=reporter.cancel,
        )
        ctx.store.update_job(reporter.job_id, experiment_id=result.experiment_id)
        payload = result.model_dump(mode="json")
        payload["model_id"] = info.model_id
        payload["estimates_are"] = "analytical estimates, not measurements"
        payload["objective_names"] = OBJECTIVE_NAMES
        return payload

    job = jobs.submit(
        kind="optimization",
        request=body.model_dump(mode="json"),
        runner=runner,
    )
    ctx.log(
        f"Optimization job {job.job_id} queued for {info.model_id}.",
        category="api",
        data={"job_id": job.job_id, "model_id": info.model_id},
    )
    return job


@router.post(
    "/evaluation/jobs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobRecord,
)
def create_evaluation_job(request: Request, body: EvaluationJobRequest) -> JobRecord:
    ctx = get_ctx(request)
    jobs = get_jobs(request)
    suites = get_suite_registry(request)
    suite = suites.get(body.suite_id)  # raises NotFoundError
    evaluation_runner = get_evaluation_runner(request)

    backend_spec = body.backend.model_dump(exclude_none=True)
    if body.backend.path:
        resolve_workspace_path(request, body.backend.path)
    if body.backend.model_id:
        validate_model_reference(request, body.backend.model_id)
    config = EvaluationConfig(
        seed=body.seed if body.seed is not None else ctx.settings.evaluation_seed,
        max_new_tokens=(
            body.max_new_tokens
            if body.max_new_tokens is not None
            else ctx.settings.max_new_tokens
        ),
        temperature=(
            body.temperature
            if body.temperature is not None
            else ctx.settings.temperature
        ),
        batch_size=body.batch_size or 1,
        limit=body.limit,
        system_prompt=body.system_prompt,
    )

    def runner(reporter: Any, token: Any) -> dict[str, Any]:
        backend = load_backend(backend_spec, ctx.settings)
        report = evaluation_runner.run(
            suite.suite_id,
            backend,
            config,
            progress=lambda done, total, item: reporter.progress(
                done / max(1, total),
                f"Evaluated {getattr(item, 'item_id', 'item')} ({done}/{total})",
            ),
            cancel=reporter.cancel,
            save_to=ctx.workspace.reports,
        )
        if report.experiment_id:
            ctx.store.update_job(reporter.job_id, experiment_id=report.experiment_id)
        return {
            "report_id": report.report_id,
            "experiment_id": report.experiment_id,
            "suite_hash": report.suite_hash,
            "status": report.status,
            "metrics": report.metrics,
            "duration_seconds": report.duration_seconds,
            "warnings": report.warnings,
        }

    job = jobs.submit(
        kind="evaluation",
        request=body.model_dump(mode="json"),
        runner=runner,
    )
    ctx.log(
        f"Evaluation job {job.job_id} queued for suite {suite.suite_id}.",
        category="api",
        data={"job_id": job.job_id, "suite": suite.suite_id},
    )
    return job


@router.get("/jobs")
def list_jobs(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    status_filter: str | None = Query(default=None, alias="status", max_length=32),
) -> dict:
    items = get_ctx(request).store.list_jobs(limit=limit, status=status_filter)
    return {
        "items": [item.model_dump(mode="json") for item in items],
        "total": len(items),
    }


@router.get("/jobs/{job_id}", response_model=JobRecord)
def get_job(request: Request, job_id: str) -> JobRecord:
    return get_ctx(request).store.get_job(job_id)


@router.post("/jobs/{job_id}/cancel", response_model=JobRecord)
def cancel_job(request: Request, job_id: str) -> JobRecord:
    ctx = get_ctx(request)
    job = get_jobs(request).cancel(job_id)
    ctx.log(
        f"Cancellation requested for job {job_id}.",
        category="api",
        level="audit",
        data={"job_id": job_id},
    )
    return job
