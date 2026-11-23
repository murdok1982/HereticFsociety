# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixture plugin: looks like a scorer but does not subclass PluginBase."""

from __future__ import annotations

from typing import Any


class WrongBaseScorer:
    name = "fixture_wrong_base"
    version = "1.0.0"
    api_version = 1
    kind = "scorer"
    description = "Fixture with the wrong base class."
    author = "FSOCIETY AI tests"
    license = "AGPL-3.0-or-later"
    dependencies: list[str] = []
    permissions: list[str] = []

    def score(self, item: dict[str, Any]) -> float:
        return 0.0
