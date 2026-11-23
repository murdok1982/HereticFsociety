# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixture plugin: hardware provider with a no-argument detect()."""

from __future__ import annotations

from typing import Any

from fsociety import PLUGIN_API_VERSION
from fsociety.plugins.api import HardwareProvider


class FixtureHardwareProvider(HardwareProvider):
    name = "fixture_hardware"
    version = "1.0.0"
    api_version = PLUGIN_API_VERSION
    kind = "hardware_provider"
    description = "Fixture hardware provider used by dispatch tests."
    author = "FSOCIETY AI tests"
    license = "AGPL-3.0-or-later"
    dependencies: list[str] = []
    permissions: list[str] = []

    def detect(self) -> dict[str, Any]:
        return {"backend": "fixture", "cuda": False}
