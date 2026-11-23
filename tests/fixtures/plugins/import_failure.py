# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixture plugin that fails at import time; discovery must not abort."""

from __future__ import annotations

raise RuntimeError("intentional fixture import failure")
