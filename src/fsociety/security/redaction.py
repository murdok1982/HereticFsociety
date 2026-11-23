# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Secret redaction.

Applied to every log line, API error envelope, audit record and report so that
credential material cannot leak through diagnostics.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

REDACTED = "[REDACTED]"

# Patterns are intentionally conservative: better to redact a benign string
# than to leak a credential into a report.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("hf_token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}\b")),
    ("aws_access_key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{30,})\b")),
    ("google_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b")),
    ("bearer", re.compile(r"(?i)\bBearer\s+([A-Za-z0-9\-._~+/]{12,}=*)")),
    ("basic_auth", re.compile(r"(?i)\bBasic\s+([A-Za-z0-9+/]{16,}=*)")),
    (
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    (
        "generic_secret_assignment",
        re.compile(
            r"(?i)\b(api[_-]?key|secret|token|password|passwd)\b(\s*[:=]\s*)['\"]?([^\s'\"]{8,})"
        ),
    ),
)


def redact(text: str) -> str:
    """Return ``text`` with credential-like substrings replaced."""
    if not text:
        return text
    result = text
    for name, pattern in _SECRET_PATTERNS:
        if name == "generic_secret_assignment":
            result = pattern.sub(
                lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", result
            )
        elif name in {"bearer", "basic_auth"}:
            result = pattern.sub(
                lambda m: m.group(0).split()[0] + f" {REDACTED}", result
            )
        else:
            result = pattern.sub(REDACTED, result)
    return result


def redact_obj(value: Any) -> Any:
    """Recursively redact strings inside JSON-like structures."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: redact_obj(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_obj(v) for v in value]
    return value


_FINDING_PATTERN_NAMES = {
    "hf_token",
    "openai_key",
    "anthropic_key",
    "aws_access_key",
    "github_token",
    "google_key",
    "private_key",
}
_FINDING_PATTERNS = tuple(
    pattern for name, pattern in _SECRET_PATTERNS if name in _FINDING_PATTERN_NAMES
)


def redact_tree(root: Path, patterns: Iterable[str] = ()) -> list[str]:
    """Report (without modifying) files containing credential-shaped strings.

    Only high-confidence patterns are used for file scanning: generic
    ``token = ...`` heuristics are appropriate for log redaction but produce
    constant false positives in documentation and source code.
    """
    findings: list[str] = []
    extra = [re.compile(p) for p in patterns]
    root = Path(root)
    if root.is_file():
        try:
            text = root.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return findings
        if any(p.search(text) for p in _FINDING_PATTERNS) or any(
            p.search(text) for p in extra
        ):
            findings.append(str(root))
        return findings
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d
            for d in dirnames
            if d
            not in {
                ".git",
                "node_modules",
                ".venv",
                "venv",
                "__pycache__",
                "dist",
                ".factory",
                ".pytest_cache",
                ".ruff_cache",
                ".mypy_cache",
            }
        ]
        for filename in filenames:
            path = Path(dirpath) / filename
            if path.suffix.lower() in {
                ".png",
                ".jpg",
                ".jpeg",
                ".gif",
                ".ico",
                ".zip",
                ".bin",
                ".safetensors",
                ".pt",
                ".gguf",
                ".lock",
            }:
                continue
            try:
                if path.stat().st_size > 2_000_000:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if any(p.search(text) for p in _FINDING_PATTERNS) or any(
                p.search(text) for p in extra
            ):
                findings.append(str(path))
    return findings
