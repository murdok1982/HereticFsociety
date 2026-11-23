# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors
#
# FSOCIETY AI compatibility shim. This file is the only engine touch-point for the
# distribution rename: it keeps the historical "heretic-llm" lookups working when
# the project is installed as "fsociety-ai".

"""Distribution metadata lookup with a historical-name fallback."""

from __future__ import annotations

import importlib.metadata
from typing import Any

#: The historical distribution name (Heretic <= 2.2) and the current one.
DISTRIBUTION_ALIASES: tuple[str, ...] = ("heretic-llm", "fsociety-ai")


def get_distribution(name: str = "heretic-llm") -> importlib.metadata.Distribution:
    """Return the installed distribution, resolving the rename alias.

    Raises ``importlib.metadata.PackageNotFoundError`` when neither name is
    installed, preserving the upstream failure mode.
    """
    candidates = [name]
    if name in DISTRIBUTION_ALIASES:
        candidates.extend(alias for alias in DISTRIBUTION_ALIASES if alias != name)
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            return importlib.metadata.distribution(candidate)
        except importlib.metadata.PackageNotFoundError as exc:
            last_error = exc
    raise last_error or importlib.metadata.PackageNotFoundError(name)


def get_version(name: str = "heretic-llm") -> str:
    """Version of the engine distribution, regardless of its current name."""
    return get_distribution(name).version


def is_engine_distribution(name: str) -> bool:
    return name in DISTRIBUTION_ALIASES


def distribution_metadata(name: str = "heretic-llm") -> dict[str, Any]:
    dist = get_distribution(name)
    return {"name": dist.metadata["Name"], "version": dist.version}
