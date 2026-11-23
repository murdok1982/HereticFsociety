# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Spanish-language heuristic scorer.

Uses ``langdetect`` lazily when installed and falls back to a stopword/accent
lexicon otherwise. This is a heuristic, not a trained language classifier: short
or code-heavy inputs are inherently uncertain and are reported as such.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from fsociety import PLUGIN_API_VERSION

from ..api import Scorer

_SPANISH_STOPWORDS = frozenset(
    [
        "el",
        "la",
        "los",
        "las",
        "un",
        "una",
        "unos",
        "unas",
        "de",
        "del",
        "al",
        "a",
        "en",
        "y",
        "o",
        "u",
        "que",
        "qué",
        "es",
        "son",
        "ser",
        "estar",
        "por",
        "para",
        "con",
        "sin",
        "sobre",
        "entre",
        "como",
        "más",
        "pero",
        "sus",
        "su",
        "mi",
        "mis",
        "tu",
        "tus",
        "nuestro",
        "nuestra",
        "este",
        "esta",
        "estos",
        "estas",
        "ese",
        "esa",
        "eso",
        "aquel",
        "aquella",
        "lo",
        "le",
        "les",
        "se",
        "me",
        "te",
        "nos",
        "ya",
        "muy",
        "también",
        "aunque",
        "porque",
        "cuando",
        "donde",
        "quien",
        "quién",
        "cual",
        "cuál",
        "hay",
        "ha",
        "han",
        "había",
        "fue",
        "fueron",
        "era",
        "eran",
        "será",
        "serían",
        "no",
        "sí",
        "si",
    ]
)
_ACCENTS = frozenset("áéíóúüñÁÉÍÓÚÜÑ")
_TOKEN_RE = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+")
_SIMILAR_LANGUAGES = frozenset({"ca", "pt", "gl", "it"})


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


class SpanishLanguageScorer(Scorer):
    name = "spanish_language"
    version = "1.0.0"
    api_version = PLUGIN_API_VERSION
    kind = "scorer"
    description = (
        "Heuristic Spanish-language score using langdetect when available and "
        "a stopword/accent lexicon fallback otherwise."
    )
    author = "FSOCIETY AI contributors"
    license = "AGPL-3.0-or-later"
    dependencies: ClassVar[list[str]] = []
    permissions: ClassVar[list[str]] = []

    def analyze(self, item: dict[str, Any]) -> dict[str, Any]:
        raw = item.get("text") or item.get("response") or ""
        text = raw if isinstance(raw, str) else str(raw)
        if not text.strip():
            return {
                "score": 0.0,
                "detected_language": None,
                "notes": ["empty input"],
                "heuristic": True,
            }

        tokens = _TOKEN_RE.findall(text)
        if not tokens:
            return {
                "score": 0.0,
                "detected_language": None,
                "notes": ["no alphabetic tokens"],
                "heuristic": True,
            }
        stopword_hits = sum(
            1 for token in tokens if token.lower() in _SPANISH_STOPWORDS
        )
        accent_hits = sum(1 for char in text if char in _ACCENTS)
        stopword_ratio = stopword_hits / len(tokens)
        lexicon_score = _clamp(stopword_ratio * 2.5)
        if accent_hits:
            lexicon_score = _clamp(lexicon_score + 0.20)

        detected: str | None = None
        detection_source = "lexicon"
        try:  # lazy optional dependency
            from langdetect import LangDetectException, detect  # type: ignore[import-not-found]

            try:
                detected = detect(text)
                detection_source = "langdetect"
            except LangDetectException:
                detected = None
        except ImportError:
            detected = None

        if detected == "es":
            score = max(lexicon_score, 0.75)
        elif detected in _SIMILAR_LANGUAGES:
            score = min(max(lexicon_score, 0.35), 0.60)
        elif detected is not None:
            score = min(lexicon_score, 0.30)
        else:
            score = lexicon_score

        notes = [
            f"tokens={len(tokens)}",
            f"stopword_ratio={stopword_ratio:.3f}",
            f"accent_chars={accent_hits}",
            f"detection_source={detection_source}",
        ]
        if len(tokens) < 8:
            notes.append("short input: language detection is unreliable")
        return {
            "score": round(_clamp(score), 4),
            "detected_language": detected,
            "notes": notes,
            "heuristic": True,
        }

    def score(self, item: dict[str, Any]) -> float:
        return self.analyze(item)["score"]
