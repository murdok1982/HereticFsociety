# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixture plugin used to prove the isolation timeout kills runaway code."""

from __future__ import annotations

from typing import Any

from fsociety import PLUGIN_API_VERSION
from fsociety.plugins.api import Scorer


class LoopingScorer(Scorer):
    name = "fixture_looping"
    version = "1.0.0"
    api_version = PLUGIN_API_VERSION
    kind = "scorer"
    description = "Fixture that loops forever."
    author = "FSOCIETY AI tests"
    license = "AGPL-3.0-or-later"
    dependencies: list[str] = []
    permissions: list[str] = []

    def score(self, item: dict[str, Any]) -> float:
        while True:
            pass
