# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""System, health and hardware endpoints."""

from __future__ import annotations

import shutil

from fastapi import APIRouter, Query, Request

from .... import __version__
from ....core.models import HardwareProfile, HealthStatus, SystemInfo
from ..deps import get_ctx

public_router = APIRouter(tags=["system"])
router = APIRouter(tags=["system"])


@public_router.get("/health", response_model=HealthStatus)
def health(request: Request) -> HealthStatus:
    ctx = get_ctx(request)
    checks: dict[str, str] = {}
    overall = "ok"
    try:
        ctx.store.statistics()
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 - health must report, not raise
        checks["database"] = f"error: {type(exc).__name__}"
        overall = "degraded"
    try:
        probe = ctx.workspace.data / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks["workspace_writable"] = "ok"
    except OSError:
        checks["workspace_writable"] = "read-only"
        overall = "degraded"
    usage = shutil.disk_usage(ctx.workspace.root)
    checks["disk_free_gib"] = f"{usage.free / 1024**3:.1f}"
    engine = ctx.engine_status()
    checks["engine"] = "available" if engine["available"] else "unavailable"
    return HealthStatus(
        status=overall,  # type: ignore[arg-type]
        checks=checks,
        version=__version__,
        uptime_seconds=round(ctx.uptime_seconds(), 3),
    )


@public_router.get("/version", response_model=SystemInfo)
def version(request: Request) -> SystemInfo:
    return get_ctx(request).system_info()


@router.get("/system", response_model=SystemInfo)
def system(request: Request) -> SystemInfo:
    return get_ctx(request).system_info()


@router.get("/system/engine")
def engine(request: Request) -> dict:
    return get_ctx(request).engine_status()


@router.get("/hardware", response_model=HardwareProfile)
def hardware(
    request: Request,
    refresh: bool = Query(
        default=False, description="Re-run detection instead of using the cache."
    ),
) -> HardwareProfile:
    return get_ctx(request).hardware(refresh=refresh)


@router.get("/system/workspace")
def workspace(request: Request) -> dict:
    ctx = get_ctx(request)
    return {
        **ctx.workspace.describe(),
        "settings_file": str(ctx.settings.config_file)
        if ctx.settings.config_file
        else None,
        "offline": ctx.settings.offline,
    }


@router.get("/events")
def events(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    category: str | None = Query(default=None, max_length=64),
) -> dict:
    items = get_ctx(request).store.list_events(limit=limit, category=category)
    return {
        "items": [item.model_dump(mode="json") for item in items],
        "total": len(items),
    }


@router.get("/settings")
def settings(request: Request) -> dict:
    ctx = get_ctx(request)
    data = ctx.settings.redacted()
    data["workspace_layout"] = ctx.workspace.describe()
    return data
