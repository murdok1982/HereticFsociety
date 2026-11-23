# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""JSON + Markdown evaluation reports.

Reports are written atomically (temporary file + ``os.replace``) so a crashed
process can never leave a half-written artifact that later looks valid. The
Markdown rendering is deliberately explicit about *conditions*: a score is only
meaningful together with the suite hash, configuration and hardware it was
produced under.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from ..core.models import BenchmarkReport, utcnow_iso
from ..errors import EvaluationError, ValidationError
from ..security.redaction import redact, redact_obj

MAX_STORED_RESPONSE_CHARS = 8_000


def atomic_write_text(path: str | Path, text: str) -> Path:
    """Write ``text`` to ``path`` atomically (same-directory temp + replace)."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:  # pragma: no cover - best effort cleanup
                pass
    return destination


def _safe_stem(report: BenchmarkReport) -> str:
    raw = report.report_id or "report"
    cleaned = "".join(char if char.isalnum() or char in "._-" else "_" for char in raw)
    return cleaned[:120] or "report"


def _format_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.4f}"
    if isinstance(value, (int, str)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _cell(value: Any) -> str:
    text = _format_value(value)
    text = text.replace("\\", "\\\\").replace("|", "\\|")
    text = text.replace("\r", " ").replace("\n", " ")
    return text.strip() or "—"


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(":---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_cell(value) for value in row) + " |")
    return "\n".join(lines)


def render_markdown(report: BenchmarkReport) -> str:
    """Render a human-readable report with explicit provenance and conditions."""
    suite = report.suite
    hardware = report.hardware
    lines: list[str] = []
    lines.append(f"# Evaluation report — {suite.name} (`{suite.suite_id}`)")
    lines.append("")
    lines.append(f"- Report ID: `{report.report_id}`")
    lines.append(f"- Experiment ID: `{report.experiment_id or 'not persisted'}`")
    lines.append(f"- Status: **{report.status}**")
    lines.append(f"- Model: `{report.model_id}`")
    lines.append(f"- Backend: `{report.backend}`")
    lines.append(f"- Generated at: {utcnow_iso()}")
    lines.append("")

    lines.append("## Provenance")
    lines.append("")
    rows: list[list[Any]] = [
        ["Suite", f"{suite.name} ({suite.suite_id}) v{suite.version}"],
        ["Suite SHA-256", suite.sha256],
        ["Report suite hash", report.suite_hash],
        ["Grader", suite.grader],
        ["Grader version", report.metrics.get("grader_version", "unknown")],
        ["Configuration hash", report.config_hash],
        ["Seed", report.seed],
        ["Hardware fingerprint", report.hardware_fingerprint or "unknown"],
        ["Host", hardware.hostname if hardware else "unknown"],
        ["Hardware summary", hardware.summary() if hardware else "unknown"],
        ["Started at", report.started_at],
        ["Finished at", report.finished_at or "—"],
        [
            "Duration (s)",
            f"{report.duration_seconds:.3f}"
            if report.duration_seconds is not None
            else "—",
        ],
        [
            "Peak memory (bytes)",
            report.peak_memory_bytes if report.peak_memory_bytes is not None else "—",
        ],
    ]
    lines.append(_table(["Field", "Value"], rows))
    lines.append("")

    if report.software:
        lines.append("### Software")
        lines.append("")
        lines.append(
            _table(
                ["Package", "Version"],
                [[name, version] for name, version in sorted(report.software.items())],
            )
        )
        lines.append("")

    lines.append("## Conditions")
    lines.append("")
    lines.append(
        "Results are only comparable when the suite hash, the configuration hash "
        "(seed, temperature, max_new_tokens, limit, system prompt), the grader version "
        "and the suite grader all match. Timings and peak memory additionally depend on "
        "the machine and on concurrent load; a differing hardware fingerprint makes "
        "performance metrics non-comparable while accuracy metrics can still be compared. "
        "Scores from these small smoke suites are indicative, not statistically "
        "representative, and must never be presented as official benchmark results."
    )
    lines.append("")

    lines.append("## Configuration")
    lines.append("")
    if report.config:
        lines.append(
            _table(
                ["Setting", "Value"],
                [[key, value] for key, value in sorted(report.config.items())],
            )
        )
    else:
        lines.append("_No configuration recorded._")
    lines.append("")

    lines.append("## Metrics")
    lines.append("")
    metric_rows = [
        [key, value]
        for key, value in sorted(report.metrics.items())
        if isinstance(value, (bool, int, float, str)) or value is None
    ]
    if metric_rows:
        lines.append(_table(["Metric", "Value"], metric_rows))
    else:
        lines.append("_No metrics were computed._")
    lines.append("")

    if report.category_metrics:
        lines.append("## Per-category metrics")
        lines.append("")
        category_headers = [
            "Category",
            "Items",
            "Graded",
            "Passed",
            "Errors",
            "Pass rate",
            "Mean score",
        ]
        category_rows: list[list[Any]] = []
        for category in sorted(report.category_metrics):
            values = report.category_metrics[category]
            category_rows.append(
                [
                    category,
                    values.get("item_count"),
                    values.get("graded_count"),
                    values.get("passed_count"),
                    values.get("error_count"),
                    values.get("pass_rate"),
                    values.get("mean_score"),
                ]
            )
        lines.append(_table(category_headers, category_rows))
        lines.append("")

    lines.append("## Item results")
    lines.append("")
    if report.results:
        item_rows = [
            [
                result.item_id,
                result.category,
                result.passed,
                result.score,
                result.error or "—",
            ]
            for result in report.results
        ]
        lines.append(
            _table(["Item", "Category", "Passed", "Score", "Error"], item_rows)
        )
    else:
        lines.append("_No items were evaluated._")
    lines.append("")

    if report.warnings:
        lines.append("## Warnings")
        lines.append("")
        for warning in report.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    if report.error:
        lines.append("## Failure")
        lines.append("")
        lines.append(report.error)
        lines.append("")

    lines.append("## Suite provenance")
    lines.append("")
    lines.append(suite.provenance or "_No provenance recorded for this suite._")
    lines.append("")

    lines.append("## Limitations")
    lines.append("")
    limitations = list(suite.limitations)
    limitations.extend(
        [
            "Small, hand-written smoke suites: scores are indicative, not statistically representative.",
            "No confidence intervals are reported; the item count is too small to support them.",
            "Refusal rate is a lexical heuristic over response text, not a classifier.",
            f"Responses are truncated to {MAX_STORED_RESPONSE_CHARS} characters in the report; grading uses the full text.",
            "Performance timings are machine-dependent and comparable only on identical hardware.",
        ]
    )
    for limitation in limitations:
        lines.append(f"- {limitation}")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "Generated by FSOCIETY AI evaluation. This document is a machine-readable "
        "companion: the authoritative data is the adjacent JSON report."
    )
    lines.append("")
    return "\n".join(lines)


def write_report(report: BenchmarkReport, directory: str | Path) -> tuple[Path, Path]:
    """Write ``report`` as JSON + Markdown into ``directory`` atomically."""
    if not isinstance(report, BenchmarkReport):
        raise ValidationError(
            "write_report expects a BenchmarkReport instance.",
            details={"received": type(report).__name__},
        )
    target_dir = Path(directory).expanduser()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise EvaluationError(
            f"Could not create report directory {target_dir}: {exc}",
            details={"directory": str(target_dir)},
        ) from exc

    stem = _safe_stem(report)
    json_path = target_dir / f"{stem}.json"
    markdown_path = target_dir / f"{stem}.md"

    payload = redact_obj(report.model_dump(mode="json"))
    json_text = (
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    )
    markdown_text = redact(render_markdown(report))

    atomic_write_text(json_path, json_text)
    atomic_write_text(markdown_path, markdown_text)
    return json_path, markdown_path


def load_report(path: str | Path) -> BenchmarkReport:
    """Load and validate a previously written JSON report."""
    source = Path(path).expanduser()
    if not source.is_file():
        raise EvaluationError(
            f"Report file not found: {source}",
            hint="Pass the JSON path returned by write_report().",
        )
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvaluationError(
            f"Report file is not readable JSON: {source}: {exc}",
            details={"path": str(source)},
        ) from exc
    try:
        return BenchmarkReport.model_validate(payload)
    except PydanticValidationError as exc:
        raise ValidationError(
            f"Report file failed schema validation: {source}",
            details={
                "path": str(source),
                "errors": exc.errors(include_url=False, include_context=False),
            },
        ) from exc


__all__ = [
    "MAX_STORED_RESPONSE_CHARS",
    "atomic_write_text",
    "load_report",
    "render_markdown",
    "write_report",
]
