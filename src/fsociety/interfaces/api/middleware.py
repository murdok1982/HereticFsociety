# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""HTTP hardening: request IDs, security headers, rate limiting, body limits."""

from __future__ import annotations

import uuid
from collections.abc import MutableMapping
from typing import Awaitable, Callable, cast
from urllib.parse import urlsplit

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from ...security.headers import apply_security_headers
from .auth import SlidingWindowRateLimiter, client_host

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

RequestHandler = Callable[[Request], Awaitable[Response]]


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        request_id = request.headers.get("X-Request-ID")
        if not request_id or len(request_id) > 128 or not request_id.isprintable():
            request_id = uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        apply_security_headers(cast(MutableMapping[str, str], response.headers))
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


class OriginCheckMiddleware(BaseHTTPMiddleware):
    """Block cross-site state changes against loopback-trusted endpoints.

    Requests without an ``Origin`` header (CLI, curl) are unaffected. A present
    Origin must be same-origin or explicitly allowed by CORS configuration;
    ``null`` origins are rejected.
    """

    def __init__(self, app, allowed_origins: list[str] | None = None) -> None:
        super().__init__(app)
        self.allowed_origins = {
            origin.rstrip("/") for origin in (allowed_origins or [])
        }

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        if request.method.upper() in MUTATING_METHODS:
            origin = request.headers.get("origin")
            if origin is not None:
                normalized = origin.rstrip("/")
                host = request.headers.get("host", "")
                same_origin = False
                try:
                    parsed = urlsplit(normalized)
                    same_origin = bool(parsed.netloc and parsed.netloc == host)
                    # The Vite dev server proxies API calls from another loopback
                    # port; a local origin is already within the loopback trust
                    # boundary and is not a cross-site attacker.
                    same_origin = same_origin or parsed.hostname in {
                        "localhost",
                        "127.0.0.1",
                        "::1",
                    }
                except ValueError:
                    same_origin = False
                if normalized == "null" or not (
                    same_origin or normalized in self.allowed_origins
                ):
                    return JSONResponse(
                        status_code=403,
                        content={
                            "error": {
                                "code": "origin_not_allowed",
                                "message": (
                                    "Cross-origin state-changing requests are not allowed."
                                ),
                                "hint": (
                                    "Send no Origin header (CLI) or use the configured "
                                    "dashboard origin."
                                ),
                            }
                        },
                    )
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, limit_per_minute: int = 120) -> None:
        super().__init__(app)
        self._limiter = SlidingWindowRateLimiter(limit_per_minute, 60.0)

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        # Static assets and the SPA shell are not API traffic. Rate limiting them
        # would break the dashboard after ~limit GETs (429 served as JS/CSS).
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        key = f"{client_host(request)}:{request.method}"
        allowed, remaining, retry_after = self._limiter.check(key)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "rate_limited",
                        "message": "Too many requests.",
                        "details": {"retry_after_seconds": round(retry_after, 1)},
                    }
                },
                headers={"Retry-After": str(int(retry_after) + 1)},
            )
        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_bytes: int = 1_000_000) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        if request.method in MUTATING_METHODS:
            length = request.headers.get("content-length")
            if length is None and request.headers.get("transfer-encoding"):
                return JSONResponse(
                    status_code=413,
                    content={
                        "error": {
                            "code": "payload_too_large",
                            "message": "Chunked request bodies must declare Content-Length.",
                        }
                    },
                )
            if request.headers.get("content-encoding", "identity").lower() not in {
                "identity",
                "",
            }:
                return JSONResponse(
                    status_code=415,
                    content={
                        "error": {
                            "code": "unsupported_encoding",
                            "message": "Compressed request bodies are not accepted.",
                        }
                    },
                )
            if length is not None:
                try:
                    if int(length) > self.max_bytes:
                        return JSONResponse(
                            status_code=413,
                            content={
                                "error": {
                                    "code": "payload_too_large",
                                    "message": f"Request body exceeds {self.max_bytes} bytes.",
                                }
                            },
                        )
                except ValueError:
                    return JSONResponse(
                        status_code=400,
                        content={
                            "error": {
                                "code": "invalid_content_length",
                                "message": "Malformed Content-Length header.",
                            }
                        },
                    )
        return await call_next(request)


def _client_key(request: Request) -> str:
    return client_host(request)
