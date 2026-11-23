# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Deterministic hashing helpers (artifacts, models, reproducibility)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

IGNORED_PARTS = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".mypy_cache",
    ".ruff_cache",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_dir(path: Path, ignore: Iterable[str] = tuple(IGNORED_PARTS)) -> str:
    """Deterministic digest of relative names + contents (ordering by path)."""
    path = Path(path)
    ignored = set(ignore)
    digest = hashlib.sha256()
    for file in sorted(p for p in path.rglob("*") if p.is_file()):
        rel = file.relative_to(path)
        if any(part in ignored for part in rel.parts):
            continue
        digest.update(rel.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(file).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def config_hash(payload: Any) -> str:
    """Stable hash of a JSON-serializable configuration."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return sha256_bytes(encoded.encode("utf-8"))


def file_manifest(
    root: Path, relative_to: Path | None = None
) -> dict[str, dict[str, Any]]:
    root = Path(root)
    base = relative_to or root
    manifest: dict[str, dict[str, Any]] = {}
    for file in sorted(p for p in root.rglob("*") if p.is_file()):
        manifest[file.relative_to(base).as_posix()] = {
            "size": file.stat().st_size,
            "sha256": sha256_file(file),
        }
    return manifest


def verify_manifest(
    root: Path, manifest: dict[str, dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """Return ``(mismatches, missing)`` for ``manifest`` against ``root``."""
    root = Path(root)
    mismatches: list[str] = []
    missing: list[str] = []
    for rel, entry in manifest.items():
        path = root / rel
        if not path.is_file():
            missing.append(rel)
            continue
        expected = entry.get("sha256")
        if expected and sha256_file(path) != expected:
            mismatches.append(rel)
    return mismatches, missing
