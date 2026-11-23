# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Reports must not persist credential-shaped strings."""

from __future__ import annotations

import json
from pathlib import Path

from fsociety.core.models import (
    BenchmarkItemResult,
    BenchmarkReport,
    BenchmarkSuiteInfo,
)
from fsociety.evaluation.reporting import load_report, write_report
from fsociety.utils.hashing import sha256_text

FAKE_TOKEN = "hf_" + "z" * 34


def _report(response: str) -> BenchmarkReport:
    suite = BenchmarkSuiteInfo(
        suite_id="s",
        name="s",
        version="1",
        description="d",
        grader="exact_match",
        item_count=1,
        sha256=sha256_text("suite"),
    )
    return BenchmarkReport(
        model_id="test/model",
        backend="callable",
        suite=suite,
        suite_hash=suite.sha256,
        config_hash=sha256_text("{}"),
        seed=1,
        results=[
            BenchmarkItemResult(
                item_id="i1",
                category="general",
                prompt_sha256=sha256_text("p"),
                response=response,
                score=1.0,
                passed=True,
            )
        ],
        metrics={"pass_rate": 1.0},
        status="succeeded",
    )


def test_report_redacts_secret_like_output(tmp_path: Path) -> None:
    report = _report(f"the token is {FAKE_TOKEN} inside a response")
    json_path, markdown_path = write_report(report, tmp_path)
    assert FAKE_TOKEN not in json_path.read_text(encoding="utf-8")
    assert FAKE_TOKEN not in markdown_path.read_text(encoding="utf-8")
    stored = load_report(json_path)
    assert stored.report_id == report.report_id
    assert "[REDACTED]" in json_path.read_text(encoding="utf-8")


def test_report_without_secrets_is_unchanged(tmp_path: Path) -> None:
    report = _report("A normal model response.")
    json_path, _ = write_report(report, tmp_path)
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["results"][0]["response"] == "A normal model response."
