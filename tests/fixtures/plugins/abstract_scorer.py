# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixture plugin: declares a scorer kind but never implements score()."""

from __future__ import annotations

from fsociety import PLUGIN_API_VERSION
from fsociety.plugins.api import Scorer


class AbstractScorer(Scorer):
    name = "fixture_abstract"
    version = "1.0.0"
    api_version = PLUGIN_API_VERSION
    kind = "scorer"
    description = "Abstract fixture scorer without an implementation."
    author = "FSOCIETY AI tests"
    license = "AGPL-3.0-or-later"
    dependencies: list[str] = []
    permissions: list[str] = []
