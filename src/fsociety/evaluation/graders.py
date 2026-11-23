# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Deterministic graders for the bundled evaluation suites.

Every grader has the same contract::

    grade(item: BenchmarkItem, response: str | None) -> (score, passed, details)

* ``score`` is clamped to ``[0.0, 1.0]``.
* ``passed`` is a boolean decision derived from ``score`` and the grader logic.
* ``details`` is JSON-serializable and always carries ``grader`` and
  ``grader_version``.

Graders never raise on model output. Malformed or empty responses produce
``score=0.0`` plus a ``details["error"]`` marker. Graders that rely on language
or keyword heuristics are labelled explicitly: they are smoke checks, not
scientific measurements.
"""

from __future__ import annotations

import ast
import json
import math
import re
import unicodedata
from typing import Any, Callable

from ..core.models import BenchmarkItem
from ..errors import ValidationError

#: Bump when grading logic changes in a way that alters historical scores.
GRADER_VERSION = "1"

_RESPONSE_PREVIEW_CHARS = 500
_MAX_DETAIL_TEXT = 2000

_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "´": "'", "`": "'"})
_EDGE_PUNCTUATION = " \t\r\n.,;:!?\"'()[]{}<>«»…—-"

_NUMBER_RE = re.compile(
    r"(?<![\w.])[+-]?(?:\d{1,3}(?:[,_]\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?"
)
_FENCE_RE = re.compile(
    r"```[ \t]*(?:python|py)?[ \t]*\r?\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)
_CODE_START_RE = re.compile(r"^(?:async\s+def|def|class|import|from)\b", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+\S", re.MULTILINE)

# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    """NFKC + casefold + apostrophe folding + whitespace collapsing."""
    folded = unicodedata.normalize("NFKC", text).translate(_APOSTROPHES)
    return " ".join(folded.casefold().split())


def _strip_edges(text: str) -> str:
    return text.strip(_EDGE_PUNCTUATION)


def _response_text(response: str | None) -> str:
    if response is None:
        return ""
    if isinstance(response, bytes):
        return response.decode("utf-8", errors="replace")
    if isinstance(response, str):
        return response
    return ""


def _preview(text: str) -> str:
    return text[:_MAX_DETAIL_TEXT]


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _as_int(value: Any) -> int | None:
    parsed = _as_float(value)
    if parsed is None or not math.isfinite(parsed) or parsed != int(parsed):
        return None
    return int(parsed)


def _term_pattern(term: str) -> re.Pattern[str]:
    """Word-bounded match for short alphanumeric tokens, substring otherwise."""
    escaped = re.escape(term)
    if re.fullmatch(r"[a-z0-9]+", term):
        return re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])")
    return re.compile(escaped)


def _term_present(normalized_text: str, term: str) -> bool:
    normalized_term = _normalize(term)
    if not normalized_term:
        return False
    return bool(_term_pattern(normalized_term).search(normalized_text))


def _concept_present(normalized_text: str, term: str) -> bool:
    """Concept/stem matching: substrings for words, word bounds for short tokens."""
    normalized_term = _normalize(term)
    if not normalized_term:
        return False
    if len(normalized_term) <= 3:
        return bool(_term_pattern(normalized_term).search(normalized_text))
    return normalized_term in normalized_text


def _alternatives(value: Any) -> list[str]:
    if isinstance(value, str):
        parts = value.split("|")
    elif isinstance(value, (list, tuple)):
        parts = [str(part) for part in value]
    else:
        return []
    return [part.strip() for part in parts if part.strip()]


def _extract_last_number(text: str) -> tuple[float | None, list[str]]:
    matches = [match.group(0) for match in _NUMBER_RE.finditer(text)]
    if not matches:
        return None, []
    raw = matches[-1].replace(",", "").replace("_", "")
    try:
        return float(raw), matches
    except ValueError:  # pragma: no cover - regex guarantees parseability
        return None, matches


def _empty_details(**extra: Any) -> dict[str, Any]:
    details: dict[str, Any] = {"empty_response": True, "error": "empty_response"}
    details.update(extra)
    return details


def _score(
    score: float, passed: bool, details: dict[str, Any]
) -> tuple[float, bool, dict[str, Any]]:
    clamped = max(0.0, min(1.0, float(score)))
    return clamped, bool(passed), details


# ---------------------------------------------------------------------------
# exact_match
# ---------------------------------------------------------------------------


def grade_exact_match(
    item: BenchmarkItem, response: str | None
) -> tuple[float, bool, dict[str, Any]]:
    """Normalized string equality (case, whitespace, edge punctuation)."""
    text = _response_text(response)
    candidates = [item.expected or ""] + _alternatives(
        item.metadata.get("acceptable_answers")
    )
    normalized_candidates = [
        _strip_edges(_normalize(candidate))
        for candidate in candidates
        if candidate and candidate.strip()
    ]
    if not normalized_candidates:
        return _score(0.0, False, {"error": "missing_expected"})
    if not text.strip():
        return _score(0.0, False, _empty_details(expected=item.expected))
    normalized_response = _strip_edges(_normalize(text))
    passed = normalized_response in normalized_candidates
    details = {
        "method": "normalized_equality",
        "expected": _preview(item.expected or ""),
        "normalized_expected": normalized_candidates[0],
        "normalized_response": _preview(normalized_response),
    }
    return _score(1.0 if passed else 0.0, passed, details)


# ---------------------------------------------------------------------------
# contains_expected
# ---------------------------------------------------------------------------


def grade_contains_expected(
    item: BenchmarkItem, response: str | None
) -> tuple[float, bool, dict[str, Any]]:
    """All required terms must appear; ``expected`` may list ``|`` alternatives."""
    text = _response_text(response)
    if not text.strip():
        return _score(0.0, False, _empty_details(expected=item.expected))

    alternatives = _alternatives(item.metadata.get("acceptable_answers"))
    if not alternatives:
        alternatives = _alternatives(item.expected or "")
    required_terms = _alternatives(item.metadata.get("required_terms"))
    if not alternatives and not required_terms:
        return _score(0.0, False, {"error": "missing_expected"})

    normalized = _normalize(text)
    matched_alternative = (
        next((alt for alt in alternatives if _term_present(normalized, alt)), None)
        if alternatives
        else None
    )
    missing_terms = [
        term for term in required_terms if not _term_present(normalized, term)
    ]

    alternative_ok = matched_alternative is not None if alternatives else True
    terms_total = len(required_terms)
    terms_matched = terms_total - len(missing_terms)
    if alternatives and terms_total:
        score = 0.5 * (1.0 if alternative_ok else 0.0) + 0.5 * (
            terms_matched / terms_total
        )
    elif terms_total:
        score = terms_matched / terms_total
    else:
        score = 1.0 if alternative_ok else 0.0

    passed = alternative_ok and not missing_terms
    details = {
        "method": "term_containment",
        "alternatives": alternatives,
        "matched_alternative": matched_alternative,
        "required_terms": required_terms,
        "missing_terms": missing_terms,
        "response_preview": _preview(_strip_edges(normalized)),
    }
    return _score(score, passed, details)


# ---------------------------------------------------------------------------
# numeric
# ---------------------------------------------------------------------------


def grade_numeric(
    item: BenchmarkItem, response: str | None
) -> tuple[float, bool, dict[str, Any]]:
    """Compare the last number in the response with ``expected``."""
    expected, _ = _extract_last_number(item.expected or "")
    if expected is None:
        return _score(
            0.0, False, {"error": "invalid_expected", "expected": item.expected}
        )
    text = _response_text(response)
    if not text.strip():
        return _score(0.0, False, _empty_details(expected=item.expected))
    actual, numbers = _extract_last_number(text)
    if actual is None:
        return _score(
            0.0,
            False,
            {
                "error": "no_numeric_answer",
                "expected": expected,
                "response_preview": _preview(text),
            },
        )
    tolerance = _as_float(item.metadata.get("tolerance"))
    if tolerance is None or tolerance < 0:
        tolerance = 1e-6
    relative_tolerance = _as_float(item.metadata.get("relative_tolerance"))
    if relative_tolerance is None or relative_tolerance < 0:
        relative_tolerance = 1e-4
    allowed = tolerance + relative_tolerance * abs(expected)
    difference = actual - expected
    passed = abs(difference) <= allowed
    details = {
        "method": "final_number_with_tolerance",
        "expected": expected,
        "actual": actual,
        "difference": difference,
        "absolute_tolerance": tolerance,
        "relative_tolerance": relative_tolerance,
        "numbers_in_response": numbers[-10:],
    }
    return _score(1.0 if passed else 0.0, passed, details)


# ---------------------------------------------------------------------------
# python_syntax
# ---------------------------------------------------------------------------


def _extract_code(response: str) -> tuple[str, str]:
    fenced = _FENCE_RE.findall(response)
    if fenced:
        return fenced[0].strip("\n"), "fenced_block"
    match = _CODE_START_RE.search(response)
    if match:
        return response[match.start() :].strip("\n"), "code_tail"
    return response.strip("\n"), "whole_response"


def _defined_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                for child in ast.walk(target):
                    if isinstance(child, ast.Name):
                        names.add(child.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def grade_python_syntax(
    item: BenchmarkItem, response: str | None
) -> tuple[float, bool, dict[str, Any]]:
    """Parse the answer with :mod:`ast` and check required constructs/names."""
    text = _response_text(response)
    if not text.strip():
        return _score(0.0, False, _empty_details(requirements=item.metadata))
    code, source = _extract_code(text)
    if not code.strip():
        return _score(0.0, False, {"error": "no_code_found", "code_source": source})
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, MemoryError) as exc:
        line = getattr(exc, "lineno", None)
        message = getattr(exc, "msg", str(exc))
        return _score(
            0.0,
            False,
            {
                "syntax_ok": False,
                "error": "syntax_error",
                "message": f"{type(exc).__name__}: {message}"[:400],
                "line": line,
                "code_source": source,
                "code_chars": len(code),
            },
        )

    node_names = {type(node).__name__ for node in ast.walk(tree)}
    defined = _defined_names(tree)
    required_constructs = [
        str(value)
        for value in item.metadata.get("required_constructs", [])
        if str(value).strip()
    ]
    required_names = [
        str(value)
        for value in item.metadata.get("required_names", [])
        if str(value).strip()
    ]
    must_contain = [
        str(value)
        for value in item.metadata.get("must_contain", [])
        if str(value).strip()
    ]
    must_not_contain = [
        str(value)
        for value in item.metadata.get("must_not_contain", [])
        if str(value).strip()
    ]

    checks: list[dict[str, Any]] = []
    for construct in required_constructs:
        checks.append(
            {"check": f"construct:{construct}", "ok": construct in node_names}
        )
    for name in required_names:
        checks.append({"check": f"name:{name}", "ok": name in defined})
    lowered = code.casefold()
    for snippet in must_contain:
        checks.append(
            {"check": f"contains:{snippet}", "ok": snippet.casefold() in lowered}
        )
    for snippet in must_not_contain:
        checks.append(
            {
                "check": f"not_contains:{snippet}",
                "ok": snippet.casefold() not in lowered,
            }
        )

    requirement_ratio = (
        sum(1 for check in checks if check["ok"]) / len(checks) if checks else 1.0
    )
    score = 0.4 + 0.6 * requirement_ratio
    passed = all(check["ok"] for check in checks)
    details = {
        "syntax_ok": True,
        "code_source": source,
        "code_chars": len(code),
        "checks": checks,
        "missing_constructs": [c for c in required_constructs if c not in node_names],
        "missing_names": [n for n in required_names if n not in defined],
        "defined_names": sorted(defined)[:50],
    }
    return _score(score, passed, details)


# ---------------------------------------------------------------------------
# instruction_format
# ---------------------------------------------------------------------------

_FORMAT_KEYS = (
    "max_words",
    "min_words",
    "max_chars",
    "min_lines",
    "max_lines",
    "min_bullets",
    "max_bullets",
    "must_start_with",
    "must_end_with",
    "required_regexes",
    "forbidden_regexes",
    "json_object",
    "json_keys",
)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return []


def grade_instruction_format(
    item: BenchmarkItem, response: str | None
) -> tuple[float, bool, dict[str, Any]]:
    """Check declarative format constraints from ``item.metadata``."""
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    present = [key for key in _FORMAT_KEYS if key in metadata]
    if not present:
        return _score(0.0, False, {"error": "no_constraints"})
    text = _response_text(response)
    if not text.strip():
        return _score(0.0, False, _empty_details(constraints=present))

    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: Any = None) -> None:
        entry: dict[str, Any] = {"check": name, "ok": bool(ok)}
        if detail is not None:
            entry["detail"] = detail
        checks.append(entry)

    words = re.findall(r"\S+", text)
    lines = [line for line in text.splitlines() if line.strip()]
    bullets = _BULLET_RE.findall(text)

    for key, actual, comparator in (
        ("max_words", len(words), lambda value, limit: value <= limit),
        ("min_words", len(words), lambda value, limit: value >= limit),
        ("max_chars", len(text), lambda value, limit: value <= limit),
        ("min_lines", len(lines), lambda value, limit: value >= limit),
        ("max_lines", len(lines), lambda value, limit: value <= limit),
        ("min_bullets", len(bullets), lambda value, limit: value >= limit),
        ("max_bullets", len(bullets), lambda value, limit: value <= limit),
    ):
        if key not in metadata:
            continue
        limit = _as_int(metadata[key])
        if limit is None:
            add(key, False, {"invalid_value": repr(metadata[key])})
            continue
        add(key, comparator(actual, limit), {"actual": actual, "limit": limit})

    if "must_start_with" in metadata:
        prefixes = [
            _normalize(value) for value in _string_list(metadata["must_start_with"])
        ]
        stripped = _strip_edges(_normalize(text))
        add(
            "must_start_with",
            bool(prefixes) and any(stripped.startswith(prefix) for prefix in prefixes),
            {"prefixes": prefixes},
        )

    if "must_end_with" in metadata:
        suffixes = [
            _strip_edges(_normalize(value))
            for value in _string_list(metadata["must_end_with"])
        ]
        stripped = _strip_edges(_normalize(text))
        add(
            "must_end_with",
            bool(suffixes) and any(stripped.endswith(suffix) for suffix in suffixes),
            {"suffixes": suffixes},
        )

    for key, expected_match in (
        ("required_regexes", True),
        ("forbidden_regexes", False),
    ):
        if key not in metadata:
            continue
        for pattern in _string_list(metadata[key]):
            try:
                compiled = re.compile(pattern, re.MULTILINE)
            except re.error as exc:
                add(key, False, {"pattern": pattern, "invalid_regex": str(exc)[:200]})
                continue
            matched = compiled.search(text) is not None
            add(
                key, matched is expected_match, {"pattern": pattern, "matched": matched}
            )

    if metadata.get("json_object"):
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, ValueError) as exc:
            add("json_object", False, {"message": str(exc)[:200]})
        else:
            add(
                "json_object",
                isinstance(payload, dict),
                {"type": type(payload).__name__},
            )
            if isinstance(payload, dict) and "json_keys" in metadata:
                required_keys = _string_list(metadata["json_keys"])
                missing = [key for key in required_keys if key not in payload]
                add(
                    "json_keys",
                    not missing,
                    {"missing": missing, "keys": sorted(payload)},
                )
    elif "json_keys" in metadata:
        add("json_keys", False, {"message": "json_keys requires json_object=true"})

    if not checks:  # pragma: no cover - guarded by ``present`` above
        return _score(0.0, False, {"error": "no_constraints"})

    matched = sum(1 for check in checks if check["ok"])
    score = matched / len(checks)
    details = {
        "method": "declarative_format_constraints",
        "checks": checks,
        "word_count": len(words),
        "line_count": len(lines),
        "bullet_count": len(bullets),
    }
    return _score(score, matched == len(checks), details)


# ---------------------------------------------------------------------------
# spanish_language
# ---------------------------------------------------------------------------

_ES_STOPWORDS = {
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
    "y",
    "o",
    "que",
    "en",
    "para",
    "por",
    "con",
    "sin",
    "es",
    "son",
    "fue",
    "ser",
    "estar",
    "está",
    "estan",
    "están",
    "como",
    "más",
    "pero",
    "si",
    "no",
    "se",
    "su",
    "sus",
    "lo",
    "le",
    "les",
    "me",
    "mi",
    "tu",
    "nos",
    "muy",
    "sobre",
    "entre",
    "cuando",
    "donde",
    "porque",
    "también",
    "hay",
    "ha",
    "han",
    "este",
    "esta",
    "estos",
    "estas",
    "eso",
    "esa",
    "cada",
    "desde",
    "hasta",
    "según",
    "tiene",
    "tienen",
    "puede",
    "pueden",
    "debe",
    "deben",
    "otra",
    "otro",
    "cual",
    "cuál",
    "qué",
    "quien",
    "quién",
    "escribe",
    "responde",
    "frase",
    "ejemplo",
    "necesita",
    "usar",
    "usando",
    "forma",
}
_EN_STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "in",
    "is",
    "are",
    "was",
    "were",
    "for",
    "with",
    "without",
    "on",
    "at",
    "by",
    "from",
    "that",
    "this",
    "these",
    "those",
    "it",
    "its",
    "as",
    "be",
    "been",
    "being",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "can",
    "could",
    "should",
    "would",
    "will",
    "not",
    "but",
    "if",
    "then",
    "than",
    "there",
    "their",
    "they",
    "you",
    "your",
    "we",
    "our",
    "i",
    "my",
    "me",
    "he",
    "she",
    "his",
    "her",
    "them",
    "us",
    "also",
    "such",
    "when",
    "where",
    "why",
    "how",
    "which",
    "who",
    "what",
}
_ACCENT_RE = re.compile(r"[áéíóúñü¿¡]", re.IGNORECASE)


def _spanish_signals(text: str) -> tuple[float, float, float, list[str], list[str]]:
    normalized = _normalize(text)
    tokens = re.findall(r"[a-záéíóúñü]+", normalized)
    es_hits = [token for token in tokens if token in _ES_STOPWORDS]
    en_hits = [token for token in tokens if token in _EN_STOPWORDS]
    accent_hits = len(_ACCENT_RE.findall(normalized))
    es_weight = float(len(es_hits) + 2 * accent_hits)
    en_weight = float(len(en_hits))
    denominator = es_weight + en_weight
    ratio = es_weight / denominator if denominator else 0.0
    return ratio, es_weight, en_weight, es_hits, en_hits


def grade_spanish_language(
    item: BenchmarkItem, response: str | None
) -> tuple[float, bool, dict[str, Any]]:
    """Heuristic Spanish detection plus required-term coverage.

    This is a lexical smoke check, not a language-identification model.
    """
    text = _response_text(response)
    if not text.strip():
        return _score(0.0, False, _empty_details(expected=item.expected))
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    required_terms = _alternatives(metadata.get("required_terms"))
    if not required_terms:
        required_terms = _alternatives(item.expected or "")
    min_ratio = _as_float(metadata.get("min_spanish_ratio"))
    if min_ratio is None or not 0 <= min_ratio <= 1:
        min_ratio = 0.3

    ratio, es_weight, en_weight, es_hits, en_hits = _spanish_signals(text)
    looks_spanish = es_weight >= 2.0 and es_weight >= en_weight and ratio >= min_ratio
    normalized = _normalize(text)
    missing_terms = [
        term for term in required_terms if not _term_present(normalized, term)
    ]
    terms_total = len(required_terms)
    terms_matched = terms_total - len(missing_terms)

    if terms_total:
        score = 0.5 * (1.0 if looks_spanish else 0.0) + 0.5 * (
            terms_matched / terms_total
        )
        passed = looks_spanish and not missing_terms
    else:
        score = 1.0 if looks_spanish else 0.0
        passed = looks_spanish

    details = {
        "method": "lexical_heuristic",
        "heuristic": True,
        "language": "es",
        "looks_spanish": looks_spanish,
        "spanish_ratio": round(ratio, 4),
        "spanish_signals": es_hits[:20],
        "english_signals": en_hits[:20],
        "accent_markers": len(_ACCENT_RE.findall(_normalize(text))),
        "required_terms": required_terms,
        "missing_terms": missing_terms,
        "response_preview": _preview(text),
    }
    return _score(score, passed, details)


# ---------------------------------------------------------------------------
# defensive_cyber_rubric
# ---------------------------------------------------------------------------


def grade_defensive_cyber_rubric(
    item: BenchmarkItem, response: str | None
) -> tuple[float, bool, dict[str, Any]]:
    """Keyword rubric over defensive security concepts (heuristic)."""
    text = _response_text(response)
    if not text.strip():
        return _score(0.0, False, _empty_details(expected=item.expected))
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    concepts = [
        value for value in metadata.get("key_concepts", []) if str(value).strip()
    ]
    if not concepts:
        concepts = _alternatives(item.expected or "")
    if not concepts:
        return _score(0.0, False, {"error": "no_key_concepts"})

    normalized = _normalize(text)
    matched_concepts: list[str] = []
    missing_concepts: list[str] = []
    for concept in concepts:
        variants = _alternatives(concept)
        if any(_concept_present(normalized, variant) for variant in variants):
            matched_concepts.append(str(concept))
        else:
            missing_concepts.append(str(concept))

    forbidden = _alternatives(metadata.get("forbidden_terms"))
    violations = [term for term in forbidden if _concept_present(normalized, term)]
    min_score = _as_float(metadata.get("min_score"))
    if min_score is None or not 0 <= min_score <= 1:
        min_score = 0.6

    score = len(matched_concepts) / len(concepts)
    if violations:
        score = 0.0
    passed = not violations and score >= min_score
    details = {
        "method": "concept_rubric",
        "heuristic": True,
        "matched_concepts": matched_concepts,
        "missing_concepts": missing_concepts,
        "forbidden_violations": violations,
        "min_score": min_score,
        "response_preview": _preview(text),
    }
    return _score(score, passed, details)


# ---------------------------------------------------------------------------
# abstention
# ---------------------------------------------------------------------------

_ABSTENTION_MARKERS = (
    "i don't know",
    "i do not know",
    "i cannot know",
    "i can't know",
    "cannot be determined",
    "can't be determined",
    "cannot be known",
    "can't be known",
    "cannot determine",
    "unable to determine",
    "insufficient information",
    "not enough information",
    "no way to know",
    "impossible to know",
    "impossible to determine",
    "cannot be answered",
    "can't be answered",
    "unanswerable",
    "unknown",
    "uncertain",
    "no information",
    "not specified",
    "not provided",
    "no se puede determinar",
    "no se puede saber",
    "no es posible saber",
    "no es posible determinar",
    "no lo sé",
    "no lo se",
    "no tengo información",
    "no tengo informacion",
    "información insuficiente",
    "informacion insuficiente",
    "no hay información suficiente",
    "no hay informacion suficiente",
    "desconozco",
    "es imposible saber",
    "no dispongo de",
)

_FABRICATION_HINTS = (
    "exactly",
    "approximately",
    "it was",
    "there were",
    "there are",
    "the answer is",
    "the number is",
)


def detect_abstention(text: str | None) -> bool:
    """True when ``text`` explicitly acknowledges not knowing the answer."""
    if not text:
        return False
    normalized = _normalize(text)
    return any(marker in normalized for marker in _ABSTENTION_MARKERS)


def grade_abstention(
    item: BenchmarkItem, response: str | None
) -> tuple[float, bool, dict[str, Any]]:
    """Reward explicit uncertainty; penalize confident ungrounded answers."""
    text = _response_text(response)
    if not text.strip():
        return _score(0.0, False, _empty_details(expected=item.expected))
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    requires_abstention = bool(metadata.get("requires_abstention", True))
    normalized = _normalize(text)
    abstained = detect_abstention(text)
    numbers = [match.group(0) for match in _NUMBER_RE.finditer(text)]
    fabrication_hints = [hint for hint in _FABRICATION_HINTS if hint in normalized]
    fabricated_specifics = bool(numbers) or bool(fabrication_hints)

    if requires_abstention:
        passed = abstained
        score = 1.0 if abstained else 0.0
        details = {
            "method": "explicit_uncertainty_detection",
            "heuristic": True,
            "requires_abstention": True,
            "abstained": abstained,
            "fabricated_specifics": fabricated_specifics,
            "numbers_in_response": numbers[-5:],
            "confidence_hints": fabrication_hints,
            "response_preview": _preview(text),
        }
        return _score(score, passed, details)

    required_terms = _alternatives(metadata.get("required_terms"))
    if not required_terms:
        required_terms = _alternatives(item.expected or "")
    missing_terms = [
        term for term in required_terms if not _term_present(normalized, term)
    ]
    terms_total = len(required_terms)
    terms_matched = terms_total - len(missing_terms)
    score = (
        (terms_matched / terms_total) if terms_total else (0.0 if abstained else 1.0)
    )
    passed = not abstained and not missing_terms
    if abstained:
        score = 0.0
    details = {
        "method": "explicit_uncertainty_detection",
        "heuristic": True,
        "requires_abstention": False,
        "abstained": abstained,
        "required_terms": required_terms,
        "missing_terms": missing_terms,
        "response_preview": _preview(text),
    }
    return _score(score, passed, details)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

Grader = Callable[[BenchmarkItem, str | None], tuple[float, bool, dict[str, Any]]]

_GRADERS: dict[str, Grader] = {
    "exact_match": grade_exact_match,
    "contains_expected": grade_contains_expected,
    "numeric": grade_numeric,
    "python_syntax": grade_python_syntax,
    "instruction_format": grade_instruction_format,
    "spanish_language": grade_spanish_language,
    "defensive_cyber_rubric": grade_defensive_cyber_rubric,
    "abstention": grade_abstention,
}


def available_graders() -> list[str]:
    return sorted(_GRADERS)


def get_grader(name: str) -> Grader:
    """Return a grader by name or raise a typed :class:`ValidationError`."""
    if not isinstance(name, str) or name not in _GRADERS:
        raise ValidationError(
            f"Unknown grader: {name!r}",
            details={"available": available_graders()},
            hint="Use one of the graders listed in .factory documentation.",
        )
    return _GRADERS[name]


def grade_with(
    grader_name: str, item: BenchmarkItem, response: str | None
) -> tuple[float, bool, dict[str, Any]]:
    """Dispatch to a grader, guaranteeing the result contract."""
    grader = get_grader(grader_name)
    try:
        score, passed, details = grader(item, response)
    except Exception as exc:  # noqa: BLE001 - graders must never break a run
        return (
            0.0,
            False,
            {
                "grader": grader_name,
                "grader_version": GRADER_VERSION,
                "error": f"grader_exception:{type(exc).__name__}",
                "message": str(exc)[:400],
            },
        )
    safe_details: dict[str, Any] = dict(details) if isinstance(details, dict) else {}
    safe_details.setdefault("grader", grader_name)
    safe_details["grader_version"] = GRADER_VERSION
    score_value = _as_float(score)
    if score_value is None:
        score_value = 0.0
        passed = False
        safe_details.setdefault("error", "invalid_grader_score")
    return max(0.0, min(1.0, score_value)), bool(passed), safe_details


__all__ = [
    "GRADER_VERSION",
    "Grader",
    "available_graders",
    "detect_abstention",
    "get_grader",
    "grade_abstention",
    "grade_contains_expected",
    "grade_defensive_cyber_rubric",
    "grade_exact_match",
    "grade_instruction_format",
    "grade_numeric",
    "grade_python_syntax",
    "grade_spanish_language",
    "grade_with",
]
