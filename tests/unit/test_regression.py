# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Unit tests for baseline/candidate comparison."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fsociety.core.models import (
    BenchmarkItemResult,
    BenchmarkReport,
    BenchmarkSuiteInfo,
)
from fsociety.errors import ValidationError
from fsociety.evaluation.regression import compare_reports, write_comparison

SUITE_HASH = "a" * 64
CONFIG_HASH = "b" * 64


def make_report(
    *,
    pass_rate: float | None = 1.0,
    mean_score: float | None = 1.0,
    suite_hash: str = SUITE_HASH,
    config_hash: str = CONFIG_HASH,
    hardware: str = "hw-baseline",
    config: dict | None = None,
    seed: int = 42,
    grader: str = "numeric",
    grader_version: str | None = "1",
    items: list[tuple[str, bool, float]] | None = None,
    model_id: str = "model-a",
    metrics: dict | None = None,
    suite_id: str = "math_word_problems",
) -> BenchmarkReport:
    entries = items if items is not None else [("i-1", True, 1.0), ("i-2", True, 1.0)]
    results = [
        BenchmarkItemResult(
            item_id=item_id,
            category="category-a",
            prompt_sha256="0" * 64,
            response="response",
            score=score,
            passed=passed,
        )
        for item_id, passed, score in entries
    ]
    if metrics is None:
        metrics = {
            "pass_rate": pass_rate,
            "mean_score": mean_score,
            "error_rate": 0.0,
            "refusal_rate": 0.0,
        }
        if grader_version is not None:
            metrics["grader_version"] = grader_version
    suite = BenchmarkSuiteInfo(
        suite_id=suite_id,
        name="Math Word Problems (smoke)",
        version="1.0.0",
        description="Synthetic suite used only inside comparison tests.",
        grader=grader,
        language="en",
        categories=["category-a"],
        item_count=len(results),
        sha256=suite_hash,
    )
    return BenchmarkReport(
        model_id=model_id,
        backend="scripted",
        suite=suite,
        suite_hash=suite_hash,
        config=config if config is not None else {"seed": seed, "temperature": 0.0},
        config_hash=config_hash,
        seed=seed,
        hardware_fingerprint=hardware,
        results=results,
        metrics=metrics,
        status="succeeded",
    )


def test_improved_verdict_and_deltas():
    baseline = make_report(pass_rate=0.5, mean_score=0.5)
    candidate = make_report(pass_rate=0.8, mean_score=0.8, model_id="model-b")
    comparison = compare_reports(baseline, candidate)

    assert comparison.comparable is True
    assert comparison.verdict == "improved"
    assert comparison.incomparability_reasons == []
    by_metric = {delta.metric: delta for delta in comparison.metric_deltas}
    assert by_metric["pass_rate"].delta == pytest.approx(0.3)
    assert by_metric["pass_rate"].direction == "higher_is_better"
    assert by_metric["pass_rate"].regressed is False
    assert by_metric["mean_score"].delta == pytest.approx(0.3)
    assert any("Model ids differ" in warning for warning in comparison.warnings)
    assert comparison.threshold == 0.02


def test_regressed_verdict_marks_metric_regression():
    baseline = make_report(pass_rate=0.9, mean_score=0.9)
    candidate = make_report(pass_rate=0.5, mean_score=0.5)
    comparison = compare_reports(baseline, candidate)

    assert comparison.comparable is True
    assert comparison.verdict == "regressed"
    by_metric = {delta.metric: delta for delta in comparison.metric_deltas}
    assert by_metric["pass_rate"].regressed is True
    assert by_metric["pass_rate"].delta == pytest.approx(-0.4)


def test_equivalent_within_threshold():
    baseline = make_report(pass_rate=0.90, mean_score=0.90)
    candidate = make_report(pass_rate=0.91, mean_score=0.91)
    comparison = compare_reports(baseline, candidate)
    assert comparison.verdict == "equivalent"
    assert comparison.comparable is True


def test_mixed_verdict_from_per_item_moves():
    baseline = make_report(
        pass_rate=0.5,
        mean_score=0.5,
        items=[("i-1", True, 1.0), ("i-2", False, 0.0)],
    )
    candidate = make_report(
        pass_rate=0.5,
        mean_score=0.5,
        items=[("i-1", False, 0.0), ("i-2", True, 1.0)],
    )
    comparison = compare_reports(baseline, candidate)
    assert comparison.comparable is True
    assert comparison.verdict == "mixed"
    assert comparison.regressed_items == ["i-1"]
    assert comparison.improved_items == ["i-2"]


def test_score_only_regression_detected():
    baseline = make_report(items=[("i-1", True, 0.9), ("i-2", True, 1.0)])
    candidate = make_report(items=[("i-1", True, 0.5), ("i-2", True, 1.0)])
    comparison = compare_reports(baseline, candidate)
    assert "i-1" in comparison.regressed_items
    assert comparison.verdict == "regressed"


def test_suite_hash_mismatch_is_incomparable():
    baseline = make_report()
    candidate = make_report(suite_hash="c" * 64)
    comparison = compare_reports(baseline, candidate)
    assert comparison.comparable is False
    assert comparison.verdict == "incomparable"
    assert any("suite_hash" in reason for reason in comparison.incomparability_reasons)


def test_suite_id_mismatch_is_incomparable():
    baseline = make_report()
    candidate = make_report(suite_id="knowledge_core")
    comparison = compare_reports(baseline, candidate)
    assert comparison.comparable is False
    assert any(
        "Different suites" in reason for reason in comparison.incomparability_reasons
    )


def test_config_hash_mismatch_is_incomparable_and_explains_fields():
    baseline = make_report(
        config={"seed": 42, "temperature": 0.0, "max_new_tokens": 256}
    )
    candidate = make_report(
        config={"seed": 7, "temperature": 0.0, "max_new_tokens": 256},
        config_hash="d" * 64,
    )
    comparison = compare_reports(baseline, candidate)
    assert comparison.comparable is False
    assert comparison.verdict == "incomparable"
    joined = " ".join(comparison.incomparability_reasons)
    assert "configuration" in joined.casefold()
    assert "seed" in joined


def test_grader_version_mismatch_is_incomparable():
    baseline = make_report(grader_version="1")
    candidate = make_report(grader_version="2")
    comparison = compare_reports(baseline, candidate)
    assert comparison.comparable is False
    assert any(
        "Grader version" in reason for reason in comparison.incomparability_reasons
    )


def test_missing_grader_version_is_incomparable():
    baseline = make_report(grader_version=None)
    candidate = make_report(grader_version="1")
    comparison = compare_reports(baseline, candidate)
    assert comparison.comparable is False
    assert any(
        "grader version is not recorded" in reason.lower()
        for reason in comparison.incomparability_reasons
    )


def test_missing_metrics_is_incomparable():
    baseline = make_report()
    candidate = make_report(metrics={})
    comparison = compare_reports(baseline, candidate)
    assert comparison.comparable is False
    assert any(
        "no metrics" in reason.lower() for reason in comparison.incomparability_reasons
    )


def test_hardware_mismatch_warns_but_stays_comparable():
    baseline = make_report(pass_rate=0.5, mean_score=0.5, hardware="hw-a")
    candidate = make_report(
        pass_rate=0.8, mean_score=0.8, hardware="hw-b", model_id="model-b"
    )
    comparison = compare_reports(baseline, candidate)
    assert comparison.comparable is True
    assert comparison.verdict == "improved"
    assert any(
        "Hardware fingerprints differ" in warning for warning in comparison.warnings
    )


def test_threshold_is_configurable_and_validated():
    baseline = make_report(pass_rate=0.5, mean_score=0.5)
    candidate = make_report(pass_rate=0.53, mean_score=0.53)
    strict = compare_reports(baseline, candidate, threshold=0.01)
    lax = compare_reports(baseline, candidate, threshold=0.1)
    assert strict.verdict == "improved"
    assert lax.verdict == "equivalent"
    assert strict.threshold == 0.01

    with pytest.raises(ValidationError):
        compare_reports(baseline, candidate, threshold=1.5)
    with pytest.raises(ValidationError):
        compare_reports(baseline, candidate, threshold="0.1")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        compare_reports(baseline, {"report": "not a report"}, 0.1)  # type: ignore[arg-type]


def test_write_comparison_produces_json_and_markdown(tmp_path: Path):
    baseline = make_report(pass_rate=0.5, mean_score=0.5)
    candidate = make_report(pass_rate=0.8, mean_score=0.8, model_id="model-b")
    comparison = compare_reports(baseline, candidate)
    json_path, markdown_path = write_comparison(
        comparison, tmp_path / "comparison.json"
    )

    assert json_path.is_file()
    assert markdown_path.is_file()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["verdict"] == "improved"
    assert payload["comparable"] is True
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "# Evaluation comparison" in markdown
    assert "improved" in markdown
    assert "pass_rate" in markdown
