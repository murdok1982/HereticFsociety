# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Defensive-cybersecurity rubric scorer.

Scores *defensive* content only: detection, mitigation, CVE understanding,
secure coding, incident response and defense in depth. Offensive patterns that
appear without adjacent defensive framing reduce the score. This scorer does not
endorse or generate offensive material; it is a rubric for defensive quality.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from fsociety import PLUGIN_API_VERSION

from ..api import Scorer

_CONCEPTS: dict[str, tuple[str, ...]] = {
    "detection": (
        "detect",
        "detection",
        "monitor",
        "logging",
        "alert",
        "siem",
        "intrusion",
        "edr",
        "signature",
    ),
    "mitigation": (
        "mitigat",
        "patch",
        "harden",
        "firewall",
        "least privilege",
        "block",
        "disable",
        "remediat",
        "defense",
    ),
    "cve_understanding": (
        "cve-",
        "cve ",
        "vulnerability",
        "vulnerabilit",
        "advisory",
        "cvss",
        "affected version",
    ),
    "secure_coding": (
        "input validation",
        "parameterized",
        "sanitiz",
        "escape",
        "authentication",
        "authorization",
        "encryption",
        "hash",
        "csrf",
        "same-origin",
    ),
    "incident_response": (
        "incident response",
        "containment",
        "forensic",
        "playbook",
        "post-mortem",
        "recovery",
        "triage",
        "escalat",
    ),
    "defense_in_depth": (
        "defense in depth",
        "layered",
        "segmentation",
        "zero trust",
        "principle of least privilege",
        "redundan",
    ),
}

_OFFENSIVE = (
    "reverse shell",
    "keylogger",
    "ransomware",
    "botnet",
    "ddos",
    "metasploit",
    "exploit code",
    "sql injection payload",
    "bypass authentication",
    "crack password",
    "credential dump",
    "wireless deauth",
    "privilege escalation exploit",
)
_DEFENSIVE_CONTEXT = (
    "detect",
    "mitigat",
    "patch",
    "prevent",
    "defend",
    "protect",
    "monitor",
    "respond",
    "remediat",
    "harden",
    "secure",
    "audit",
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


class DefensiveCybersecurityScorer(Scorer):
    name = "defensive_cybersecurity"
    version = "1.0.0"
    api_version = PLUGIN_API_VERSION
    kind = "scorer"
    description = (
        "Defensive-only cybersecurity rubric: detection, mitigation, CVE "
        "understanding, secure coding, incident response and defense in depth."
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
                "concepts": {},
                "notes": ["empty input"],
                "heuristic": True,
            }

        matched: dict[str, list[str]] = {}
        for concept, keywords in _CONCEPTS.items():
            hits = [keyword for keyword in keywords if keyword in lowered]
            if hits:
                matched[concept] = hits
        coverage = len(matched) / len(_CONCEPTS)

        offensive_hits: list[str] = []
        unmitigated: list[str] = []
        sentences = [sentence.lower() for sentence in _SENTENCE_SPLIT_RE.split(text)]
        for term in _OFFENSIVE:
            if term not in lowered:
                continue
            offensive_hits.append(term)
            defended = any(
                term in sentence
                and any(context in sentence for context in _DEFENSIVE_CONTEXT)
                for sentence in sentences
            )
            if not defended:
                unmitigated.append(term)

        score = 0.20 + 0.80 * coverage
        score -= min(0.60, 0.15 * len(unmitigated))

        notes = [
            f"concepts_matched={sorted(matched)}",
            f"offensive_terms={offensive_hits}",
            f"unmitigated_offensive_terms={unmitigated}",
            "defensive scoring only",
        ]
        return {
            "score": round(_clamp(score), 4),
            "concepts": {
                concept: sorted(set(hits)) for concept, hits in matched.items()
            },
            "unmitigated": unmitigated,
            "notes": notes,
            "heuristic": True,
        }

    def score(self, item: dict[str, Any]) -> float:
        return self.analyze(item)["score"]
