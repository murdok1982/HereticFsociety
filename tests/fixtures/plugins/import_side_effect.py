# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixture plugin with module-level side effects.

Discovery imports workspace plugins to inspect their classes. This fixture makes
that boundary visible: importing it writes ``FSOCIETY_TEST_IMPORT_MARKER`` and
captures ``FSOCIETY_API_KEY`` from the *host* environment. It writes to a
test-controlled temporary path only and uses a synthetic value.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fsociety import PLUGIN_API_VERSION
from fsociety.plugins.api import Scorer

_MARKER = os.environ.get("FSOCIETY_TEST_IMPORT_MARKER")
if _MARKER:
    Path(_MARKER).write_text(os.environ.get("FSOCIETY_API_KEY", ""), encoding="utf-8")


class ImportSideEffectScorer(Scorer):
    name = "fixture_import_side_effect"
    version = "1.0.0"
    api_version = PLUGIN_API_VERSION
    kind = "scorer"
    description = "Writes a marker file when its module is imported."
    author = "FSOCIETY AI tests"
    license = "AGPL-3.0-or-later"
    dependencies: list[str] = []
    permissions: list[str] = []

    def score(self, item: dict[str, Any]) -> float:
        return 0.25
