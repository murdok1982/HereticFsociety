# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Filesystem safety and workspace layout.

All platform paths are resolved through :func:`safe_join`, which prevents
``..`` traversal and symlink escapes. Anything accepting a user path (API
query, CLI argument, plugin artifact) must use it.
"""

from __future__ import annotations

import os
from pathlib import Path

from .errors import PathSecurityError

#: Maximum size we are willing to hash in one go for ad-hoc operations.
MAX_INSPECT_BYTES = 512 * 1024 * 1024


def safe_join(base: Path, *parts: str | os.PathLike[str]) -> Path:
    """Join ``parts`` onto ``base`` and guarantee the result stays inside it.

    The check is performed on the *resolved* path so symlinks cannot escape the
    workspace, even if the link itself lives inside it.
    """
    base_resolved = Path(base).expanduser().resolve()
    candidate = base_resolved
    for part in parts:
        text = str(part)
        if not text:
            continue
        # Reject absolute paths outright; "C:\\x" on Windows, "/x" on POSIX.
        if Path(text).is_absolute() or text.startswith(("/", "\\")):
            raise PathSecurityError(
                f"Absolute paths are not allowed here: {text!r}",
                details={"base": str(base_resolved)},
            )
        candidate = candidate / text

    resolved = candidate.expanduser().resolve()
    if resolved != base_resolved and base_resolved not in resolved.parents:
        raise PathSecurityError(
            "Path escapes the allowed directory.",
            details={"base": str(base_resolved), "requested": str(parts)},
            hint="Use a relative path inside the workspace.",
        )
    return resolved


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


class Workspace:
    """Directory layout for all mutable platform state."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # ---- managed directories ------------------------------------------------
    @property
    def data(self) -> Path:
        return ensure_dir(self.root / "data")

    @property
    def db_path(self) -> Path:
        return self.data / "fsociety.sqlite3"

    @property
    def models(self) -> Path:
        return ensure_dir(self.root / "models")

    @property
    def exports(self) -> Path:
        return ensure_dir(self.root / "exports")

    @property
    def reports(self) -> Path:
        return ensure_dir(self.root / "reports")

    @property
    def logs(self) -> Path:
        return ensure_dir(self.root / "logs")

    @property
    def plugins(self) -> Path:
        return ensure_dir(self.root / "plugins")

    @property
    def cache(self) -> Path:
        return ensure_dir(self.root / "cache")

    @property
    def audits(self) -> Path:
        return ensure_dir(self.root / "audits")

    def resolve(self, relative: str | Path, *, base: Path | None = None) -> Path:
        return safe_join(base or self.root, relative)

    def describe(self) -> dict[str, str]:
        return {
            "root": str(self.root),
            "data": str(self.data),
            "models": str(self.models),
            "exports": str(self.exports),
            "reports": str(self.reports),
            "logs": str(self.logs),
            "plugins": str(self.plugins),
        }
