# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Behavioral-regression scorer for two BenchmarkReport-like dictionaries.

The math is implemented locally on purpose: importing the evaluation package
here would create a plugin → evaluation → plugin dependency cycle. The scorer
reads only ``results`` entries and their ``passed``/``score`` fields.
"""

from __future__ import annotations

from typing import Any, ClassVar

from fsociety import PLUGIN_API_VERSION

from ...errors import PluginError
from ..api import Scorer


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _item_outcomes(report: dict[str, Any]) -> dict[str, bool]:
    outcomes: dict[str, bool] = {}
    results = report.get("results")
    if not isinstance(results, list):
        return outcomes
    for index, entry in enumerate(results):
        if not isinstance(entry, dict):
            continue
        item_id = str(entry.get("item_id") or entry.get("id") or f"#{index}")
        passed = entry.get("passed")
        if isinstance(passed, bool):
            outcomes[item_id] = passed
            continue
        score = entry.get("score")
        if isinstance(score, (int, float)):
            outcomes[item_id] = float(score) >= 0.5
    return outcomes


def _pass_rate(outcomes: dict[str, bool]) -> float | None:
    if not outcomes:
        return None
    return sum(1 for passed in outcomes.values() if passed) / len(outcomes)


class BehavioralRegressionScorer(Scorer):
    name = "behavioral_regression"
    version = "1.0.0"
    api_version = PLUGIN_API_VERSION
    kind = "scorer"
    description = (
        "Compares two benchmark reports: pass-rate delta and per-item "
        "regressions, mapped to a 0..1 score."
    )
    author = "FSOCIETY AI contributors"
    license = "AGPL-3.0-or-later"
    dependencies: ClassVar[list[str]] = []
    permissions: ClassVar[list[str]] = []

    def analyze(self, item: dict[str, Any]) -> dict[str, Any]:
        baseline = item.get("baseline") or item.get("baseline_report")
        candidate = item.get("candidate") or item.get("candidate_report")
        if not isinstance(baseline, dict) or not isinstance(candidate, dict):
            raise PluginError(
                "BehavioralRegressionScorer requires two report objects: "
                "'baseline' and 'candidate'.",
            )

        baseline_outcomes = _item_outcomes(baseline)
        candidate_outcomes = _item_outcomes(candidate)
        baseline_rate = _pass_rate(baseline_outcomes)
        candidate_rate = _pass_rate(candidate_outcomes)

        regressed: list[str] = []
        improved: list[str] = []
        for item_id, baseline_passed in baseline_outcomes.items():
            if item_id not in candidate_outcomes:
                continue
            candidate_passed = candidate_outcomes[item_id]
            if baseline_passed and not candidate_passed:
                regressed.append(item_id)
            elif not baseline_passed and candidate_passed:
                improved.append(item_id)

        if baseline_rate is None or candidate_rate is None:
            delta = 0.0
            note = "insufficient scorable results; neutral score"
        else:
            delta = candidate_rate - baseline_rate
            note = f"pass-rate delta {delta:+.4f}"
        comparable_items = max(1, len(baseline_outcomes))
        regression_share = len(regressed) / comparable_items
        score = _clamp(0.5 + 0.5 * delta - 0.15 * regression_share)

        return {
            "score": round(score, 4),
            "baseline_pass_rate": baseline_rate,
            "candidate_pass_rate": candidate_rate,
            "pass_rate_delta": delta,
            "regressed_items": sorted(regressed),
            "improved_items": sorted(improved),
            "notes": [
                note,
                f"regressions={len(regressed)} improvements={len(improved)}",
            ],
            "heuristic": False,
        }

    def score(self, item: dict[str, Any]) -> float:
        return self.analyze(item)["score"]
