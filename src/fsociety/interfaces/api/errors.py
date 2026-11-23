# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Structured error handling.

No endpoint is allowed to leak a traceback, file path or secret. Errors are
returned as ``{"error": {"code", "message", "details"?, "hint"?}}`` with a
request ID that matches the server audit log.
"""

from __future__ import annotations

import traceback
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ...errors import FSocietyError
from ...security.redaction import redact_obj

_HTTP_CODES = {
    400: "bad_request",
    401: "authentication_required",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    408: "timeout",
    413: "payload_too_large",
    415: "unsupported_media_type",
    429: "rate_limited",
}


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "") or ""


def _envelope(error: dict[str, Any], request_id: str) -> dict[str, Any]:
    if request_id:
        error["request_id"] = request_id
    return {"error": redact_obj(error)}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(FSocietyError)
    async def fsociety_error(request: Request, exc: FSocietyError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content=_envelope(exc.to_dict(), _request_id(request)),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = []
        for item in exc.errors()[:20]:
            loc = ".".join(str(part) for part in item.get("loc", ()) if part != "body")
            details.append(
                {"field": loc or "request", "message": item.get("msg", "invalid")}
            )
        return JSONResponse(
            status_code=422,
            content=_envelope(
                {
                    "code": "validation_error",
                    "message": "Request validation failed.",
                    "details": {"errors": details},
                },
                _request_id(request),
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict):
            error = dict(detail)
            error.setdefault("code", f"http_{exc.status_code}")
            error.setdefault("message", "Request failed.")
        else:
            error = {
                "code": _HTTP_CODES.get(exc.status_code, f"http_{exc.status_code}"),
                "message": str(detail),
            }
        response = JSONResponse(
            status_code=exc.status_code,
            content=_envelope(error, _request_id(request)),
            headers=getattr(exc, "headers", None),
        )
        return response

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        ctx = getattr(request.app.state, "ctx", None)
        request_id = _request_id(request)
        if ctx is not None:
            ctx.logger.error(
                f"Unhandled API error: {type(exc).__name__}",
                category="api",
                data={"request_id": request_id, "path": request.url.path},
            )
        else:  # pragma: no cover - only before startup
            traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content=_envelope(
                {
                    "code": "internal_error",
                    "message": "An internal error occurred.",
                    "hint": "Check the server audit log with the request ID.",
                },
                request_id,
            ),
        )
