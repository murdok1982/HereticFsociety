# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixture plugin: depends on a package that is not installed."""

from __future__ import annotations

from typing import Any

from fsociety import PLUGIN_API_VERSION
from fsociety.plugins.api import Scorer


class MissingDependencyScorer(Scorer):
    name = "fixture_missing_dependency"
    version = "1.0.0"
    api_version = PLUGIN_API_VERSION
    kind = "scorer"
    description = "Fixture that requests an unavailable dependency."
    author = "FSOCIETY AI tests"
    license = "AGPL-3.0-or-later"
    dependencies = ["definitely_missing_package_xyz"]
    permissions: list[str] = []

    def score(self, item: dict[str, Any]) -> float:
        return 0.0
