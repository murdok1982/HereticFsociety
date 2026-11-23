# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixture plugin: requests an unknown privilege."""

from __future__ import annotations

from typing import Any

from fsociety import PLUGIN_API_VERSION
from fsociety.plugins.api import Scorer


class UnknownPermissionScorer(Scorer):
    name = "fixture_unknown_permission"
    version = "1.0.0"
    api_version = PLUGIN_API_VERSION
    kind = "scorer"
    description = "Fixture that requests root_access, which is not allowed."
    author = "FSOCIETY AI tests"
    license = "AGPL-3.0-or-later"
    dependencies: list[str] = []
    permissions = ["network", "root_access"]

    def score(self, item: dict[str, Any]) -> float:
        return 0.0
