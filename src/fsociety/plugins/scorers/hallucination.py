# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Heuristic hallucination-risk scorer (higher score = lower risk).

Detects *signals* associated with fabrication: unverifiable URLs, citations
without a reference section, precise statistics without attribution, impossible
dates, and unhedged certainty. It performs **no network lookups** and makes no
claim that a URL is valid or invalid; this is a risk heuristic only.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, ClassVar

from fsociety import PLUGIN_API_VERSION

from ..api import Scorer

_URL_RE = re.compile(r"https?://[^\s)\]>\"']+", re.IGNORECASE)
_CITATION_RE = re.compile(
    r"\[\d{1,3}\]|\((?:[A-Z][A-Za-z.\-]+(?: et al\.)?,?\s*)(?:19|20)\d{2}\)"
)
_YEAR_RE = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")
_STAT_RE = re.compile(r"\b\d{1,3}(?:\.\d+)?\s?%|\b\d+ out of \d+\b")
_HEDGES = (
    "may",
    "might",
    "could",
    "likely",
    "probably",
    "approximately",
    "roughly",
    "about",
    "i am not sure",
    "uncertain",
    "possibly",
    "appears to",
    "suggests",
    "according to my training",
    "knowledge cutoff",
)
_SOURCE_SECTIONS = ("references", "bibliography", "sources", "works cited", "footnotes")
_PLACEHOLDER_HOSTS = (
    "example.com",
    "example.org",
    "example.net",
    "localhost",
    "127.0.0.1",
)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


class HallucinationScorer(Scorer):
    name = "hallucination_risk"
    version = "1.0.0"
    api_version = PLUGIN_API_VERSION
    kind = "scorer"
    description = (
        "Heuristic hallucination-risk score (higher = less risk) based on "
        "unverifiable citations/URLs, unattributed statistics and impossible dates."
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
                "risk": 1.0,
                "notes": ["empty input"],
                "heuristic": True,
            }

        urls = _URL_RE.findall(text)
        unverifiable_urls = [
            url
            for url in urls
            if not any(host in url.lower() for host in _PLACEHOLDER_HOSTS)
        ]
        citations = _CITATION_RE.findall(text)
        has_sources = any(section in lowered for section in _SOURCE_SECTIONS)
        stats = _STAT_RE.findall(text)
        current_year = datetime.now(timezone.utc).year
        years = [int(year) for year in _YEAR_RE.findall(text)]
        impossible_years = [
            year for year in years if year > current_year or year < 1900
        ]
        hedges = sum(1 for hedge in _HEDGES if hedge in lowered)

        risk = 0.0
        risk += min(0.30, 0.10 * len(unverifiable_urls))
        if citations and not has_sources:
            risk += min(0.30, 0.15 * len(citations))
        if stats and not citations and not has_sources:
            risk += min(0.20, 0.08 * len(stats))
        risk += min(0.20, 0.12 * len(impossible_years))
        risk = _clamp(risk)

        relief = min(0.20, 0.04 * hedges)
        score = _clamp(0.90 - risk + relief)

        notes = [
            f"urls={len(urls)} unverifiable={len(unverifiable_urls)}",
            f"citations={len(citations)} reference_section={has_sources}",
            f"statistics={len(stats)} impossible_years={impossible_years}",
            f"hedges={hedges}",
        ]
        return {
            "score": round(score, 4),
            "risk": round(risk, 4),
            "notes": notes,
            "heuristic": True,
        }

    def score(self, item: dict[str, Any]) -> float:
        return self.analyze(item)["score"]
