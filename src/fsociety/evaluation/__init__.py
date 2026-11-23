# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Evaluation subsystem: suites, graders, backends, runner, reports, comparison.

Importing this package requires neither PyTorch nor transformers; heavy
dependencies are imported lazily by the concrete backends and the optional
``lm-evaluation-harness`` adapter.
"""

from __future__ import annotations

from ..core.models import (
    BenchmarkItem,
    BenchmarkItemResult,
    BenchmarkReport,
    BenchmarkSuiteInfo,
    ComparisonResult,
    MetricDelta,
)
from .backends import (
    BackendSpec,
    CallableBackend,
    LlamaCppBackend,
    ModelBackend,
    TransformersBackend,
    load_backend,
)
from .graders import (
    GRADER_VERSION,
    available_graders,
    detect_abstention,
    get_grader,
    grade_with,
)
from .lm_eval_adapter import (
    build_command,
    harness_version,
    lm_eval_available,
    run_lm_eval,
)
from .regression import compare_reports, write_comparison
from .reporting import (
    MAX_STORED_RESPONSE_CHARS,
    load_report,
    render_markdown,
    write_report,
)
from .runner import (
    EvaluationConfig,
    EvaluationRunner,
    compute_category_metrics,
    compute_metrics,
    config_hash_for,
)
from .suites import (
    SuiteDocument,
    SuiteRegistry,
    canonical_suite_json,
    rebuild_manifest,
    suite_sha256,
)

__all__ = [
    "GRADER_VERSION",
    "MAX_STORED_RESPONSE_CHARS",
    "BackendSpec",
    "BenchmarkItem",
    "BenchmarkItemResult",
    "BenchmarkReport",
    "BenchmarkSuiteInfo",
    "CallableBackend",
    "ComparisonResult",
    "EvaluationConfig",
    "EvaluationRunner",
    "LlamaCppBackend",
    "MetricDelta",
    "ModelBackend",
    "SuiteDocument",
    "SuiteRegistry",
    "TransformersBackend",
    "available_graders",
    "build_command",
    "canonical_suite_json",
    "compare_reports",
    "compute_category_metrics",
    "compute_metrics",
    "config_hash_for",
    "detect_abstention",
    "get_grader",
    "grade_with",
    "harness_version",
    "lm_eval_available",
    "load_backend",
    "load_report",
    "rebuild_manifest",
    "render_markdown",
    "run_lm_eval",
    "suite_sha256",
    "write_comparison",
    "write_report",
]
