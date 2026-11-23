# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Reproducible evaluation runs.

The runner is the only place where suite items, a backend and the grading
pipeline meet. It records everything needed to re-run and audit a measurement:
suite hash, configuration hash, seed, backend/model identity, hardware
fingerprint, software versions, per-item prompt/response hashes and a JSON +
Markdown report.

Failure semantics:

* A backend exception is isolated to the affected item(s); the run continues.
* ``status="failed"`` means no item could be graded at all.
* Cancellation is cooperative: the ``cancel`` callback is polled between items
  and the report keeps the partial results with ``status="cancelled"``.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from pydantic import Field
from pydantic import ValidationError as PydanticValidationError

from ..context import software_versions
from ..core.hardware import detect_hardware, hardware_fingerprint
from ..core.models import (
    ArtifactRecord,
    BenchmarkItem,
    BenchmarkItemResult,
    BenchmarkReport,
    BenchmarkSuiteInfo,
    ExperimentRecord,
    HardwareProfile,
    StrictModel,
    new_id,
    utcnow_iso,
)
from ..errors import EvaluationError, ValidationError
from ..experiments.store import ExperimentStore
from ..utils.hashing import config_hash, sha256_file, sha256_text
from .backends import ModelBackend
from .graders import GRADER_VERSION, detect_abstention, grade_with
from .reporting import MAX_STORED_RESPONSE_CHARS, write_report
from .suites import SuiteRegistry

#: Configuration fields that influence model output and therefore comparability.
HASHED_CONFIG_FIELDS: tuple[str, ...] = (
    "seed",
    "max_new_tokens",
    "temperature",
    "limit",
    "system_prompt",
)


class EvaluationConfig(StrictModel):
    """Run-level configuration, recorded verbatim in the report."""

    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    max_new_tokens: int = Field(default=256, ge=1, le=32_768)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    batch_size: int = Field(default=1, ge=1, le=64)
    limit: int | None = Field(default=None, ge=1, le=100_000)
    system_prompt: str | None = Field(default=None, max_length=4_000)

    @classmethod
    def from_settings(cls, settings: Any) -> EvaluationConfig:
        """Build a config from a :class:`fsociety.config.PlatformSettings`."""
        return cls(
            seed=int(getattr(settings, "evaluation_seed", 42)),
            max_new_tokens=int(getattr(settings, "max_new_tokens", 256)),
            temperature=float(getattr(settings, "temperature", 0.0)),
        )

    def hashed_payload(self) -> dict[str, Any]:
        """Configuration subset used for the comparability hash."""
        payload = self.model_dump(mode="json")
        return {field: payload[field] for field in HASHED_CONFIG_FIELDS}


def config_hash_for(config: EvaluationConfig) -> str:
    """Hash the comparability-relevant configuration (batch size excluded)."""
    return config_hash(config.hashed_payload())


def _as_config(config: EvaluationConfig | dict[str, Any] | None) -> EvaluationConfig:
    if config is None:
        return EvaluationConfig()
    if isinstance(config, EvaluationConfig):
        return config
    if isinstance(config, dict):
        try:
            return EvaluationConfig.model_validate(config)
        except PydanticValidationError as exc:
            raise ValidationError(
                "Invalid evaluation configuration.",
                details={
                    "errors": exc.errors(include_url=False, include_context=False),
                },
            ) from exc
    raise ValidationError(
        "config must be an EvaluationConfig, a dict or None.",
        details={"received": type(config).__name__},
    )


def _peak_rss_bytes() -> int | None:
    """Peak RSS where the OS exposes it (Linux ``VmHWM``), else current RSS."""
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/self/status", "r", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("VmHWM:"):
                        parts = line.split()
                        if len(parts) >= 2:
                            return int(parts[1]) * 1024
        import psutil

        return int(psutil.Process().memory_info().rss)
    except Exception:  # pragma: no cover - platform dependent
        return None


def _rounded(value: float | None, digits: int = 6) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _summary_metrics(results: Sequence[BenchmarkItemResult]) -> dict[str, Any]:
    """Aggregate already-collected results (shared by overall + per category)."""
    item_count = len(results)
    graded = [result for result in results if result.score is not None]
    passed = [result for result in graded if result.passed]
    errored = [result for result in results if result.error]
    grading_errors = [
        result
        for result in results
        if isinstance(result.details, dict) and result.details.get("error")
    ]
    responses = [
        result.response
        for result in results
        if isinstance(result.response, str) and result.response
    ]
    refusals = [response for response in responses if detect_abstention(response)]
    graded_count = len(graded)
    mean_score = (
        sum(float(result.score or 0.0) for result in graded) / graded_count
        if graded_count
        else None
    )
    return {
        "item_count": item_count,
        "graded_count": graded_count,
        "passed_count": len(passed),
        "error_count": len(errored),
        "grading_error_count": len(grading_errors),
        "pass_rate": _rounded(len(passed) / graded_count) if graded_count else None,
        "mean_score": _rounded(mean_score),
        "error_rate": _rounded(len(errored) / item_count) if item_count else None,
        "mean_response_chars": (
            _rounded(sum(len(response) for response in responses) / len(responses), 2)
            if responses
            else None
        ),
        "refusal_rate": (
            _rounded(len(refusals) / len(responses)) if responses else None
        ),
    }


def compute_metrics(
    results: Sequence[BenchmarkItemResult], planned_item_count: int | None = None
) -> dict[str, Any]:
    """Overall metrics for a run. Rates use graded items as their denominator."""
    metrics = _summary_metrics(results)
    if planned_item_count is not None:
        metrics["planned_item_count"] = int(planned_item_count)
    metrics["grader_version"] = GRADER_VERSION
    metrics["pass_rate_basis"] = "graded items only; backend errors are excluded"
    metrics["refusal_rate_heuristic"] = True
    metrics["statistical_confidence"] = "not computed: item counts are too small"
    return metrics


def compute_category_metrics(
    results: Sequence[BenchmarkItemResult],
) -> dict[str, dict[str, Any]]:
    """Per-category metrics derived from the same aggregation rules."""
    grouped: dict[str, list[BenchmarkItemResult]] = {}
    for result in results:
        grouped.setdefault(result.category or "general", []).append(result)
    return {
        category: _summary_metrics(grouped[category]) for category in sorted(grouped)
    }


def _validate_backend(backend: ModelBackend) -> ModelBackend:
    if not callable(getattr(backend, "generate", None)):
        raise ValidationError(
            "backend must implement generate(prompts, max_new_tokens).",
            details={"received": type(backend).__name__},
        )
    for attribute in ("name", "model_id"):
        value = getattr(backend, attribute, None)
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(
                f"backend.{attribute} must be a non-empty string.",
                details={"received": repr(value)},
            )
    return backend


def _hardware_profile(
    hardware: HardwareProfile | Callable[[], HardwareProfile] | None,
) -> HardwareProfile:
    if hardware is None:
        return detect_hardware()
    if callable(hardware):
        profile = hardware()
    else:
        profile = hardware
    if not isinstance(profile, HardwareProfile):
        raise ValidationError(
            "hardware must be a HardwareProfile, a callable returning one, or None.",
            details={"received": type(profile).__name__},
        )
    return profile


class EvaluationRunner:
    """Coordinates suite loading, generation, grading, metrics and persistence."""

    def __init__(
        self,
        store: ExperimentStore | None = None,
        logger: Any | None = None,
        hardware: HardwareProfile | Callable[[], HardwareProfile] | None = None,
        registry: SuiteRegistry | None = None,
    ) -> None:
        self.store = store
        self.logger = logger
        self._hardware = hardware
        self.registry = registry or SuiteRegistry()

    # ---- backend helpers ---------------------------------------------------
    @staticmethod
    def _prompt_for(item: BenchmarkItem, system_prompt: str | None) -> str:
        if system_prompt:
            return f"{system_prompt.strip()}\n\n{item.prompt}"
        return item.prompt

    @staticmethod
    def _validate_responses(responses: Any, expected: int) -> list[str]:
        if not isinstance(responses, (list, tuple)):
            raise EvaluationError(
                "Backend.generate() must return a list of strings.",
                details={"received": type(responses).__name__},
            )
        if len(responses) != expected:
            raise EvaluationError(
                "Backend.generate() returned the wrong number of responses.",
                details={"expected": expected, "received": len(responses)},
            )
        validated: list[str] = []
        for index, value in enumerate(responses):
            if not isinstance(value, str):
                raise EvaluationError(
                    "Backend.generate() responses must be strings.",
                    details={"index": index, "type": type(value).__name__},
                )
            validated.append(value)
        return validated

    def _generate_chunk(
        self,
        backend: ModelBackend,
        prompts: list[str],
        max_new_tokens: int,
    ) -> tuple[list[str | Exception], bool]:
        """Generate for a chunk; retry individually so one failure stays local."""
        try:
            responses = backend.generate(list(prompts), max_new_tokens)
            return list(self._validate_responses(responses, len(prompts))), False
        except Exception as exc:
            if len(prompts) == 1:
                return [exc], False
            isolated: list[str | Exception] = []
            for prompt in prompts:
                try:
                    single = backend.generate([prompt], max_new_tokens)
                    isolated.append(self._validate_responses(single, 1)[0])
                except Exception as inner:  # noqa: BLE001 - isolate per prompt
                    isolated.append(inner)
            return isolated, True

    def _emit_progress(
        self,
        progress: Callable[[int, int, BenchmarkItemResult], None] | None,
        done: int,
        total: int,
        result: BenchmarkItemResult,
        warnings: list[str],
    ) -> None:
        if progress is None:
            return
        try:
            progress(done, total, result)
        except Exception as exc:  # noqa: BLE001 - a UI callback must not abort a run
            message = (
                "Progress callback raised and was disabled: "
                f"{type(exc).__name__}: {str(exc)[:200]}"
            )
            if message not in warnings:
                warnings.append(message)

    # ---- main entry point --------------------------------------------------
    def run(
        self,
        suite_id: str,
        backend: ModelBackend,
        config: EvaluationConfig | dict[str, Any] | None = None,
        progress: Callable[[int, int, BenchmarkItemResult], None] | None = None,
        cancel: Callable[[], bool] | None = None,
        save_to: str | Path | None = None,
    ) -> BenchmarkReport:
        """Evaluate ``suite_id`` with ``backend`` and return a full report.

        ``progress`` is called as ``progress(done, total, result)`` after every
        graded item; exceptions it raises are recorded in ``report.warnings``
        and do not abort the run. ``cancel`` is polled between items and must
        return ``True`` to request cancellation; the report then keeps the
        partial results with ``status="cancelled"``.
        """
        if not isinstance(suite_id, str) or not suite_id.strip():
            raise ValidationError("suite_id must be a non-empty string.")
        backend = _validate_backend(backend)
        resolved_config = _as_config(config)
        if progress is not None and not callable(progress):
            raise ValidationError("progress must be callable or None.")
        if cancel is not None and not callable(cancel):
            raise ValidationError("cancel must be callable or None.")

        suite_info: BenchmarkSuiteInfo = self.registry.get(suite_id)
        items: list[BenchmarkItem] = self.registry.load_items(suite_id)
        if resolved_config.limit is not None:
            items = items[: resolved_config.limit]
        planned = len(items)
        if planned == 0:
            raise EvaluationError(
                f"Suite {suite_id!r} produced zero items to evaluate."
            )

        hardware = _hardware_profile(self._hardware)
        hardware_fp = hardware.fingerprint or hardware_fingerprint(hardware)
        software = software_versions()
        run_config_hash = config_hash_for(resolved_config)
        config_payload = resolved_config.model_dump(mode="json")
        started_at = utcnow_iso()
        started_clock = time.perf_counter()

        results: list[BenchmarkItemResult] = []
        warnings: list[str] = []
        cancelled = False
        fallback_items = 0
        truncated_items = 0
        grader = suite_info.grader
        batch_size = resolved_config.batch_size

        for start in range(0, planned, batch_size):
            if cancel is not None and cancel():
                cancelled = True
                break
            chunk = items[start : start + batch_size]
            prompts = [
                self._prompt_for(item, resolved_config.system_prompt) for item in chunk
            ]
            item_clock = time.perf_counter()
            outcomes, used_fallback = self._generate_chunk(
                backend, prompts, resolved_config.max_new_tokens
            )
            if used_fallback:
                fallback_items += len(chunk)

            for offset, item in enumerate(chunk):
                if cancel is not None and cancel():
                    cancelled = True
                    break
                prompt = prompts[offset]
                outcome = outcomes[offset]
                prompt_hash = sha256_text(prompt)
                if isinstance(outcome, Exception):
                    results.append(
                        BenchmarkItemResult(
                            item_id=item.item_id,
                            category=item.category,
                            prompt_sha256=prompt_hash,
                            response=None,
                            score=None,
                            passed=None,
                            details={
                                "backend_error": type(outcome).__name__,
                                "grader": grader,
                                "grader_version": GRADER_VERSION,
                            },
                            error=f"{type(outcome).__name__}: {str(outcome)[:500]}",
                        )
                    )
                else:
                    response = outcome
                    score, passed, details = grade_with(grader, item, response)
                    stored_response = response
                    if len(response) > MAX_STORED_RESPONSE_CHARS:
                        stored_response = response[:MAX_STORED_RESPONSE_CHARS]
                        truncated_items += 1
                        details["response_truncated"] = True
                        details["response_chars_full"] = len(response)
                    details["response_chars"] = len(response)
                    details["duration_ms"] = round(
                        (time.perf_counter() - item_clock) * 1000, 3
                    )
                    results.append(
                        BenchmarkItemResult(
                            item_id=item.item_id,
                            category=item.category,
                            prompt_sha256=prompt_hash,
                            response=stored_response,
                            score=score,
                            passed=passed,
                            details=details,
                            error=None,
                        )
                    )
                self._emit_progress(
                    progress, len(results), planned, results[-1], warnings
                )
            if cancelled:
                break

        finished_at = utcnow_iso()
        duration = round(time.perf_counter() - started_clock, 6)
        metrics = compute_metrics(results, planned_item_count=planned)
        metrics["peak_memory_basis"] = (
            "VmHWM (Linux) or current RSS; includes process lifetime"
        )
        category_metrics = compute_category_metrics(results)

        if fallback_items:
            warnings.append(
                f"Batched generation failed for {fallback_items} item(s); each prompt "
                "was retried individually to isolate errors."
            )
        errored = sum(1 for result in results if result.error)
        if errored:
            warnings.append(
                f"{errored} item(s) failed at the backend level; errors are recorded "
                "per item and excluded from pass-rate denominators."
            )
        if truncated_items:
            warnings.append(
                f"{truncated_items} response(s) were truncated to "
                f"{MAX_STORED_RESPONSE_CHARS} characters in the report; grading used "
                "the full text."
            )
        if cancelled:
            warnings.append(
                f"Run cancelled by the caller after {len(results)} of {planned} item(s); "
                "metrics describe the partial run only."
            )
        if suite_info.language not in {"en", "es"}:
            warnings.append(
                f"Suite language {suite_info.language!r} has no dedicated language "
                "heuristic; only declared graders were applied."
            )
        warnings.append(
            "Small hand-written smoke suite: scores are indicative and not "
            "statistically representative."
        )

        graded_count = int(metrics.get("graded_count") or 0)
        if cancelled:
            status = "cancelled"
        elif graded_count == 0:
            status = "failed"
        else:
            status = "succeeded"

        report = BenchmarkReport(
            experiment_id=None,
            model_id=backend.model_id,
            backend=backend.name,
            suite=suite_info,
            suite_hash=suite_info.sha256,
            config=config_payload,
            config_hash=run_config_hash,
            seed=resolved_config.seed,
            hardware=hardware,
            hardware_fingerprint=hardware_fp,
            software=software,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration,
            peak_memory_bytes=_peak_rss_bytes(),
            results=results,
            metrics=metrics,
            category_metrics=category_metrics,
            status=status,
            error=None,
            warnings=warnings,
        )

        if self.logger is not None:
            try:
                self.logger.emit(
                    f"Evaluation {suite_id} finished with status {status}.",
                    level="info" if status == "succeeded" else "warning",
                    category="evaluation",
                    data={
                        "suite_id": suite_id,
                        "status": status,
                        "pass_rate": metrics.get("pass_rate"),
                        "item_count": len(results),
                    },
                )
            except Exception:  # noqa: BLE001 - logging must not break evaluation
                report.warnings.append("Audit logger raised while recording the run.")

        if self.store is not None:
            self._persist(report, resolved_config)

        if save_to is not None:
            json_path, markdown_path = write_report(report, save_to)
            self._record_artifacts(report, json_path, markdown_path)

        return report

    # ---- persistence -------------------------------------------------------
    def _persist(self, report: BenchmarkReport, config: EvaluationConfig) -> None:
        experiment = ExperimentRecord(
            experiment_id=new_id("exp"),
            name=f"evaluation:{report.suite.suite_id}:{report.model_id}"[:200],
            kind="evaluation",
            status=report.status,
            model_id=report.model_id,
            started_at=report.started_at,
            finished_at=report.finished_at,
            params={
                "suite_id": report.suite.suite_id,
                "suite_version": report.suite.version,
                "grader": report.suite.grader,
                "grader_version": GRADER_VERSION,
                "backend": report.backend,
                "config": config.model_dump(mode="json"),
                "hashed_config_fields": list(HASHED_CONFIG_FIELDS),
                "planned_item_count": report.metrics.get("planned_item_count"),
            },
            metrics=dict(report.metrics),
            tags=["evaluation", report.suite.suite_id, report.backend],
            seed=report.seed,
            config_hash=report.config_hash,
            suite_hash=report.suite_hash,
            hardware_fingerprint=report.hardware_fingerprint,
            software=dict(report.software),
            error=report.error,
            notes=f"Suite provenance: {report.suite.provenance}"[:4_000],
        )
        store = self.store
        if store is None:
            return
        store.create_experiment(experiment)
        report.experiment_id = experiment.experiment_id

    def _record_artifacts(
        self,
        report: BenchmarkReport,
        json_path: Path,
        markdown_path: Path,
    ) -> None:
        if self.store is None:
            return
        artifact = ArtifactRecord(
            artifact_id=new_id("art"),
            experiment_id=report.experiment_id,
            kind="evaluation_report",
            path=str(json_path),
            sha256=sha256_file(json_path),
            size_bytes=json_path.stat().st_size,
            metadata={
                "markdown_path": str(markdown_path),
                "suite_id": report.suite.suite_id,
                "suite_hash": report.suite_hash,
                "report_id": report.report_id,
            },
        )
        self.store.add_artifact(artifact)


__all__ = [
    "HASHED_CONFIG_FIELDS",
    "EvaluationConfig",
    "EvaluationRunner",
    "compute_category_metrics",
    "compute_metrics",
    "config_hash_for",
]
