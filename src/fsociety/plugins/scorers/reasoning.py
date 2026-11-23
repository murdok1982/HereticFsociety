# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Heuristic reasoning-quality scorer (no ML).

Signals: explicit chain-of-thought markers, self-consistency of simple
arithmetic claims, and the balance between hedging and unsupported certainty.
Arithmetic checking only understands literal ``a op b = c`` statements.
"""

from __future__ import annotations

import operator
import re
from collections.abc import Callable
from typing import Any, ClassVar

from fsociety import PLUGIN_API_VERSION

from ..api import Scorer

_CLAIM_RE = re.compile(
    r"(-?\d+(?:\.\d+)?)\s*([+*/x×-])\s*(-?\d+(?:\.\d+)?)\s*=\s*(-?\d+(?:\.\d+)?)"
)
_MARKERS = (
    "step 1",
    "first",
    "second",
    "next",
    "then",
    "therefore",
    "because",
    "thus",
    "so ",
    "finally",
    "given that",
    "let us",
    "let's",
    "implies",
    "consequently",
)
_HEDGES = (
    "probably",
    "likely",
    "possibly",
    "maybe",
    "may",
    "might",
    "approximately",
    "about",
    "i think",
    "uncertain",
    "assume",
    "it seems",
    "roughly",
)
_CERTAINTY = (
    "definitely",
    "certainly",
    "always",
    "never",
    "guaranteed",
    "proven",
    "undeniably",
    "100%",
    "without a doubt",
)

_OPERATORS: dict[str, Callable[[float, float], float]] = {
    "+": operator.add,
    "-": operator.sub,
    "*": operator.mul,
    "x": operator.mul,
    "×": operator.mul,
    "/": operator.truediv,
}


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


class ReasoningScorer(Scorer):
    name = "reasoning"
    version = "1.0.0"
    api_version = PLUGIN_API_VERSION
    kind = "scorer"
    description = (
        "Heuristic reasoning score: chain markers, arithmetic self-consistency "
        "and hedging vs unsupported certainty."
    )
    author = "FSOCIETY AI contributors"
    license = "AGPL-3.0-or-later"
    dependencies: ClassVar[list[str]] = []
    permissions: ClassVar[list[str]] = []

    def analyze(self, item: dict[str, Any]) -> dict[str, Any]:
        raw = item.get("text") or item.get("response") or ""
        text = raw if isinstance(raw, str) else str(raw)
        lowered = text.lower()
        if not text.strip():
            return {
                "score": 0.0,
                "breakdown": {},
                "notes": ["empty input"],
                "heuristic": True,
            }

        marker_hits = sorted(
            {marker.strip() for marker in _MARKERS if marker in lowered}
        )
        chain_score = _clamp(len(marker_hits) / 4.0)

        claims = _CLAIM_RE.findall(text)
        correct = 0
        wrong = 0
        for left, op, right, expected in claims:
            try:
                value = _OPERATORS[op](float(left), float(right))
            except (KeyError, ZeroDivisionError):
                wrong += 1
                continue
            if abs(value - float(expected)) <= max(1e-6, abs(float(expected)) * 1e-9):
                correct += 1
            else:
                wrong += 1
        arithmetic_score = correct / len(claims) if claims else 0.5

        hedges = sum(1 for word in _HEDGES if word in lowered)
        certainty = sum(1 for word in _CERTAINTY if word in lowered)
        hedge_score = _clamp(0.5 + min(0.5, hedges * 0.12))
        unsupported_certainty = max(0, certainty - hedges)
        certainty_penalty = min(0.25, 0.06 * unsupported_certainty)

        score = 0.35 * chain_score + 0.35 * arithmetic_score + 0.30 * hedge_score
        score -= certainty_penalty
        breakdown = {
            "chain_markers": round(0.35 * chain_score, 4),
            "arithmetic_consistency": round(0.35 * arithmetic_score, 4),
            "hedging": round(0.30 * hedge_score, 4),
            "certainty_penalty": round(-certainty_penalty, 4),
        }
        notes = [
            f"markers={len(marker_hits)}",
            f"arithmetic_claims={len(claims)} correct={correct} wrong={wrong}",
            f"hedges={hedges} certainty={certainty}",
        ]
        return {
            "score": round(_clamp(score), 4),
            "breakdown": breakdown,
            "notes": notes,
            "heuristic": True,
        }

    def score(self, item: dict[str, Any]) -> float:
        return self.analyze(item)["score"]
