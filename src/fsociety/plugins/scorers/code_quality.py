# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Heuristic code-quality scorer (no ML, no network).

Signals: AST parseability, approximate cyclomatic complexity, docstrings,
comment density, error handling and banned dangerous patterns. The score is a
weighted heuristic, not a proof of correctness or security.
"""

from __future__ import annotations

import ast
import re
from typing import Any, ClassVar

from fsociety import PLUGIN_API_VERSION

from ..api import Scorer

_BANNED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("eval", re.compile(r"\beval\s*\(")),
    ("exec", re.compile(r"\bexec\s*\(")),
    ("shell_true", re.compile(r"\bshell\s*=\s*True\b")),
    ("os_system", re.compile(r"\bos\.system\s*\(")),
    ("unsafe_import", re.compile(r"\b__import__\s*\(")),
    ("pickle_loads", re.compile(r"\bpickle\.loads?\s*\(")),
)

_COMPLEXITY_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ExceptHandler,
    ast.With,
    ast.AsyncWith,
    ast.Assert,
    ast.IfExp,
    ast.comprehension,
    ast.BoolOp,
)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


class CodeQualityScorer(Scorer):
    name = "code_quality"
    version = "1.0.0"
    api_version = PLUGIN_API_VERSION
    kind = "scorer"
    description = (
        "Heuristic code-quality score: AST parse, complexity, docstrings, "
        "comments, error handling and banned dangerous constructs."
    )
    author = "FSOCIETY AI contributors"
    license = "AGPL-3.0-or-later"
    dependencies: ClassVar[list[str]] = []
    permissions: ClassVar[list[str]] = []

    def analyze(self, item: dict[str, Any]) -> dict[str, Any]:
        raw = item.get("code") or item.get("text") or item.get("response") or ""
        code = raw if isinstance(raw, str) else str(raw)
        if not code.strip():
            return {
                "score": 0.0,
                "breakdown": {},
                "notes": ["empty input"],
                "heuristic": True,
            }

        try:
            tree = ast.parse(code)
        except (SyntaxError, ValueError) as exc:
            return {
                "score": 0.05,
                "breakdown": {"parse": 0.0},
                "notes": [
                    f"syntax error: {exc.msg if isinstance(exc, SyntaxError) else exc}"
                ],
                "heuristic": True,
            }

        complexity = 1
        for node in ast.walk(tree):
            if isinstance(node, _COMPLEXITY_NODES):
                complexity += 1
        complexity_score = _clamp(1.0 - max(0, complexity - 1) / 40.0)

        docstring = ast.get_docstring(tree) is not None or any(
            ast.get_docstring(node) is not None
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        )
        lines = code.splitlines()
        comment_lines = sum(1 for line in lines if line.strip().startswith("#"))
        comment_ratio = comment_lines / max(1, len(lines))
        comment_score = _clamp(comment_ratio / 0.10)
        has_error_handling = any(isinstance(node, ast.Try) for node in ast.walk(tree))

        banned_hits = [
            label for label, pattern in _BANNED_PATTERNS if pattern.search(code)
        ]
        banned_score = 1.0 if not banned_hits else 0.0

        breakdown = {
            "parse": 0.25,
            "complexity": round(0.20 * complexity_score, 4),
            "docstring": 0.15 if docstring else 0.0,
            "comments": round(0.10 * comment_score, 4),
            "error_handling": 0.15 if has_error_handling else 0.0,
            "banned_patterns": round(0.15 * banned_score, 4),
        }
        score = sum(breakdown.values())
        if banned_hits:
            score -= min(0.30, 0.10 * len(banned_hits))
            breakdown["banned_penalty"] = round(-min(0.30, 0.10 * len(banned_hits)), 4)

        notes = [f"cyclomatic_complexity~{complexity}"]
        if banned_hits:
            notes.append("banned constructs: " + ", ".join(banned_hits))
        return {
            "score": round(_clamp(score), 4),
            "breakdown": breakdown,
            "notes": notes,
            "heuristic": True,
        }

    def score(self, item: dict[str, Any]) -> float:
        return self.analyze(item)["score"]
