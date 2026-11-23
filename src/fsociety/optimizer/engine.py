# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Seeded, persisted hyper-parameter optimization (Optuna TPE / random search).

This module owns the *execution* half of the optimizer: it validates the
request at the boundary, creates an :class:`ExperimentRecord`, runs a real
Optuna study one trial at a time, and persists every trial (including failed
ones) to the SQLite experiment store.

Design decisions that matter:

* **Manual ``ask``/``tell`` loop.** ``study.optimize`` cannot express "record
  this trial as failed but keep going" without returning a fabricated value.
  Asking and telling explicitly lets a failed trial keep ``TrialState.FAIL``,
  so Optuna's surrogate model never learns from a made-up number.
* **Lazy Optuna import.** Importing the package (and therefore the CLI/API)
  must not require optuna; a missing dependency surfaces as
  :class:`EngineUnavailableError` with an install hint, never a fake fallback.
* **No silent failures.** Every objective exception is logged and persisted as
  an event; if nothing succeeded the run raises :class:`OptimizationError`
  after the experiment has been marked ``failed``.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from ..core.models import (
    EventRecord,
    ExperimentRecord,
    JobStatus,
    StrictModel,
    TrialRecord,
    new_id,
    utcnow_iso,
)
from ..errors import EngineUnavailableError, FSocietyError, ValidationError
from ..security.redaction import redact, redact_obj
from ..utils.hashing import config_hash
from .space import SearchSpace

Direction = Literal["minimize", "maximize"]
SamplerName = Literal["tpe", "random"]
StopReason = Literal["completed", "cancelled", "timeout"]
TrialStateLabel = Literal["complete", "failed", "pruned", "cancelled"]

Objective = Callable[[dict[str, Any]], Any]
"""``objective(params)`` returns ``float``, ``list[float]`` or
``(list[float], metadata_dict)``."""

ProgressCallback = Callable[[int, int, float], None]
"""``progress(trial_number, n_trials, best_value)``; ``best_value`` is the
best first-objective value so far (NaN-free, never a fabrication)."""

CancelCallback = Callable[[], bool]
"""``cancel()`` is polled between trials; ``True`` stops the study."""

MAX_TRIALS = 20_000


class OptimizationError(FSocietyError):
    """The study could not produce a single successful trial."""

    code = "optimization_error"
    http_status = 500


def _require_optuna() -> Any:
    """Import Optuna lazily with an actionable error instead of a stub."""
    try:
        import optuna
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch in tests
        raise EngineUnavailableError(
            "The optimization engine requires Optuna, which is not installed.",
            details={"missing": ["optuna"]},
            hint='Install the optimizer dependency: `pip install "optuna>=5,<6"` '
            "or `uv sync` in a source checkout.",
        ) from exc
    return optuna


def _normalize_objective(
    raw: Any, n_directions: int
) -> tuple[list[float], dict[str, Any]]:
    """Coerce the three supported objective shapes into ``(values, metadata)``."""
    payload = raw
    metadata: dict[str, Any] = {}
    if (
        isinstance(payload, tuple)
        and len(payload) == 2
        and isinstance(payload[1], dict)
    ):
        payload, metadata = payload[0], dict(payload[1])

    if isinstance(payload, bool) or not isinstance(payload, (int, float, list, tuple)):
        raise ValidationError(
            "Objective must return a number, a sequence of numbers, or "
            f"(values, metadata); got {type(payload).__name__}.",
            details={"objective_result": redact_obj(payload)},
        )

    if isinstance(payload, (int, float)):
        values = [float(payload)]
    else:
        values = []
        for item in payload:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise ValidationError(
                    f"Objective returned a non-numeric value: {item!r}.",
                    details={"objective_result": redact_obj(payload)},
                )
            values.append(float(item))

    if len(values) != n_directions:
        raise ValidationError(
            f"Objective returned {len(values)} value(s) but the study has "
            f"{n_directions} direction(s).",
            details={"values": values, "directions": n_directions},
        )
    if not all(math.isfinite(value) for value in values):
        raise ValidationError(
            "Objective returned a non-finite value (NaN or infinity).",
            details={"values": values},
        )
    return values, metadata


def _default_directions() -> list[Direction]:
    return ["minimize"]


class OptimizationRequest(StrictModel):
    """Validated description of one optimization run."""

    name: str = Field(min_length=1, max_length=200)
    space: SearchSpace
    n_trials: int = Field(default=20, ge=1, le=MAX_TRIALS)
    seed: int = Field(default=42, ge=0)
    sampler: SamplerName = "tpe"
    directions: list[Direction] = Field(
        default_factory=_default_directions, min_length=1, max_length=8
    )
    timeout_seconds: float | None = Field(default=None, gt=0)
    study_name: str | None = Field(default=None, max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=32)
    model_id: str | None = Field(default=None, max_length=200)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Optimization name must not be blank.")
        return cleaned

    @field_validator("study_name")
    @classmethod
    def _validate_study_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("tags")
    @classmethod
    def _validate_tags(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for tag in value:
            normalized = str(tag).strip()
            if not normalized:
                raise ValueError("Tags must be non-empty strings.")
            if len(normalized) > 64:
                raise ValueError(f"Tag {normalized[:32]!r}… exceeds 64 characters.")
            if normalized not in cleaned:
                cleaned.append(normalized)
        return cleaned

    @model_validator(mode="after")
    def _validate_directions(self) -> OptimizationRequest:
        if not self.directions:
            raise ValueError("At least one optimization direction is required.")
        return self


class TrialOutcome(StrictModel):
    """Per-trial outcome including the error text the store cannot hold."""

    number: int
    state: TrialStateLabel
    params: dict[str, Any] = Field(default_factory=dict)
    values: list[float] = Field(default_factory=list)
    error: str | None = None
    duration_seconds: float | None = None


class OptimizationResult(StrictModel):
    """Everything a caller needs to reproduce and inspect a study."""

    experiment_id: str
    study_name: str
    sampler: str
    status: JobStatus
    stop_reason: StopReason
    directions: list[Direction]
    best_params: dict[str, Any] = Field(default_factory=dict)
    best_values: list[float] = Field(default_factory=list)
    best_trial_number: int | None = None
    trials: list[TrialRecord] = Field(default_factory=list)
    outcomes: list[TrialOutcome] = Field(default_factory=list)
    n_trials_requested: int
    n_trials_completed: int = 0
    n_trials_failed: int = 0
    objective_names: list[str] = Field(default_factory=list)
    objective_metadata: dict[str, dict[str, Any]] = Field(default_factory=dict)
    duration_seconds: float
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class Optimizer:
    """Runs seeded Optuna studies and persists every trial."""

    def __init__(
        self,
        store: Any,
        settings: Any = None,
        logger: Any = None,
        hardware: Any = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.logger = logger
        self.hardware = hardware

    # ---- public API --------------------------------------------------------
    def run(
        self,
        request: OptimizationRequest,
        objective: Objective,
        progress: ProgressCallback | None = None,
        cancel: CancelCallback | None = None,
    ) -> OptimizationResult:
        if not isinstance(request, OptimizationRequest):
            raise ValidationError(
                f"request must be an OptimizationRequest, got {type(request).__name__}."
            )
        if not callable(objective):
            raise ValidationError(
                f"objective must be callable, got {type(objective).__name__}."
            )
        if progress is not None and not callable(progress):
            raise ValidationError("progress must be callable or None.")
        if cancel is not None and not callable(cancel):
            raise ValidationError("cancel must be callable or None.")

        optuna = _require_optuna()

        warnings: list[str] = []
        grid_size = request.space.grid_size()
        if grid_size is not None and grid_size < request.n_trials:
            warnings.append(
                f"The search space has only {grid_size} distinct configuration(s); "
                f"{request.n_trials} trials will repeat some of them."
            )

        started = time.perf_counter()
        experiment = self._create_experiment(request, warnings)
        study_name = request.study_name or experiment.experiment_id
        objective_names = [
            f"objective_{index}" for index in range(len(request.directions))
        ]
        trials: list[TrialRecord] = []
        outcomes: list[TrialOutcome] = []
        metadata_by_trial: dict[str, dict[str, Any]] = {}
        completed_values: list[list[float]] = []
        n_failed = 0
        stop_reason: StopReason = "completed"
        cancelled = False
        last_error: str | None = None

        sampler = self._build_sampler(optuna, request)
        study_kwargs: dict[str, Any] = {"sampler": sampler, "study_name": study_name}
        if len(request.directions) == 1:
            study_kwargs["direction"] = request.directions[0]
        else:
            study_kwargs["directions"] = list(request.directions)
        study = self._create_study(optuna, study_kwargs)

        self._event(
            "info",
            f"Optimization {request.name!r} started: {request.n_trials} trial(s), "
            f"sampler={request.sampler}, seed={request.seed}.",
            {
                "experiment_id": experiment.experiment_id,
                "space": request.space.describe(),
                "directions": list(request.directions),
                "grid_size": grid_size,
            },
        )

        try:
            for index in range(request.n_trials):
                if cancel is not None and self._poll_cancel(cancel):
                    cancelled = True
                    stop_reason = "cancelled"
                    break
                if (
                    request.timeout_seconds is not None
                    and (time.perf_counter() - started) > request.timeout_seconds
                ):
                    stop_reason = "timeout"
                    warnings.append(
                        f"Stopped after {index} trial(s): the {request.timeout_seconds:g}s "
                        "time budget was exhausted."
                    )
                    break

                record, outcome, metadata, renamed = self._execute_trial(
                    optuna,
                    study,
                    request,
                    objective,
                    objective_names,
                    experiment.experiment_id,
                )
                objective_names = renamed
                trials.append(record)
                outcomes.append(outcome)
                if metadata:
                    metadata_by_trial[str(record.number)] = redact_obj(metadata)

                if outcome.state == "complete":
                    completed_values.append(outcome.values)
                else:
                    n_failed += 1
                    last_error = outcome.error
                    self._event(
                        "error",
                        f"Trial {record.number} failed: {outcome.error}",
                        {
                            "experiment_id": experiment.experiment_id,
                            "trial": record.number,
                        },
                    )

                if progress is not None:
                    best_so_far = _best_first_value(
                        completed_values, request.directions[0]
                    )
                    self._emit_progress(
                        progress,
                        record.number + 1,
                        request.n_trials,
                        best_so_far,
                        experiment.experiment_id,
                    )
        except Exception as exc:
            self._finalize(
                experiment.experiment_id,
                status="failed",
                metrics={
                    "n_trials_completed": len(completed_values),
                    "n_trials_failed": n_failed,
                    "stop_reason": stop_reason,
                },
                error=redact(f"{type(exc).__name__}: {exc}")[:2000],
            )
            self._event(
                "error",
                f"Optimization {request.name!r} aborted: {type(exc).__name__}: {exc}",
                {"experiment_id": experiment.experiment_id},
            )
            raise

        duration_seconds = time.perf_counter() - started
        completed_trials = [
            t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE
        ]

        if not completed_trials:
            if cancelled:
                # The contract is explicit: cancellation marks the experiment
                # cancelled and returns a status, it does not raise.
                if n_failed:
                    warnings.append(
                        f"{n_failed} trial(s) failed before the run was cancelled."
                    )
                result = self._finish(
                    experiment,
                    request,
                    status="cancelled",
                    stop_reason="cancelled",
                    trials=trials,
                    outcomes=outcomes,
                    completed_values=completed_values,
                    objective_names=objective_names,
                    metadata_by_trial=metadata_by_trial,
                    duration_seconds=duration_seconds,
                    warnings=warnings,
                    best_trial=None,
                    n_failed=n_failed,
                    error=None,
                )
                self._event(
                    "warning",
                    f"Optimization {request.name!r} was cancelled before any trial "
                    f"completed ({n_failed} failed).",
                    {"experiment_id": experiment.experiment_id},
                )
                return result

            if stop_reason == "timeout":
                error = (
                    f"The {request.timeout_seconds:g}s time budget expired before any trial "
                    "completed."
                )
            else:
                error = last_error or "No trial completed successfully."
            message = (
                f"Optimization {request.name!r} produced no successful trial "
                f"({n_failed} failed). Last error: {error}"
            )
            self._finalize(
                experiment.experiment_id,
                status="failed",
                metrics={
                    "n_trials_completed": 0,
                    "n_trials_failed": n_failed,
                    "stop_reason": stop_reason,
                },
                error=error[:2000],
            )
            self._event(
                "error",
                message,
                {"experiment_id": experiment.experiment_id, "last_error": error},
            )
            raise OptimizationError(
                message,
                details={
                    "experiment_id": experiment.experiment_id,
                    "n_trials_failed": n_failed,
                    "last_error": error,
                },
                hint="Inspect the persisted trials with `fsociety experiments show "
                f"{experiment.experiment_id}`; fix the objective before retrying.",
            )

        best_trial = self._best_trial(study, request.directions)
        if n_failed:
            warnings.append(
                f"{n_failed} of {len(trials)} trial(s) failed; see stored events."
            )
        if stop_reason == "cancelled":
            status: JobStatus = "cancelled"
        else:
            status = "succeeded"

        result = self._finish(
            experiment,
            request,
            status=status,
            stop_reason=stop_reason,
            trials=trials,
            outcomes=outcomes,
            completed_values=completed_values,
            objective_names=objective_names,
            metadata_by_trial=metadata_by_trial,
            duration_seconds=duration_seconds,
            warnings=warnings,
            best_trial=best_trial,
            n_failed=n_failed,
            error=None,
        )
        self._event(
            "info" if status == "succeeded" else "warning",
            f"Optimization {request.name!r} {status} in {duration_seconds:.2f}s: "
            f"best trial #{best_trial.number} => {list(best_trial.values)}.",
            {
                "experiment_id": experiment.experiment_id,
                "best_params": redact_obj(result.best_params),
                "stop_reason": stop_reason,
            },
        )
        return result

    # ---- internals ---------------------------------------------------------
    def _execute_trial(
        self,
        optuna: Any,
        study: Any,
        request: OptimizationRequest,
        objective: Objective,
        objective_names: list[str],
        experiment_id: str,
    ) -> tuple[TrialRecord, TrialOutcome, dict[str, Any], list[str]]:
        """Ask, evaluate, tell and persist exactly one trial.

        Returns ``(record, outcome, metadata, objective_names)``. Failures are
        recorded as ``TrialState.FAIL`` trials with the error text preserved in
        the outcome and the audit event stream; they never fabricate a value
        for the surrogate model to learn from.
        """
        trial = study.ask()
        trial_started = time.perf_counter()
        params: dict[str, Any] = {}
        values: list[float] = []
        metadata: dict[str, Any] = {}
        names = list(objective_names)
        state: TrialStateLabel = "complete"
        error: str | None = None

        try:
            params = request.space.suggest(trial)
            problems = request.space.validate_params(params)
            if problems:
                raise ValidationError(
                    "Sampled parameters failed space validation: "
                    + "; ".join(problems),
                    details={"params": params},
                )
            values, metadata = _normalize_objective(
                objective(dict(params)), len(request.directions)
            )
            declared_names = metadata.get("objective_names")
            if declared_names is not None:
                if (
                    not isinstance(declared_names, (list, tuple))
                    or len(declared_names) != len(request.directions)
                    or not all(
                        isinstance(item, str) and item.strip()
                        for item in declared_names
                    )
                ):
                    raise ValidationError(
                        "objective_names metadata must be a list of "
                        f"{len(request.directions)} non-empty strings.",
                        details={"objective_names": redact_obj(declared_names)},
                    )
                names = [item.strip() for item in declared_names]
        except Exception as exc:  # objective/space failure => failed trial
            state = "failed"
            error = redact(f"{type(exc).__name__}: {exc}")[:2000]
            values = []
            metadata = {}

        duration = max(0.0, time.perf_counter() - trial_started)
        try:
            if state == "complete":
                study.tell(trial, values[0] if len(values) == 1 else values)
            else:
                study.tell(trial, state=optuna.trial.TrialState.FAIL)
        except Exception as exc:
            # A broken study must not corrupt the persisted record.
            state = "failed"
            error = redact(
                f"Optuna rejected the trial result: {type(exc).__name__}: {exc}"
            )[:2000]
            values = []
            metadata = {}

        record = TrialRecord(
            trial_id=new_id("trial"),
            experiment_id=experiment_id,
            number=trial.number,
            state=state,
            params=redact_obj(params),
            values=values,
            objective_names=names,
            duration_seconds=round(duration, 6),
        )
        self.store.add_trial(record)
        outcome = TrialOutcome(
            number=trial.number,
            state=state,
            params=record.params,
            values=values,
            error=error,
            duration_seconds=record.duration_seconds,
        )
        return record, outcome, metadata, names

    def _build_sampler(self, optuna: Any, request: OptimizationRequest) -> Any:
        if request.sampler == "tpe":
            return optuna.samplers.TPESampler(seed=request.seed)
        return optuna.samplers.RandomSampler(seed=request.seed)

    def _create_study(self, optuna: Any, study_kwargs: dict[str, Any]) -> Any:
        previous = optuna.logging.get_verbosity()
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        try:
            return optuna.create_study(**study_kwargs)
        finally:
            optuna.logging.set_verbosity(previous)

    def _create_experiment(
        self, request: OptimizationRequest, warnings: list[str]
    ) -> ExperimentRecord:
        experiment = ExperimentRecord(
            experiment_id=new_id("exp"),
            name=request.name,
            kind="optimization",
            status="running",
            model_id=request.model_id,
            started_at=utcnow_iso(),
            params={
                "search_space": request.space.to_dict(),
                "n_trials": request.n_trials,
                "sampler": request.sampler,
                "directions": list(request.directions),
                "timeout_seconds": request.timeout_seconds,
                "study_name": request.study_name,
                "grid_size": request.space.grid_size(),
            },
            tags=request.tags,
            seed=request.seed,
            config_hash=config_hash(request.model_dump(mode="json")),
            hardware_fingerprint=self._hardware_fingerprint(),
            software=self._software_versions(),
            notes="; ".join(warnings) if warnings else None,
        )
        self.store.create_experiment(experiment)
        return experiment

    def _finish(
        self,
        experiment: ExperimentRecord,
        request: OptimizationRequest,
        *,
        status: JobStatus,
        stop_reason: StopReason,
        trials: list[TrialRecord],
        outcomes: list[TrialOutcome],
        completed_values: list[list[float]],
        objective_names: list[str],
        metadata_by_trial: dict[str, dict[str, Any]],
        duration_seconds: float,
        warnings: list[str],
        best_trial: Any,
        n_failed: int,
        error: str | None,
    ) -> OptimizationResult:
        best_params: dict[str, Any] = {}
        best_values: list[float] = []
        best_number: int | None = None
        if best_trial is not None:
            best_params = redact_obj(dict(best_trial.params))
            best_values = [float(value) for value in best_trial.values]
            best_number = int(best_trial.number)

        metrics: dict[str, Any] = {
            "n_trials_completed": len(completed_values),
            "n_trials_failed": n_failed,
            "best_trial_number": best_number,
            "best_values": best_values,
            "best_params": best_params,
            "objective_names": objective_names,
            "stop_reason": stop_reason,
            "grid_size": request.space.grid_size(),
            "objective_metadata": metadata_by_trial,
        }
        self._finalize(
            experiment.experiment_id,
            status=status,
            metrics=metrics,
            error=error,
            notes="; ".join(warnings) if warnings else None,
        )
        return OptimizationResult(
            experiment_id=experiment.experiment_id,
            study_name=request.study_name or experiment.experiment_id,
            sampler=request.sampler,
            status=status,
            stop_reason=stop_reason,
            directions=list(request.directions),
            best_params=best_params,
            best_values=best_values,
            best_trial_number=best_number,
            trials=trials,
            outcomes=outcomes,
            n_trials_requested=request.n_trials,
            n_trials_completed=len(completed_values),
            n_trials_failed=n_failed,
            objective_names=objective_names,
            objective_metadata=metadata_by_trial,
            duration_seconds=round(duration_seconds, 6),
            warnings=warnings,
            error=error,
        )

    def _best_trial(self, study: Any, directions: Sequence[Direction]) -> Any:
        if len(directions) == 1:
            return study.best_trial
        # Multi-objective: the Pareto front, resolved deterministically.
        candidates = sorted(study.best_trials, key=lambda trial: trial.number)
        if not candidates:  # pragma: no cover - guarded by the completed check
            raise OptimizationError("The study has no completed trial to report.")
        return candidates[0]

    def _finalize(
        self,
        experiment_id: str,
        *,
        status: JobStatus,
        metrics: dict[str, Any],
        error: str | None = None,
        notes: str | None = None,
    ) -> None:
        fields: dict[str, Any] = {
            "status": status,
            "finished_at": utcnow_iso(),
            "metrics": metrics,
        }
        if error is not None:
            fields["error"] = error
        if notes is not None:
            fields["notes"] = notes
        try:
            self.store.update_experiment(experiment_id, **fields)
        except Exception as exc:  # persistence is the point: never hide this
            self._log_stdlib(
                "error",
                f"Could not persist the final state of experiment {experiment_id}: "
                f"{type(exc).__name__}: {exc}",
            )
            raise

    def _poll_cancel(self, cancel: CancelCallback) -> bool:
        try:
            return bool(cancel())
        except Exception as exc:
            self._event(
                "warning",
                f"Cancel callback raised {type(exc).__name__}: {exc}; continuing the study.",
            )
            return False

    def _emit_progress(
        self,
        progress: ProgressCallback,
        trial_number: int,
        n_trials: int,
        best_value: float | None,
        experiment_id: str,
    ) -> None:
        if best_value is None:  # pragma: no cover - only called after a completed trial
            return
        try:
            progress(trial_number, n_trials, best_value)
        except Exception as exc:
            self._event(
                "warning",
                f"Progress callback raised {type(exc).__name__}: {exc}; "
                "the study continues without UI progress.",
                {"experiment_id": experiment_id},
            )

    def _hardware_fingerprint(self) -> str | None:
        hardware = self.hardware
        if hardware is not None:
            fingerprint = str(getattr(hardware, "fingerprint", "") or "")
            if fingerprint:
                return fingerprint
            try:
                from ..core.hardware import hardware_fingerprint

                return hardware_fingerprint(hardware)
            except Exception as exc:
                self._event(
                    "warning",
                    f"Could not fingerprint the supplied hardware profile: "
                    f"{type(exc).__name__}: {exc}",
                )
        settings = self.settings
        if settings is None:
            return None
        if isinstance(settings, dict):
            return config_hash({"settings": redact_obj(settings)})
        if hasattr(settings, "redacted"):
            return config_hash({"settings": redact_obj(settings.redacted())})
        if hasattr(settings, "model_dump"):
            return config_hash(
                {"settings": redact_obj(settings.model_dump(mode="json"))}
            )
        return config_hash({"settings": redact(str(settings))})

    def _software_versions(self) -> dict[str, str]:
        try:
            from ..context import software_versions

            return software_versions()
        except Exception as exc:  # never block an experiment on metadata lookup
            self._event(
                "warning",
                f"Could not collect software versions: {type(exc).__name__}: {exc}",
            )
            return {}

    # ---- logging -----------------------------------------------------------
    def _event(
        self,
        level: Literal["debug", "info", "warning", "error", "audit"],
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        payload = redact_obj(data or {})
        logger = self.logger
        if logger is not None and hasattr(logger, "emit"):
            try:
                event = logger.emit(
                    message, level=level, category="optimizer", data=payload
                )
            except Exception as exc:
                self._log_stdlib(
                    "warning",
                    f"Logger failed while emitting an optimizer event "
                    f"({type(exc).__name__}: {exc}); falling back to the store.",
                )
            else:
                self._persist_event(event)
                return
        elif logger is not None:
            method = getattr(logger, level if level != "audit" else "info", None)
            if callable(method):
                method(message)

        event = EventRecord(
            created_at=utcnow_iso(),
            level=level,
            category="optimizer",
            message=redact(message),
            data=payload,
        )
        self._persist_event(event)

    def _persist_event(self, event: EventRecord) -> None:
        try:
            self.store.add_event(event)
        except Exception as exc:
            self._log_stdlib(
                "warning",
                f"Could not persist optimizer event: {type(exc).__name__}: {exc}",
            )

    @staticmethod
    def _log_stdlib(level: str, message: str) -> None:
        import logging

        logging.getLogger("fsociety.optimizer").log(
            getattr(logging, level.upper(), logging.INFO), message
        )


def _best_first_value(
    completed: Sequence[Sequence[float]], direction: Direction
) -> float | None:
    """Best first-objective value seen so far (for the progress callback)."""
    if not completed:
        return None
    firsts = [values[0] for values in completed]
    return min(firsts) if direction == "minimize" else max(firsts)
