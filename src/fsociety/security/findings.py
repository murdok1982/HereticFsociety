# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Configuration-derived security findings.

These findings reflect the *live deployment configuration* (trust policy, API
binding, plugin isolation). They are not a vulnerability scan; dependency
auditing lives in :mod:`fsociety.security.sbom`.
"""

from __future__ import annotations

from typing import Any

from ..context import PlatformContext

SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def configuration_findings(ctx: PlatformContext) -> dict[str, Any]:
    settings = ctx.settings
    findings: list[dict[str, Any]] = []
    if settings.api_host not in LOOPBACK_HOSTS and not settings.api_key:
        findings.append(
            {
                "severity": "HIGH",
                "title": "API exposed without authentication",
                "detail": f"api_host={settings.api_host!r} and no API key is configured.",
                "status": "open",
                "remediation": "Set FSOCIETY_API_KEY or bind to 127.0.0.1.",
            }
        )
    if settings.trust_remote_code == "always":
        findings.append(
            {
                "severity": "HIGH",
                "title": "trust_remote_code enabled globally",
                "detail": "Any model repository can execute arbitrary Python in this process.",
                "status": "open",
                "remediation": "Use trust_remote_code='allowlist' with explicit org/repo ids.",
            }
        )
    elif settings.trusted_repositories:
        findings.append(
            {
                "severity": "LOW",
                "title": "Remote code allowlist is configured",
                "detail": "Allowed repositories: "
                + ", ".join(settings.trusted_repositories),
                "status": "accepted",
            }
        )
    if settings.plugin_allow_network:
        findings.append(
            {
                "severity": "MEDIUM",
                "title": "Plugin network access enabled",
                "detail": "Isolated plugins may reach the network.",
                "status": "open",
                "remediation": "Set FSOCIETY_PLUGIN_ALLOW_NETWORK=false.",
            }
        )
    if not settings.plugin_isolation:
        findings.append(
            {
                "severity": "HIGH",
                "title": "Plugin isolation disabled",
                "detail": "Third-party plugin code would execute in the host process.",
                "status": "open",
                "remediation": "Enable plugin_isolation.",
            }
        )
    if "*" in settings.api_allowed_hosts:
        findings.append(
            {
                "severity": "MEDIUM",
                "title": "TrustedHost accepts any Host header",
                "detail": "api_allowed_hosts contains '*'.",
                "status": "open",
            }
        )
    if settings.api_cors_origins:
        findings.append(
            {
                "severity": "LOW",
                "title": "CORS origins configured",
                "detail": ", ".join(settings.api_cors_origins),
                "status": "accepted",
            }
        )
    engine = ctx.engine_status()
    if not engine["available"]:
        findings.append(
            {
                "severity": "INFO",
                "title": "Modification engine unavailable",
                "detail": engine["message"],
                "status": "open",
                "remediation": engine.get("hint"),
            }
        )
    findings.sort(key=lambda f: SEVERITY_ORDER.get(f["severity"], 9))
    counts = {
        level: sum(1 for f in findings if f["severity"] == level)
        for level in SEVERITY_ORDER
    }
    return {"items": findings, "total": len(findings), "counts": counts}
