# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Authentication, rate limiting and client-address handling."""

from __future__ import annotations

import ipaddress
import threading
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import Header, HTTPException, Request

from ...context import PlatformContext
from ...security.policy import ApiAccessPolicy


def client_host(request: Request) -> str:
    return request.client.host if request.client and request.client.host else "unknown"


def is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        return host in {"localhost"}


def extract_api_key(
    x_api_key: str | None,
    authorization: str | None,
) -> str | None:
    if x_api_key:
        return x_api_key.strip()
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() in {"bearer", "token"} and value:
            return value.strip()
    return None


def authorize_request(
    *,
    settings: Any,
    host: str,
    provided_key: str | None,
) -> None:
    """Raise an HTTP error unless the request may proceed.

    Policy: loopback is trusted by default; non-loopback **always** requires a
    configured API key, even if the operator forgot to set one (fail closed).
    """
    if not ApiAccessPolicy.require_key(host, settings):
        return
    expected = settings.api_key_value()
    if expected is None:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "remote_access_disabled",
                "message": "Remote API access is disabled because no API key is configured.",
                "hint": "Set FSOCIETY_API_KEY and restart the API, or connect from loopback.",
            },
        )
    if provided_key is None:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "authentication_required",
                "message": "An API key is required.",
                "hint": "Send X-API-Key or Authorization: Bearer <key>.",
            },
        )
    if not ApiAccessPolicy.verify_key(provided_key, settings):
        raise HTTPException(
            status_code=401,
            detail={"code": "invalid_api_key", "message": "The API key is invalid."},
        )


async def require_auth(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
) -> None:
    ctx: PlatformContext = request.app.state.ctx
    authorize_request(
        settings=ctx.settings,
        host=client_host(request),
        provided_key=extract_api_key(x_api_key, authorization),
    )


class SlidingWindowRateLimiter:
    """Thread-safe in-memory limiter (single-process deployments).

    Multi-worker deployments must use a shared limiter (documented limitation).
    """

    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = max(1, limit)
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._last_cleanup = time.monotonic()

    def check(self, key: str) -> tuple[bool, int, float]:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            cutoff = now - self.window
            while hits and hits[0] < cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                retry_after = max(0.0, self.window - (now - hits[0]))
                return False, 0, retry_after
            hits.append(now)
            remaining = self.limit - len(hits)
            if now - self._last_cleanup > self.window * 2:
                self._cleanup(now)
                self._last_cleanup = now
            return True, remaining, max(0.0, self.window - (now - hits[0]))

    def _cleanup(self, now: float) -> None:
        cutoff = now - self.window
        stale = [
            key for key, hits in self._hits.items() if not hits or hits[-1] < cutoff
        ]
        for key in stale:
            self._hits.pop(key, None)


__all__ = [
    "SlidingWindowRateLimiter",
    "authorize_request",
    "client_host",
    "extract_api_key",
    "is_loopback",
    "require_auth",
]
