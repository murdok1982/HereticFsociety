# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""HTTP API package."""

from __future__ import annotations

from .app import create_app

__all__ = ["create_app"]
