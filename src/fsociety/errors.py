# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Typed exception hierarchy for FSOCIETY AI.

Every error that can reach the CLI or the HTTP API derives from
:class:`FSocietyError` and carries a stable machine-readable ``code``. This is
what lets the API emit structured error envelopes instead of leaking stack
traces.
"""

from __future__ import annotations

from typing import Any


class FSocietyError(Exception):
    """Base class for all platform errors."""

    code = "internal_error"
    http_status = 500

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
        self.hint = hint

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        if self.hint:
            payload["hint"] = self.hint
        return payload


class ValidationError(FSocietyError):
    code = "validation_error"
    http_status = 422


class NotFoundError(FSocietyError):
    code = "not_found"
    http_status = 404


class ConflictError(FSocietyError):
    code = "conflict"
    http_status = 409


class AuthenticationError(FSocietyError):
    code = "authentication_required"
    http_status = 401


class AuthorizationError(FSocietyError):
    code = "forbidden"
    http_status = 403


class RateLimitError(FSocietyError):
    code = "rate_limited"
    http_status = 429


class PathSecurityError(FSocietyError):
    code = "unsafe_path"
    http_status = 400


class TrustPolicyError(FSocietyError):
    code = "trust_policy_denied"
    http_status = 403


class EngineUnavailableError(FSocietyError):
    """Raised when an operation requires torch/transformers and they are absent."""

    code = "engine_unavailable"
    http_status = 503

    def __init__(
        self,
        message: str = "The Heretic engine dependencies are not installed.",
        *,
        details: dict[str, Any] | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(
            message,
            details=details,
            hint=hint
            or "Install the engine profile, e.g. `uv sync --all-extras` or "
            "`pip install fsociety-ai` (which includes torch/transformers).",
        )


class PluginError(FSocietyError):
    code = "plugin_error"
    http_status = 400


class PluginTimeoutError(PluginError):
    code = "plugin_timeout"
    http_status = 408


class JobCancelledError(FSocietyError):
    code = "job_cancelled"
    http_status = 409


class EvaluationError(FSocietyError):
    code = "evaluation_error"
    http_status = 400


class IncomparableResultsError(EvaluationError):
    code = "incomparable_results"
    http_status = 422


class ExportError(FSocietyError):
    code = "export_error"
    http_status = 400


class ModelIntegrityError(FSocietyError):
    code = "model_integrity_error"
    http_status = 409
