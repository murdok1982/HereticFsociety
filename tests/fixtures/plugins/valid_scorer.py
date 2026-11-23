# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixture plugin: a valid workspace scorer."""

from __future__ import annotations

from typing import Any

from fsociety import PLUGIN_API_VERSION
from fsociety.plugins.api import Scorer


class ValidScorer(Scorer):
    name = "fixture_valid"
    version = "1.0.0"
    api_version = PLUGIN_API_VERSION
    kind = "scorer"
    description = "Valid fixture scorer used by discovery/isolation tests."
    author = "FSOCIETY AI tests"
    license = "AGPL-3.0-or-later"
    dependencies: list[str] = []
    permissions: list[str] = []

    def score(self, item: dict[str, Any]) -> float:
        return 0.5
