# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""FastAPI dependency accessors.

Components are created lazily and cached on ``app.state`` so that the API can
start without torch, without a model directory and without plugins.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Request

from ...context import PlatformContext
from ...errors import PathSecurityError
from ...paths import safe_join
from .jobs import JobManager


def get_ctx(request: Request) -> PlatformContext:
    return request.app.state.ctx


def get_jobs(request: Request) -> JobManager:
    return request.app.state.jobs


def get_model_manager(request: Request) -> Any:
    manager = getattr(request.app.state, "model_manager", None)
    if manager is None:
        from ...core.model_manager import ModelManager

        manager = ModelManager(
            request.app.state.ctx.settings, request.app.state.ctx.logger
        )
        request.app.state.model_manager = manager
    return manager


def get_suite_registry(request: Request) -> Any:
    registry = getattr(request.app.state, "suite_registry", None)
    if registry is None:
        from ...evaluation.suites import SuiteRegistry

        registry = SuiteRegistry()
        request.app.state.suite_registry = registry
    return registry


def get_evaluation_runner(request: Request) -> Any:
    runner = getattr(request.app.state, "evaluation_runner", None)
    if runner is None:
        from ...evaluation.runner import EvaluationRunner

        ctx: PlatformContext = request.app.state.ctx
        runner = EvaluationRunner(
            store=ctx.store,
            logger=ctx.logger,
            hardware=ctx.hardware(),
        )
        request.app.state.evaluation_runner = runner
    return runner


def get_plugin_registry(request: Request) -> Any:
    registry = getattr(request.app.state, "plugin_registry", None)
    if registry is None:
        from ...plugins.registry import PluginRegistry

        ctx: PlatformContext = request.app.state.ctx
        registry = PluginRegistry(
            [ctx.workspace.plugins], logger=ctx.logger, settings=ctx.settings
        )
        request.app.state.plugin_registry = registry
    return registry


def _looks_like_path(value: str) -> bool:
    """True for filesystem references, false for Hub repo ids like ``org/name``."""
    if value.startswith(("/", "~", ".")) or "\\" in value:
        return True
    try:
        return Path(value).is_absolute()
    except (OSError, ValueError):
        return False


def validate_model_reference(request: Request, reference: str) -> None:
    """Reject filesystem model references outside the allowed workspace roots.

    Hub repository ids (``org/name``) are not filesystem paths and pass through;
    the model manager applies the trust/offline policy when resolving them.
    """
    if _looks_like_path(reference):
        resolve_workspace_path(request, reference)


def resolve_workspace_path(request: Request, relative: str) -> Path:
    """Resolve a user-supplied path inside the workspace (or models dir)."""
    ctx: PlatformContext = request.app.state.ctx
    if Path(relative).is_absolute():
        if not ctx.settings.allow_absolute_model_paths:
            raise PathSecurityError("Absolute paths are disabled in this deployment.")
        candidate = Path(relative).expanduser().resolve()
        allowed = {ctx.workspace.root, ctx.settings.resolved_models_dir().resolve()}
        if not any(candidate == root or root in candidate.parents for root in allowed):
            raise PathSecurityError(
                "Absolute path is outside the workspace and configured model directory.",
                details={"path": relative},
            )
        return candidate
    return safe_join(ctx.workspace.root, relative)
