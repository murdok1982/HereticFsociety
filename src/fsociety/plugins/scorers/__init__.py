# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Builtin example scorers.

All scorers are deterministic heuristics unless explicitly documented otherwise.
Importing this package imports only the plugin API and the standard library;
optional dependencies (``langdetect``) are loaded lazily by the scorer itself.
"""

from __future__ import annotations

from .behavioral_regression import BehavioralRegressionScorer
from .code_quality import CodeQualityScorer
from .defensive_cybersecurity import DefensiveCybersecurityScorer
from .hallucination import HallucinationScorer
from .model_integrity import ModelIntegrityScorer
from .reasoning import ReasoningScorer
from .spanish_language import SpanishLanguageScorer

#: Deterministic discovery order for the registry.
BUILTIN_SCORERS: tuple[type, ...] = (
    CodeQualityScorer,
    ReasoningScorer,
    SpanishLanguageScorer,
    HallucinationScorer,
    ModelIntegrityScorer,
    BehavioralRegressionScorer,
    DefensiveCybersecurityScorer,
)

__all__ = [
    "BUILTIN_SCORERS",
    "BehavioralRegressionScorer",
    "CodeQualityScorer",
    "DefensiveCybersecurityScorer",
    "HallucinationScorer",
    "ModelIntegrityScorer",
    "ReasoningScorer",
    "SpanishLanguageScorer",
]
