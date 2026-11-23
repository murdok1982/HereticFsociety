# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Baseline vs candidate report comparison.

Comparability is decided on *conditions*, never on model identity: comparing a
base model against a modified model is the whole point of the platform. A pair
is incomparable when the suite content, the comparability-relevant
configuration, the grader version or the metrics themselves differ. Hardware
differences produce a warning (accuracy remains comparable, performance does
not).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, cast

from ..core.models import (
    BenchmarkItemResult,
    BenchmarkReport,
    ComparisonResult,
    MetricDelta,
)
from ..errors import ValidationError
from ..security.redaction import redact, redact_obj
from .reporting import atomic_write_text

#: Metric -> direction used for deltas. ``neutral`` never sets ``regressed``.
METRIC_DIRECTIONS: dict[str, str] = {
    "pass_rate": "higher_is_better",
    "mean_score": "higher_is_better",
    "error_rate": "lower_is_better",
    "mean_response_chars": "neutral",
    "refusal_rate": "neutral",
    "item_count": "neutral",
}

_CONFIG_FIELDS = ("seed", "temperature", "max_new_tokens", "limit", "system_prompt")


def _metric_value(report: BenchmarkReport, name: str) -> float | None:
    value = report.metrics.get(name) if isinstance(report.metrics, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _config_differences(
    baseline: BenchmarkReport, candidate: BenchmarkReport
) -> list[str]:
    differences: list[str] = []
    for field in _CONFIG_FIELDS:
        baseline_value = baseline.config.get(field)
        candidate_value = candidate.config.get(field)
        if baseline_value != candidate_value:
            differences.append(
                f"{field}: baseline={baseline_value!r} candidate={candidate_value!r}"
            )
    return differences


def _grader_version(report: BenchmarkReport) -> str | None:
    value = (
        report.metrics.get("grader_version")
        if isinstance(report.metrics, dict)
        else None
    )
    return str(value) if value is not None else None


def _delta(
    metric: str,
    baseline_value: float | None,
    candidate_value: float | None,
    direction: Literal["higher_is_better", "lower_is_better", "neutral"],
    threshold: float,
) -> MetricDelta:
    delta: float | None = None
    relative: float | None = None
    if baseline_value is not None and candidate_value is not None:
        delta = candidate_value - baseline_value
        if baseline_value != 0:
            relative = delta / abs(baseline_value)
    regressed = False
    if delta is not None:
        if direction == "higher_is_better" and delta < -threshold:
            regressed = True
        elif direction == "lower_is_better" and delta > threshold:
            regressed = True
    return MetricDelta(
        metric=metric,
        baseline=baseline_value,
        candidate=candidate_value,
        delta=round(delta, 6) if delta is not None else None,
        relative_delta=round(relative, 6) if relative is not None else None,
        direction=direction,
        regressed=regressed,
    )


def _index_results(
    report: BenchmarkReport,
) -> dict[str, BenchmarkItemResult]:
    indexed: dict[str, BenchmarkItemResult] = {}
    for result in report.results:
        if result.passed is None and result.score is None:
            continue
        indexed[result.item_id] = result
    return indexed


def compare_reports(
    baseline: BenchmarkReport,
    candidate: BenchmarkReport,
    threshold: float = 0.02,
) -> ComparisonResult:
    """Compare two reports and classify the candidate against the baseline."""
    if not isinstance(baseline, BenchmarkReport) or not isinstance(
        candidate, BenchmarkReport
    ):
        raise ValidationError(
            "compare_reports expects two BenchmarkReport instances.",
            details={
                "baseline": type(baseline).__name__,
                "candidate": type(candidate).__name__,
            },
        )
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise ValidationError("threshold must be a number between 0 and 1.")
    if not 0 <= float(threshold) <= 1:
        raise ValidationError("threshold must be between 0 and 1.")
    threshold_value = float(threshold)

    reasons: list[str] = []
    warnings: list[str] = []

    if baseline.suite.suite_id != candidate.suite.suite_id:
        reasons.append(
            f"Different suites: baseline={baseline.suite.suite_id!r} "
            f"candidate={candidate.suite.suite_id!r}."
        )
    if baseline.suite_hash != candidate.suite_hash:
        reasons.append(
            "Suite content differs (suite_hash mismatch): item wording or expected "
            "answers changed, so scores are not comparable."
        )
    if baseline.config_hash != candidate.config_hash:
        differences = _config_differences(baseline, candidate)
        detail = (
            "; ".join(differences) if differences else "configuration payload differs"
        )
        reasons.append(f"Evaluation configuration differs: {detail}.")
    if baseline.suite.grader != candidate.suite.grader:
        reasons.append(
            f"Grader differs: baseline={baseline.suite.grader!r} "
            f"candidate={candidate.suite.grader!r}."
        )

    baseline_grader = _grader_version(baseline)
    candidate_grader = _grader_version(candidate)
    if baseline_grader is None or candidate_grader is None:
        reasons.append("Grader version is not recorded in at least one report.")
    elif baseline_grader != candidate_grader:
        reasons.append(
            f"Grader version differs: baseline={baseline_grader} "
            f"candidate={candidate_grader}."
        )

    for label, report in (("Baseline", baseline), ("Candidate", candidate)):
        if not isinstance(report.metrics, dict) or not report.metrics:
            reasons.append(f"{label} report has no metrics.")
            continue
        if (
            _metric_value(report, "pass_rate") is None
            and _metric_value(report, "mean_score") is None
        ):
            reasons.append(
                f"{label} report has no graded metrics (pass_rate/mean_score)."
            )

    comparable = not reasons

    if (
        baseline.hardware_fingerprint
        and candidate.hardware_fingerprint
        and baseline.hardware_fingerprint != candidate.hardware_fingerprint
    ):
        warnings.append(
            "Hardware fingerprints differ "
            f"(baseline={baseline.hardware_fingerprint}, "
            f"candidate={candidate.hardware_fingerprint}): accuracy metrics remain "
            "comparable, but timings, throughput and peak memory are not."
        )
    if baseline.model_id != candidate.model_id:
        warnings.append(
            "Model ids differ "
            f"(baseline={baseline.model_id!r}, candidate={candidate.model_id!r}): "
            "this is expected for a base-vs-modified comparison."
        )

    deltas: list[MetricDelta] = []
    for metric, direction in METRIC_DIRECTIONS.items():
        baseline_value = _metric_value(baseline, metric)
        candidate_value = _metric_value(candidate, metric)
        if baseline_value is None or candidate_value is None:
            continue
        deltas.append(
            _delta(
                metric,
                baseline_value,
                candidate_value,
                cast(
                    Literal["higher_is_better", "lower_is_better", "neutral"],
                    direction,
                ),
                threshold_value,
            )
        )

    baseline_items = _index_results(baseline)
    candidate_items = _index_results(candidate)
    shared = sorted(set(baseline_items) & set(candidate_items))
    regressed_items: list[str] = []
    improved_items: list[str] = []
    for item_id in shared:
        before = baseline_items[item_id]
        after = candidate_items[item_id]
        if before.passed and not after.passed:
            regressed_items.append(item_id)
            continue
        if not before.passed and after.passed:
            improved_items.append(item_id)
            continue
        if before.score is None or after.score is None:
            continue
        item_delta = float(after.score) - float(before.score)
        if item_delta < -threshold_value:
            regressed_items.append(item_id)
        elif item_delta > threshold_value:
            improved_items.append(item_id)

    unmatched = abs(len(baseline_items) - len(candidate_items))
    if unmatched:
        warnings.append(
            f"{unmatched} graded item(s) exist only in one report; they were excluded "
            "from the per-item comparison."
        )
    if baseline_items and candidate_items and not shared:
        warnings.append("No graded item ids are shared between the two reports.")

    verdict: str
    if not comparable:
        verdict = "incomparable"
    else:
        baseline_pass = _metric_value(baseline, "pass_rate")
        candidate_pass = _metric_value(candidate, "pass_rate")
        if baseline_pass is not None and candidate_pass is not None:
            pass_delta: float | None = candidate_pass - baseline_pass
        else:
            baseline_mean = _metric_value(baseline, "mean_score")
            candidate_mean = _metric_value(candidate, "mean_score")
            pass_delta = (
                candidate_mean - baseline_mean
                if baseline_mean is not None and candidate_mean is not None
                else None
            )
        if pass_delta is not None and pass_delta > threshold_value:
            verdict = "improved"
        elif pass_delta is not None and pass_delta < -threshold_value:
            verdict = "regressed"
        elif regressed_items and improved_items:
            verdict = "mixed"
        elif regressed_items:
            verdict = "regressed"
        elif improved_items:
            verdict = "improved"
        else:
            verdict = "equivalent"

    return ComparisonResult(
        baseline_report_id=baseline.report_id,
        candidate_report_id=candidate.report_id,
        comparable=comparable,
        incomparability_reasons=reasons,
        metric_deltas=deltas,
        regressed_items=regressed_items,
        improved_items=improved_items,
        warnings=warnings,
        verdict=verdict,  # type: ignore[arg-type]
        threshold=threshold_value,
    )


def _comparison_markdown(comparison: ComparisonResult) -> str:
    lines: list[str] = ["# Evaluation comparison", ""]
    lines.append(f"- Baseline report: `{comparison.baseline_report_id}`")
    lines.append(f"- Candidate report: `{comparison.candidate_report_id}`")
    lines.append(f"- Verdict: **{comparison.verdict}**")
    lines.append(f"- Comparable: {'yes' if comparison.comparable else 'no'}")
    lines.append(f"- Threshold: {comparison.threshold}")
    lines.append("")

    lines.append("## Incomparability reasons")
    lines.append("")
    if comparison.incomparability_reasons:
        for reason in comparison.incomparability_reasons:
            lines.append(f"- {reason}")
    else:
        lines.append("_None: both reports were produced under matching conditions._")
    lines.append("")

    lines.append("## Metric deltas")
    lines.append("")
    if comparison.metric_deltas:
        lines.append(
            "| Metric | Baseline | Candidate | Delta | Relative | Direction | Regressed |"
        )
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
        for delta in comparison.metric_deltas:
            lines.append(
                "| "
                + " | ".join(
                    [
                        delta.metric,
                        _fmt(delta.baseline),
                        _fmt(delta.candidate),
                        _fmt(delta.delta),
                        _fmt(delta.relative_delta),
                        delta.direction,
                        "yes" if delta.regressed else "no",
                    ]
                )
                + " |"
            )
    else:
        lines.append("_No shared numeric metrics._")
    lines.append("")

    lines.append("## Item changes")
    lines.append("")
    lines.append(
        "- Regressed: "
        + (", ".join(f"`{item}`" for item in comparison.regressed_items) or "none")
    )
    lines.append(
        "- Improved: "
        + (", ".join(f"`{item}`" for item in comparison.improved_items) or "none")
    )
    lines.append("")

    if comparison.warnings:
        lines.append("## Warnings")
        lines.append("")
        for warning in comparison.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "Comparability rules: identical suite hash, identical configuration hash, "
        "identical grader version, and graded metrics present in both reports. "
        "Differing model ids are expected and do not make a comparison invalid."
    )
    lines.append("")
    return "\n".join(lines)


def _fmt(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.6f}"


def write_comparison(
    comparison: ComparisonResult,
    path: str | Path,
    markdown_path: str | Path | None = None,
) -> tuple[Path, Path]:
    """Write ``comparison`` as JSON plus a Markdown companion, atomically."""
    if not isinstance(comparison, ComparisonResult):
        raise ValidationError(
            "write_comparison expects a ComparisonResult instance.",
            details={"received": type(comparison).__name__},
        )
    json_path = Path(path).expanduser()
    markdown_destination = (
        Path(markdown_path).expanduser()
        if markdown_path is not None
        else json_path.with_suffix(".md")
    )
    payload: dict[str, Any] = redact_obj(comparison.model_dump(mode="json"))
    json_text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    atomic_write_text(json_path, json_text)
    atomic_write_text(markdown_destination, redact(_comparison_markdown(comparison)))
    return json_path, markdown_destination


__all__ = [
    "METRIC_DIRECTIONS",
    "compare_reports",
    "write_comparison",
]
