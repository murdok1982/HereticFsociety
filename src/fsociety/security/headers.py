# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""HTTP security headers applied by the API layer.

Kept as a plain dict + helper so the API can import it without FastAPI and so
tests can assert the exact policy. The CSP allows same-origin resources only.
"""

from __future__ import annotations

from collections.abc import MutableMapping

SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": (
        "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
        "magnetometer=(), microphone=(), payment=(), usb=()"
    ),
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def apply_security_headers(
    response_headers: MutableMapping[str, str],
) -> MutableMapping[str, str]:
    """Merge :data:`SECURITY_HEADERS` into ``response_headers`` in place."""
    response_headers.update(SECURITY_HEADERS)
    return response_headers
