# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixture plugin: integration probe with a no-argument probe()."""

from __future__ import annotations

from typing import Any

from fsociety import PLUGIN_API_VERSION
from fsociety.plugins.api import Integration


class FixtureIntegration(Integration):
    name = "fixture_integration"
    version = "1.0.0"
    api_version = PLUGIN_API_VERSION
    kind = "integration"
    description = "Fixture integration used by dispatch tests."
    author = "FSOCIETY AI tests"
    license = "AGPL-3.0-or-later"
    dependencies: list[str] = []
    permissions: list[str] = []

    def probe(self) -> dict[str, Any]:
        return {"ok": True, "endpoint": "fixture://local"}
