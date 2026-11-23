# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Plugins, export bundles, security posture and SBOM endpoints."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, Query, Request

from ....core.models import ArtifactRecord, new_id, utcnow_iso
from ....errors import NotFoundError, ValidationError
from ....security.findings import configuration_findings
from ....security.policy import TrustPolicy
from ....security.sbom import dependency_audit, generate_cyclonedx
from ....utils.hashing import sha256_dir
from ..deps import (
    get_ctx,
    get_plugin_registry,
    resolve_workspace_path,
)
from ..schemas import ExportBundleRequest, PluginRunRequest

router = APIRouter(tags=["plugins"])


# ---------------------------------------------------------------------------
# Plugins
# ---------------------------------------------------------------------------


@router.get("/plugins")
def list_plugins(request: Request) -> dict:
    items = get_plugin_registry(request).list()
    return {
        "items": [item.model_dump(mode="json") for item in items],
        "total": len(items),
    }


@router.post("/plugins/reload")
def reload_plugins(request: Request) -> dict:
    request.app.state.plugin_registry = None
    items = get_plugin_registry(request).list()
    get_ctx(request).log("Plugin registry reloaded.", category="plugins", level="audit")
    return {
        "items": [item.model_dump(mode="json") for item in items],
        "total": len(items),
    }


@router.post("/plugins/{name}/run")
def run_plugin(request: Request, name: str, body: PluginRunRequest) -> dict:
    ctx = get_ctx(request)
    registry = get_plugin_registry(request)
    result = registry.run(
        name,
        body.method,
        body.payload,
        allow_in_process=body.allow_in_process,
    )
    ctx.log(
        f"Plugin {name} executed method {body.method}.",
        category="plugins",
        level="audit",
        data={"plugin": name, "method": body.method},
    )
    return {"plugin": name, "method": body.method, "result": result}


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------


@router.get("/exports")
def list_exports(request: Request) -> dict:
    items = [
        artifact
        for artifact in get_ctx(request).store.list_artifacts()
        if artifact.kind.startswith("export")
    ]
    return {
        "items": [item.model_dump(mode="json") for item in items],
        "total": len(items),
    }


@router.post("/exports/bundle")
def create_bundle(request: Request, body: ExportBundleRequest) -> dict:
    from ....exporters.manifest import create_export_bundle

    ctx = get_ctx(request)
    files: list[Path] = []
    for relative in body.files:
        path = resolve_workspace_path(request, relative)
        if not path.is_file():
            raise NotFoundError(f"File not found: {relative}")
        files.append(path)
    destination = resolve_workspace_path(request, body.destination)
    exports_root = ctx.workspace.exports.resolve()
    if not (destination == exports_root or exports_root in destination.parents):
        raise ValidationError(
            "Export destination must be inside the workspace exports directory."
        )
    result = create_export_bundle(
        destination,
        files=files,
        metadata={
            "created_by": "fsociety-ai",
            "request": body.metadata,
            "file_count": len(files),
        },
    )
    artifact = ArtifactRecord(
        artifact_id=new_id("artifact"),
        kind="export_bundle",
        path=str(destination),
        sha256=sha256_dir(destination),
        size_bytes=sum(f.stat().st_size for f in files),
        created_at=utcnow_iso(),
        metadata={"result": result, "files": [str(f) for f in files]},
    )
    ctx.store.add_artifact(artifact)
    ctx.log(
        f"Export bundle created at {destination}.",
        category="exports",
        level="audit",
        data={"artifact_id": artifact.artifact_id},
    )
    return {
        "artifact": artifact.model_dump(mode="json"),
        "bundle": result,
    }


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


@router.get("/security/policy")
def security_policy(request: Request) -> dict:
    ctx = get_ctx(request)
    policy = TrustPolicy(ctx.settings)
    return policy.summary()


@router.get("/security/audit")
def security_audit(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    items = get_ctx(request).store.list_events(limit=limit)
    return {
        "items": [item.model_dump(mode="json") for item in items],
        "total": len(items),
    }


@router.get("/security/findings")
def security_findings(request: Request) -> dict:
    """Configuration findings derived from live settings - not a CVE scan."""
    return configuration_findings(get_ctx(request))


@router.get("/security/sbom")
def sbom(request: Request) -> dict:
    ctx = get_ctx(request)
    cache = getattr(request.app.state, "sbom_cache", None)
    now = time.time()
    if cache and now - cache[0] < 300:
        return cache[1]
    payload = generate_cyclonedx()
    request.app.state.sbom_cache = (now, payload)
    ctx.log("SBOM generated.", category="security", level="audit")
    return payload


@router.get("/security/dependency-audit")
def dependency_audit_endpoint(
    request: Request,
    refresh: bool = Query(default=False),
) -> dict:
    cache = getattr(request.app.state, "audit_cache", None)
    now = time.time()
    if cache and not refresh and now - cache[0] < 600:
        return cache[1]
    payload = dependency_audit()
    request.app.state.audit_cache = (now, payload)
    return payload
