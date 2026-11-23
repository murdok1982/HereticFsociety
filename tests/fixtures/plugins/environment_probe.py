# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixture plugin that reports the environment it actually sees.

Used by the security audit to prove that the isolated worker does not inherit
provider API keys or platform settings.
"""

from __future__ import annotations

import os
from typing import Any

from fsociety import PLUGIN_API_VERSION
from fsociety.plugins.api import Scorer


class EnvironmentScorer(Scorer):
    name = "fixture_environment"
    version = "1.0.0"
    api_version = PLUGIN_API_VERSION
    kind = "scorer"
    description = "Returns the worker environment for isolation assertions."
    author = "FSOCIETY AI tests"
    license = "AGPL-3.0-or-later"
    dependencies: list[str] = []
    permissions: list[str] = []

    def score(self, item: dict[str, Any]) -> Any:
        names = item.get("names") or []
        return {
            "keys": sorted(os.environ),
            "cwd": os.getcwd(),
            "selected": {str(name): os.environ.get(str(name)) for name in names},
        }
