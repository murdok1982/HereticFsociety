# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Static frontend security checks (no Node build required)."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND = REPO_ROOT / "frontend"
SRC = FRONTEND / "src"


def _sources() -> dict[Path, str]:
    return {
        path: path.read_text(encoding="utf-8")
        for path in sorted(SRC.rglob("*"))
        if path.suffix in {".ts", ".tsx"}
    }


def test_no_html_injection_primitives() -> None:
    forbidden = (
        "dangerouslySetInnerHTML",
        ".innerHTML",
        ".outerHTML",
        "document.write",
        "document.cookie",
        "eval(",
        "new Function(",
    )
    problems = [
        f"{path}: {needle}"
        for path, text in _sources().items()
        for needle in forbidden
        if needle in text
    ]
    assert not problems, problems


def test_api_key_is_only_kept_in_session_storage() -> None:
    api_key_source = (SRC / "lib" / "apiKey.ts").read_text(encoding="utf-8")
    assert "sessionStorage" in api_key_source
    assert "localStorage" not in api_key_source
    assert "document.cookie" not in api_key_source

    for path, text in _sources().items():
        if path.name == "apiKey.ts":
            continue
        assert "localStorage" not in text, path
        assert "document.cookie" not in text, path


def test_api_key_is_sent_as_a_header_not_in_urls() -> None:
    client = (SRC / "api" / "client.ts").read_text(encoding="utf-8")
    assert 'headers["X-API-Key"] = apiKey' in client
    assert "api_key=" not in client
    assert "Authorization" not in client


def test_index_html_has_no_inline_scripts() -> None:
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    scripts = re.findall(r"<script\b[^>]*>", html, flags=re.IGNORECASE)
    assert scripts, "entry script missing"
    for tag in scripts:
        assert "src=" in tag, tag
        assert 'type="module"' in tag, tag


def test_csp_matches_the_dashboard_build() -> None:
    from fsociety.security.headers import SECURITY_HEADERS

    csp = SECURITY_HEADERS["Content-Security-Policy"]
    assert "script-src 'self'" in csp
    assert "unsafe-eval" not in csp
    assert "unsafe-inline" not in csp.split("script-src", 1)[1].split(";", 1)[0]
    # Fonts and the favicon are bundled/self-hosted; data: is limited to images
    # and fonts, never to scripts.
    assert "font-src 'self' data:" in csp
    assert "img-src 'self' data:" in csp


def test_frontend_build_disables_sourcemaps() -> None:
    vite = (FRONTEND / "vite.config.ts").read_text(encoding="utf-8")
    assert "sourcemap: false" in vite
