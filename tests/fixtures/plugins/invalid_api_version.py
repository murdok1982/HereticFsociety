# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixture plugin: declares an unsupported plugin API version."""

from __future__ import annotations

from typing import Any

from fsociety.plugins.api import Scorer


class WrongApiScorer(Scorer):
    name = "fixture_wrong_api"
    version = "1.0.0"
    api_version = 999
    kind = "scorer"
    description = "Fixture with an incompatible plugin API version."
    author = "FSOCIETY AI tests"
    license = "AGPL-3.0-or-later"
    dependencies: list[str] = []
    permissions: list[str] = []

    def score(self, item: dict[str, Any]) -> float:
        return 0.0
