# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Unit tests for the deterministic grader registry."""

from __future__ import annotations

import pytest

from fsociety.core.models import BenchmarkItem
from fsociety.errors import ValidationError
from fsociety.evaluation import graders as graders_module
from fsociety.evaluation.graders import (
    GRADER_VERSION,
    available_graders,
    get_grader,
    grade_abstention,
    grade_contains_expected,
    grade_defensive_cyber_rubric,
    grade_exact_match,
    grade_instruction_format,
    grade_numeric,
    grade_python_syntax,
    grade_spanish_language,
    grade_with,
)


def item(**kwargs) -> BenchmarkItem:
    payload = {"item_id": "t-001", "prompt": "test prompt"}
    payload.update(kwargs)
    return BenchmarkItem(**payload)


# ---------------------------------------------------------------------------
# exact_match
# ---------------------------------------------------------------------------


def test_exact_match_normalizes_case_and_spacing():
    score, passed, details = grade_exact_match(item(expected="Au"), "  au. ")
    assert (score, passed) == (1.0, True)
    assert details["method"] == "normalized_equality"


def test_exact_match_rejects_extra_text():
    score, passed, _ = grade_exact_match(item(expected="Au"), "The symbol is Au.")
    assert score == 0.0
    assert passed is False


def test_exact_match_accepts_metadata_alternatives():
    target = item(expected="colour", metadata={"acceptable_answers": ["color"]})
    score, passed, _ = grade_exact_match(target, "Color")
    assert (score, passed) == (1.0, True)


def test_exact_match_missing_expected_and_empty_response():
    score, passed, details = grade_exact_match(item(expected=None), "anything")
    assert score == 0.0 and passed is False
    assert details["error"] == "missing_expected"

    score, passed, details = grade_exact_match(item(expected="Au"), "")
    assert score == 0.0 and passed is False
    assert details["empty_response"] is True


# ---------------------------------------------------------------------------
# contains_expected
# ---------------------------------------------------------------------------


def test_contains_expected_positive_and_negative():
    target = item(expected="Canberra")
    score, passed, _ = grade_contains_expected(target, "The capital is Canberra.")
    assert (score, passed) == (1.0, True)
    score, passed, _ = grade_contains_expected(target, "The capital is Sydney.")
    assert (score, passed) == (0.0, False)


def test_contains_expected_short_token_requires_word_boundary():
    target = item(expected="Au")
    score, passed, _ = grade_contains_expected(target, "Because it is gold.")
    assert score == 0.0
    assert passed is False
    score, passed, _ = grade_contains_expected(target, "The symbol is Au.")
    assert (score, passed) == (1.0, True)


def test_contains_expected_required_terms_partial_score():
    target = item(
        expected="Au",
        metadata={"required_terms": ["gold", "symbol"]},
    )
    score, passed, details = grade_contains_expected(
        target, "The symbol of the metal is Au."
    )
    assert score == pytest.approx(0.75)
    assert passed is False
    assert details["missing_terms"] == ["gold"]

    score, passed, _ = grade_contains_expected(target, "The symbol of gold is Au.")
    assert (score, passed) == (1.0, True)


def test_contains_expected_empty_response_has_error():
    score, passed, details = grade_contains_expected(item(expected="Au"), "")
    assert score == 0.0 and passed is False
    assert details["empty_response"] is True


# ---------------------------------------------------------------------------
# numeric
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expected", "response"),
    [
        ("126", "The answer is 126."),
        ("1234.5", "1,234.5 km in total"),
        ("4.5", "4.5"),
        ("34", "40 - 15% = 34"),
        ("-3", "The result is -3."),
    ],
)
def test_numeric_positive(expected, response):
    score, passed, _ = grade_numeric(item(expected=expected), response)
    assert (score, passed) == (1.0, True)


def test_numeric_negative_and_tolerance():
    score, passed, details = grade_numeric(item(expected="4.5"), "4.7")
    assert score == 0.0
    assert passed is False
    assert details["difference"] == pytest.approx(0.2)

    target = item(expected="4.5", metadata={"tolerance": 0.01})
    score, passed, _ = grade_numeric(target, "4.50001")
    assert (score, passed) == (1.0, True)


def test_numeric_unparseable_answer_and_expected():
    score, passed, details = grade_numeric(
        item(expected="126"), "one hundred and twenty six"
    )
    assert score == 0.0 and passed is False
    assert details["error"] == "no_numeric_answer"

    score, passed, details = grade_numeric(item(expected="not a number"), "42")
    assert score == 0.0 and passed is False
    assert details["error"] == "invalid_expected"


# ---------------------------------------------------------------------------
# python_syntax
# ---------------------------------------------------------------------------


ADD_ITEM = item(
    expected="def add(a, b):\n    return a + b",
    metadata={
        "required_names": ["add"],
        "required_constructs": ["FunctionDef"],
        "must_contain": ["return"],
    },
)


def test_python_syntax_accepts_fenced_code():
    response = "Here you go:\n```python\ndef add(a, b):\n    return a + b\n```"
    score, passed, details = grade_python_syntax(ADD_ITEM, response)
    assert passed is True
    assert score == 1.0
    assert details["syntax_ok"] is True
    assert details["missing_names"] == []


def test_python_syntax_accepts_unfenced_code_tail():
    response = "Solution:\ndef add(a, b):\n    return a + b"
    _, passed, details = grade_python_syntax(ADD_ITEM, response)
    assert passed is True
    assert details["code_source"] == "code_tail"


def test_python_syntax_reports_syntax_error():
    response = "```python\ndef add(a, b)\n    return a + b\n```"
    score, passed, details = grade_python_syntax(ADD_ITEM, response)
    assert score == 0.0
    assert passed is False
    assert details["error"] == "syntax_error"


def test_python_syntax_missing_requirements_lowers_score():
    target = item(expected="x", metadata={"required_names": ["missing_fn"]})
    score, passed, details = grade_python_syntax(target, "def other():\n    return 1")
    assert passed is False
    assert 0.0 < score < 1.0
    assert details["missing_names"] == ["missing_fn"]


def test_python_syntax_empty_response():
    score, passed, details = grade_python_syntax(ADD_ITEM, "")
    assert score == 0.0 and passed is False
    assert details["empty_response"] is True


# ---------------------------------------------------------------------------
# instruction_format
# ---------------------------------------------------------------------------


def test_instruction_format_bullets():
    target = item(metadata={"min_bullets": 3, "max_bullets": 3})
    _, passed, details = grade_instruction_format(target, "- one\n- two\n- three")
    assert passed is True
    assert details["bullet_count"] == 3

    _, passed, _ = grade_instruction_format(target, "- one\n- two")
    assert passed is False


def test_instruction_format_word_limit():
    target = item(metadata={"max_words": 5})
    _, passed, details = grade_instruction_format(target, "one two three four five")
    assert passed is True
    assert details["word_count"] == 5
    _, passed, _ = grade_instruction_format(target, "one two three four five six")
    assert passed is False


def test_instruction_format_json_and_prefix_constraints():
    json_item = item(metadata={"json_object": True, "json_keys": ["name", "version"]})
    _, passed, _ = grade_instruction_format(json_item, '{"name": "x", "version": "1"}')
    assert passed is True
    _, passed, details = grade_instruction_format(json_item, '{"name": "x"}')
    assert passed is False
    assert details["checks"][-1]["detail"]["missing"] == ["version"]

    prefix_item = item(metadata={"must_start_with": ["Verdict:"]})
    _, passed, _ = grade_instruction_format(prefix_item, "Verdict: caching is safe")
    assert passed is True


def test_instruction_format_invalid_regex_does_not_raise():
    target = item(metadata={"required_regexes": ["[unclosed"]})
    score, passed, details = grade_instruction_format(target, "anything")
    assert score == 0.0
    assert passed is False
    assert details["checks"][0]["detail"]["invalid_regex"]


def test_instruction_format_invalid_constraint_value_and_empty():
    target = item(metadata={"max_words": "many"})
    score, passed, details = grade_instruction_format(target, "some words")
    assert score == 0.0
    assert passed is False
    assert details["checks"][0]["detail"]["invalid_value"] == "'many'"

    score, passed, details = grade_instruction_format(item(metadata={}), "text")
    assert score == 0.0
    assert details["error"] == "no_constraints"

    score, passed, details = grade_instruction_format(
        item(metadata={"max_words": 3}), ""
    )
    assert score == 0.0
    assert details["empty_response"] is True


# ---------------------------------------------------------------------------
# spanish_language
# ---------------------------------------------------------------------------


SPANISH_ITEM = item(
    expected="La capital de Australia es Canberra.",
    metadata={"required_terms": ["canberra"]},
)


def test_spanish_language_positive():
    score, passed, details = grade_spanish_language(
        SPANISH_ITEM, "La capital de Australia es Canberra."
    )
    assert (score, passed) == (1.0, True)
    assert details["heuristic"] is True
    assert details["looks_spanish"] is True


def test_spanish_language_rejects_english_answer():
    score, passed, details = grade_spanish_language(
        SPANISH_ITEM, "The capital of Australia is Canberra."
    )
    assert passed is False
    assert score == pytest.approx(0.5)
    assert details["looks_spanish"] is False


def test_spanish_language_short_answer_is_not_a_sentence():
    score, passed, _ = grade_spanish_language(SPANISH_ITEM, "Canberra")
    assert passed is False
    assert score == pytest.approx(0.5)


def test_spanish_language_missing_term():
    score, passed, details = grade_spanish_language(
        SPANISH_ITEM, "La capital de Australia es Sídney."
    )
    assert passed is False
    assert details["missing_terms"] == ["canberra"]


def test_spanish_language_empty_response():
    score, passed, details = grade_spanish_language(SPANISH_ITEM, "")
    assert score == 0.0 and passed is False
    assert details["empty_response"] is True


# ---------------------------------------------------------------------------
# defensive_cyber_rubric
# ---------------------------------------------------------------------------


def test_defensive_rubric_positive():
    target = item(
        metadata={
            "key_concepts": ["hash|hashing|bcrypt", "salt|salted"],
            "min_score": 0.6,
        }
    )
    score, passed, details = grade_defensive_cyber_rubric(
        target, "Use salted password hashing with bcrypt and a per-user salt."
    )
    assert (score, passed) == (1.0, True)
    assert details["missing_concepts"] == []


def test_defensive_rubric_partial_and_forbidden_terms():
    target = item(metadata={"key_concepts": ["hash", "salt"], "min_score": 0.6})
    score, passed, _ = grade_defensive_cyber_rubric(target, "Use hashing.")
    assert score == 0.5
    assert passed is False

    forbidden = item(
        metadata={
            "key_concepts": ["hash"],
            "forbidden_terms": ["reverse shell"],
        }
    )
    score, passed, details = grade_defensive_cyber_rubric(
        forbidden, "Use hashing, then open a reverse shell to the host."
    )
    assert score == 0.0
    assert passed is False
    assert details["forbidden_violations"] == ["reverse shell"]


def test_defensive_rubric_requires_concepts():
    score, passed, details = grade_defensive_cyber_rubric(item(metadata={}), "text")
    assert score == 0.0
    assert passed is False
    assert details["error"] == "no_key_concepts"


# ---------------------------------------------------------------------------
# abstention
# ---------------------------------------------------------------------------


def test_abstention_positive_english_and_spanish():
    target = item(metadata={"requires_abstention": True})
    for response in (
        "I don't know; that information was never recorded.",
        "The answer cannot be determined from the available information.",
        "No se puede determinar con la información disponible.",
    ):
        score, passed, details = grade_abstention(target, response)
        assert (score, passed) == (1.0, True), response
        assert details["abstained"] is True


def test_abstention_negative_confident_fabrication():
    target = item(metadata={"requires_abstention": True})
    score, passed, details = grade_abstention(
        target, "The number was exactly 1,234,567 grains."
    )
    assert score == 0.0
    assert passed is False
    assert details["abstained"] is False
    assert details["fabricated_specifics"] is True


def test_abstention_answerable_item_requires_the_fact():
    target = item(expected="Paris", metadata={"requires_abstention": False})
    _, passed, _ = grade_abstention(target, "Paris")
    assert passed is True
    _, passed, _ = grade_abstention(target, "I don't know.")
    assert passed is False


def test_abstention_empty_response():
    target = item(metadata={"requires_abstention": True})
    score, passed, details = grade_abstention(target, "")
    assert score == 0.0 and passed is False
    assert details["empty_response"] is True


# ---------------------------------------------------------------------------
# Registry contract
# ---------------------------------------------------------------------------


def test_registry_exposes_all_graders():
    assert set(available_graders()) == {
        "exact_match",
        "contains_expected",
        "numeric",
        "python_syntax",
        "instruction_format",
        "spanish_language",
        "defensive_cyber_rubric",
        "abstention",
    }
    assert get_grader("numeric") is grade_numeric


def test_unknown_grader_raises_validation_error():
    with pytest.raises(ValidationError):
        get_grader("does_not_exist")


def test_grade_with_adds_grader_and_version():
    score, passed, details = grade_with("numeric", item(expected="2"), "2")
    assert (score, passed) == (1.0, True)
    assert details["grader"] == "numeric"
    assert details["grader_version"] == GRADER_VERSION


def test_grade_with_catches_grader_exceptions(monkeypatch):
    def broken(_item, _response):
        raise RuntimeError("boom")

    monkeypatch.setitem(graders_module._GRADERS, "numeric", broken)  # noqa: SLF001
    score, passed, details = grade_with("numeric", item(expected="2"), "2")
    assert score == 0.0
    assert passed is False
    assert details["error"] == "grader_exception:RuntimeError"
    assert details["grader_version"] == GRADER_VERSION


@pytest.mark.parametrize("response", [None, "", "   ", "\x00", "```\n```"])
def test_graders_never_raise_and_scores_are_bounded(response):
    target = item(expected="42", metadata={"required_terms": ["42"]})
    for name in available_graders():
        score, passed, details = grade_with(name, target, response)
        assert 0.0 <= score <= 1.0
        assert isinstance(passed, bool)
        assert isinstance(details, dict)
        assert details["grader_version"] == GRADER_VERSION
