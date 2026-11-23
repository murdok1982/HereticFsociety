# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Model discovery, inspection, estimation and integrity endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query, Request

from ....core.estimator import EstimationRequest, estimate_memory
from ....errors import NotFoundError, PathSecurityError, ValidationError
from ..deps import (
    get_ctx,
    get_model_manager,
    resolve_workspace_path,
    validate_model_reference,
)
from ..schemas import ScanRequest, VerifyRequest

router = APIRouter(tags=["models"])


@router.get("/models")
def list_models(
    request: Request,
    include_hf_cache: bool = Query(default=True),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict:
    ctx = get_ctx(request)
    manager = get_model_manager(request)
    roots = [ctx.settings.resolved_models_dir()]
    items = manager.scan(roots=roots, include_hf_cache=include_hf_cache, limit=limit)
    return {
        "items": [item.model_dump(mode="json") for item in items],
        "total": len(items),
    }


@router.post("/models/scan")
def scan_models(request: Request, body: ScanRequest) -> dict:
    ctx = get_ctx(request)
    roots: list[Path] = []
    for root in body.roots:
        resolved = resolve_workspace_path(request, root)
        if not resolved.is_dir():
            raise NotFoundError(f"Directory not found: {root}")
        roots.append(resolved)
    if not roots:
        roots = [ctx.settings.resolved_models_dir()]
    manager = get_model_manager(request)
    items = manager.scan(roots=roots, include_hf_cache=body.include_hf_cache, limit=500)
    return {
        "items": [item.model_dump(mode="json") for item in items],
        "total": len(items),
    }


@router.get("/models/inspect")
def inspect_model(
    request: Request,
    reference: str = Query(min_length=1, max_length=1024),
    revision: str | None = Query(default=None, max_length=128),
    hash_files: bool = Query(default=False),
) -> dict:
    validate_model_reference(request, reference)
    manager = get_model_manager(request)
    info = manager.inspect(reference, revision=revision, hash_files=hash_files)
    return info.model_dump(mode="json")


@router.post("/models/verify")
def verify_model(request: Request, body: VerifyRequest) -> dict:
    validate_model_reference(request, body.reference)
    manager = get_model_manager(request)
    manifest = None
    manifest_path: Path | None = None
    if body.manifest_path:
        manifest_path = resolve_workspace_path(request, body.manifest_path)
        if not manifest_path.is_file():
            raise NotFoundError("Manifest file not found.")
    validation = manager.verify(
        body.reference, manifest=manifest, manifest_path=manifest_path
    )
    return validation.model_dump(mode="json")


@router.get("/models/estimate")
def estimate(
    request: Request,
    reference: str = Query(min_length=1, max_length=1024),
    batch_size: int = Query(default=1, ge=1, le=4096),
    sequence_length: int = Query(default=2048, ge=16, le=1_048_576),
    dtype: str = Query(default="bfloat16", max_length=32),
    quantization: str = Query(default="none", max_length=32),
    kv_cache_dtype: str = Query(default="bfloat16", max_length=32),
) -> dict:
    ctx = get_ctx(request)
    validate_model_reference(request, reference)
    manager = get_model_manager(request)
    info = manager.inspect(reference)
    if not info.parameter_count:
        raise ValidationError(
            "Parameter count is unknown for this model; estimation is not possible.",
            details={"model_id": info.model_id},
            hint="Inspect the model files (safetensors headers) or provide a local model directory.",
        )
    try:
        estimate_obj = estimate_memory(
            info,
            EstimationRequest(
                batch_size=batch_size,
                sequence_length=sequence_length,
                dtype=dtype,
                quantization=quantization,
                kv_cache_dtype=kv_cache_dtype,
            ),
            ctx.hardware(),
        )
    except PathSecurityError:  # pragma: no cover - estimator does not touch paths
        raise
    return {
        "model": info.model_dump(mode="json"),
        "estimate": estimate_obj.model_dump(mode="json"),
        "hardware": ctx.hardware().model_dump(mode="json"),
    }
