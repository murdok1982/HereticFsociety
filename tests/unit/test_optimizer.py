# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Unit tests for the optimizer: search spaces, seeded studies, persistence.

Every test runs a real Optuna study (no mocked sampler) against an in-memory
SQLite store, so trial persistence and determinism are exercised end to end.
"""

from __future__ import annotations

import itertools
import os
import subprocess
import sys
import time
import types
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError

from fsociety.core.models import GpuInfo, HardwareProfile
from fsociety.errors import EngineUnavailableError, FSocietyError, ValidationError
from fsociety.experiments.store import ExperimentStore
from fsociety.optimizer import (
    Dimension,
    OptimizationError,
    OptimizationRequest,
    OptimizationResult,
    Optimizer,
    SearchSpace,
    available_presets,
    default_space,
)
from fsociety.utils.hashing import config_hash

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store() -> ExperimentStore:
    instance = ExperimentStore(":memory:")
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture()
def hardware() -> HardwareProfile:
    return HardwareProfile(
        hostname="builder-host",
        os="Linux",
        os_release="6.8.0",
        architecture="x86_64",
        python_version="3.13.0",
        ram_total_bytes=32 * 1024**3,
        ram_available_bytes=24 * 1024**3,
        gpus=[
            GpuInfo(
                index=0,
                name="NVIDIA RTX 4060",
                backend="cuda",
                total_memory_bytes=8 * 1024**3,
                free_memory_bytes=7 * 1024**3,
            )
        ],
        fingerprint="hw-fixture-fingerprint",
    )


def quadratic_space() -> SearchSpace:
    return SearchSpace(
        dimensions=[
            Dimension(name="x", kind="float", low=0.0, high=1.0),
            Dimension(name="y", kind="int", low=0, high=5),
            Dimension(name="c", kind="categorical", choices=["a", "b", "c"]),
        ]
    )


CATEGORY_PENALTY = {"a": 0.0, "b": 0.4, "c": 0.8}


def quadratic_objective(params: dict[str, Any]) -> float:
    """Deterministic objective with a known optimum at x=0.3, y=2, c='a'."""
    return (
        (params["x"] - 0.3) ** 2
        + (params["y"] - 2) ** 2
        + CATEGORY_PENALTY[params["c"]]
    )


def make_request(**overrides: Any) -> OptimizationRequest:
    payload: dict[str, Any] = {
        "name": "quadratic",
        "space": quadratic_space(),
        "n_trials": 24,
        "seed": 42,
    }
    payload.update(overrides)
    return OptimizationRequest(**payload)


# ---------------------------------------------------------------------------
# Search space
# ---------------------------------------------------------------------------


def test_search_space_grid_size_and_sampling_are_deterministic() -> None:
    space = quadratic_space()
    assert space.grid_size() is None  # 'x' is continuous
    assert space.is_finite() is False

    finite = SearchSpace.preset("runtime")
    assert finite.is_finite() is True
    assert finite.grid_size() == 32 * 4 * 3  # batch ∈ [1,32], 4 contexts, 3 dtypes

    first = space.sample(seed=7)
    second = space.sample(seed=7)
    assert first == second
    assert space.validate_params(first) == []
    assert space.validate_params(space.sample(seed=8)) == []


def test_validate_params_reports_every_problem() -> None:
    space = SearchSpace.preset("modifier")
    problems = space.validate_params(
        {"rank": 1, "alpha": 17, "dropout": 0.5, "unexpected": True}
    )
    joined = " | ".join(problems)
    assert "Unknown parameter 'unexpected'" in joined
    assert "'rank' must be within [4, 64]" in joined
    assert "multiple of step 4" in joined
    assert "'dropout' must be within [0, 0.2]" in joined

    assert "Missing parameter 'dropout'" in " | ".join(
        space.validate_params({"rank": 8, "alpha": 16})
    )
    assert space.validate_params("not-a-dict") == ["Params must be a mapping, got str."]


def test_search_space_roundtrip_and_presets() -> None:
    space = default_space()
    assert SearchSpace.from_dict(space.to_dict()).to_dict() == space.to_dict()
    assert space.names() == ["rank", "alpha", "dropout"]
    assert SearchSpace.preset("lora_rank").to_dict() == space.to_dict()
    assert {"modifier", "lora_rank", "runtime"} <= set(available_presets())

    with pytest.raises(ValidationError) as excinfo:
        SearchSpace.preset("does-not-exist")
    assert "available" in excinfo.value.details

    with pytest.raises(ValidationError):
        SearchSpace.from_dict({"dimensions": [], "unexpected": 1})


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "a", "kind": "categorical", "choices": []},
        {"name": "a", "kind": "categorical", "choices": [1, True]},
        {"name": "a", "kind": "categorical", "choices": ["x", "x"]},
        {"name": "a", "kind": "float", "low": 0.0, "high": 1.0, "log": True},
        {"name": "a", "kind": "float", "low": 1.0, "high": 1.0},
        {"name": "a", "kind": "float", "low": 0.0, "high": 1.0, "step": 5.0},
        {"name": "a", "kind": "int", "low": 0, "high": 4, "step": 0},
        {"name": "a", "kind": "int", "low": 0.5, "high": 4},
        {"name": " a", "kind": "int", "low": 0, "high": 4},
        {"name": "a", "kind": "int", "low": 0, "high": 4, "choices": ["x"]},
    ],
)
def test_dimension_validation_rejects_nonsense(payload: dict[str, Any]) -> None:
    with pytest.raises(PydanticValidationError):
        Dimension(**payload)


def test_search_space_rejects_duplicate_dimension_names() -> None:
    with pytest.raises(PydanticValidationError):
        SearchSpace(
            dimensions=[
                Dimension(name="x", kind="int", low=0, high=2),
                Dimension(name="x", kind="int", low=0, high=3),
            ]
        )


# ---------------------------------------------------------------------------
# Optimization runs
# ---------------------------------------------------------------------------


def test_quadratic_optimum_is_found_and_persisted(
    store: ExperimentStore, hardware: HardwareProfile
) -> None:
    request = make_request()
    result = Optimizer(store, hardware=hardware).run(request, quadratic_objective)

    assert result.status == "succeeded"
    assert result.stop_reason == "completed"
    assert result.sampler == "tpe"
    assert result.directions == ["minimize"]
    assert result.objective_names == ["objective_0"]
    assert result.n_trials_completed == 24
    assert result.n_trials_failed == 0
    assert len(result.trials) == 24
    assert len(result.outcomes) == 24
    assert all(trial.state == "complete" for trial in result.trials)

    # The optimum is reachable and the reported values are the real objective.
    best = result.best_params
    assert best["y"] == 2
    assert abs(best["x"] - 0.3) <= 0.1
    assert result.best_values == [pytest.approx(quadratic_objective(best))]
    assert result.best_values[0] == min(trial.values[0] for trial in result.trials)
    assert result.best_values[0] <= 0.05
    best_trial = next(t for t in result.trials if t.number == result.best_trial_number)
    assert best_trial.params == best

    # Every trial is a valid point of the declared space.
    for trial in result.trials:
        assert request.space.validate_params(trial.params) == []
        assert trial.objective_names == ["objective_0"]
        assert trial.duration_seconds is not None and trial.duration_seconds >= 0.0

    # Persistence round-trip.
    stored = store.list_trials(result.experiment_id)
    assert len(stored) == 24
    assert [t.number for t in stored] == [t.number for t in result.trials]
    assert [t.params for t in stored] == [t.params for t in result.trials]
    assert [t.values for t in stored] == [t.values for t in result.trials]

    experiment = store.get_experiment(result.experiment_id)
    assert experiment.kind == "optimization"
    assert experiment.status == "succeeded"
    assert experiment.seed == 42
    assert experiment.hardware_fingerprint == "hw-fixture-fingerprint"
    assert experiment.config_hash == config_hash(request.model_dump(mode="json"))
    assert experiment.started_at is not None and experiment.finished_at is not None
    assert len(experiment.params["search_space"]["dimensions"]) == 3
    assert experiment.metrics["best_trial_number"] == result.best_trial_number
    assert experiment.metrics["best_values"] == result.best_values


def test_identical_seed_and_space_reproduce_the_same_trials() -> None:
    request = make_request(seed=1234, n_trials=10)
    runs: list[OptimizationResult] = []
    for _ in range(2):
        store = ExperimentStore(":memory:")
        try:
            runs.append(Optimizer(store).run(request, quadratic_objective))
        finally:
            store.close()

    first, second = runs
    assert [trial.params for trial in first.trials] == [
        trial.params for trial in second.trials
    ]
    assert [trial.values for trial in first.trials] == [
        trial.values for trial in second.trials
    ]
    assert first.best_params == second.best_params
    assert first.experiment_id != second.experiment_id


def test_failed_trial_is_recorded_and_the_study_continues(
    store: ExperimentStore,
) -> None:
    calls = {"count": 0}

    def flaky(params: dict[str, Any]) -> float:
        calls["count"] += 1
        if calls["count"] == 3:
            raise RuntimeError("synthetic objective failure")
        return quadratic_objective(params)

    result = Optimizer(store).run(make_request(n_trials=6, seed=7), flaky)

    assert result.status == "succeeded"
    assert result.n_trials_failed == 1
    assert result.n_trials_completed == 5
    assert len(result.trials) == 6

    states = {trial.number: trial.state for trial in result.trials}
    assert states[2] == "failed"
    assert list(states.values()).count("failed") == 1

    failed = next(outcome for outcome in result.outcomes if outcome.state == "failed")
    assert failed.error is not None and "synthetic objective failure" in failed.error
    assert failed.values == []

    stored = {
        trial.number: trial.state for trial in store.list_trials(result.experiment_id)
    }
    assert stored == states

    events = store.list_events(limit=100, category="optimizer")
    failure_events = [event for event in events if event.level == "error"]
    assert failure_events, "trial failures must be logged, never swallowed silently"
    assert "synthetic objective failure" in failure_events[0].message

    experiment = store.get_experiment(result.experiment_id)
    assert experiment.metrics["n_trials_failed"] == 1


def test_all_trials_failing_raises_and_marks_the_experiment_failed(
    store: ExperimentStore,
) -> None:
    def broken(_: dict[str, Any]) -> float:
        raise ValueError("objective is always broken")

    with pytest.raises(OptimizationError) as excinfo:
        Optimizer(store).run(make_request(n_trials=4, seed=11), broken)

    error = excinfo.value
    assert isinstance(error, FSocietyError)
    assert error.code == "optimization_error"
    assert "objective is always broken" in error.message
    assert error.details["n_trials_failed"] == 4
    assert error.hint and "experiments show" in error.hint

    experiments = store.list_experiments()
    assert len(experiments) == 1
    experiment = experiments[0]
    assert experiment.status == "failed"
    assert experiment.finished_at is not None
    assert experiment.error is not None and "always broken" in experiment.error
    trials = store.list_trials(experiment.experiment_id)
    assert len(trials) == 4
    assert all(trial.state == "failed" for trial in trials)
    assert all(trial.values == [] for trial in trials)


def test_cancellation_stops_between_trials(store: ExperimentStore) -> None:
    polls = {"count": 0}

    def cancel() -> bool:
        polls["count"] += 1
        return polls["count"] > 2

    result = Optimizer(store).run(
        make_request(n_trials=20, seed=5), quadratic_objective, cancel=cancel
    )

    assert result.status == "cancelled"
    assert result.stop_reason == "cancelled"
    assert len(result.trials) == 2
    assert result.n_trials_requested == 20
    assert result.best_trial_number is not None
    assert store.get_experiment(result.experiment_id).status == "cancelled"
    assert len(store.list_trials(result.experiment_id)) == 2


def test_cancellation_returns_status_even_when_every_trial_failed(
    store: ExperimentStore,
) -> None:
    polls = {"count": 0}

    def cancel() -> bool:
        polls["count"] += 1
        return polls["count"] > 1

    def broken(_: dict[str, Any]) -> float:
        raise RuntimeError("still broken")

    result = Optimizer(store).run(
        make_request(n_trials=10, seed=2), broken, cancel=cancel
    )

    assert result.status == "cancelled"
    assert result.n_trials_completed == 0
    assert result.n_trials_failed == 1
    assert result.trials[0].state == "failed"
    assert store.get_experiment(result.experiment_id).status == "cancelled"


def test_maximize_direction_is_honoured(store: ExperimentStore) -> None:
    def objective(params: dict[str, Any]) -> float:
        return -float((params["x"] - 0.75) ** 2)

    result = Optimizer(store).run(
        make_request(name="maximize", n_trials=20, seed=21, directions=["maximize"]),
        objective,
    )

    assert result.status == "succeeded"
    assert result.directions == ["maximize"]
    assert abs(result.best_params["x"] - 0.75) <= 0.1
    assert result.best_values[0] == max(trial.values[0] for trial in result.trials)
    assert result.best_values[0] == pytest.approx(objective(result.best_params))


def test_multi_objective_metadata_and_progress(store: ExperimentStore) -> None:
    seen: list[tuple[int, int, float]] = []

    def objective(params: dict[str, Any]) -> tuple[list[float], dict[str, Any]]:
        return (
            [float((params["x"] - 0.3) ** 2), -float(params["y"])],
            {"objective_names": ["regret", "neg_y"], "note": "two objectives"},
        )

    request = make_request(
        name="multi",
        n_trials=8,
        seed=5,
        directions=["minimize", "minimize"],
    )
    result = Optimizer(store).run(
        request,
        objective,
        progress=lambda number, total, best: seen.append((number, total, best)),
    )

    assert result.status == "succeeded"
    assert result.objective_names == ["regret", "neg_y"]
    assert len(result.best_values) == 2
    assert result.best_trial_number is not None
    assert (
        result.objective_metadata[str(result.best_trial_number)]["note"]
        == "two objectives"
    )
    assert [entry[0] for entry in seen] == list(range(1, 9))
    assert all(entry[1] == 8 for entry in seen)
    best_so_far = [entry[2] for entry in seen]
    assert all(
        later <= earlier + 1e-12 for earlier, later in itertools.pairwise(best_so_far)
    )

    stored = store.list_trials(result.experiment_id)
    assert stored[0].objective_names == ["regret", "neg_y"]
    assert store.get_experiment(result.experiment_id).metrics["objective_metadata"]


def test_wrong_objective_arity_fails_cleanly(store: ExperimentStore) -> None:
    with pytest.raises(OptimizationError) as excinfo:
        Optimizer(store).run(make_request(n_trials=2, seed=5), lambda _: [1.0, 2.0])
    assert "direction" in excinfo.value.details["last_error"]
    assert store.list_experiments()[0].status == "failed"


def test_non_finite_objective_values_are_rejected(store: ExperimentStore) -> None:
    with pytest.raises(OptimizationError) as excinfo:
        Optimizer(store).run(make_request(n_trials=2, seed=5), lambda _: float("nan"))
    assert "non-finite" in excinfo.value.details["last_error"]


def test_time_budget_stops_the_study_cleanly(store: ExperimentStore) -> None:
    def slow(params: dict[str, Any]) -> float:
        time.sleep(0.01)
        return quadratic_objective(params)

    result = Optimizer(store).run(
        make_request(n_trials=200, seed=4, timeout_seconds=0.05),
        slow,
    )

    assert result.stop_reason == "timeout"
    assert result.status == "succeeded"
    assert 0 < len(result.trials) < 200
    assert any("time budget" in warning for warning in result.warnings)
    assert store.get_experiment(result.experiment_id).status == "succeeded"
    assert (
        store.get_experiment(result.experiment_id).metrics["stop_reason"] == "timeout"
    )


def test_time_budget_expiring_before_the_first_trial_is_an_error(
    store: ExperimentStore,
) -> None:
    with pytest.raises(OptimizationError) as excinfo:
        Optimizer(store).run(
            make_request(n_trials=5, seed=4, timeout_seconds=1e-9), quadratic_objective
        )

    assert "time budget expired" in excinfo.value.message
    assert store.list_experiments()[0].status == "failed"


def test_missing_optuna_raises_engine_unavailable(
    store: ExperimentStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "optuna", None)

    with pytest.raises(EngineUnavailableError) as excinfo:
        Optimizer(store).run(make_request(n_trials=1), quadratic_objective)

    assert excinfo.value.details["missing"] == ["optuna"]
    assert excinfo.value.hint is not None and "optuna" in excinfo.value.hint
    assert store.list_experiments() == [], (
        "a missing dependency must not fake an experiment"
    )


def test_hardware_fingerprint_falls_back_to_settings(store: ExperimentStore) -> None:
    settings = types.SimpleNamespace(
        redacted=lambda: {"workspace": "/tmp/fsociety", "api_key_configured": False}
    )
    result = Optimizer(store, settings=settings).run(
        make_request(n_trials=2, seed=9), quadratic_objective
    )
    experiment = store.get_experiment(result.experiment_id)
    expected = config_hash(
        {"settings": {"workspace": "/tmp/fsociety", "api_key_configured": False}}
    )
    assert experiment.hardware_fingerprint == expected


# ---------------------------------------------------------------------------
# Boundary validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"n_trials": 0},
        {"n_trials": 100_000},
        {"seed": -1},
        {"name": "   "},
        {"sampler": "cmaes"},
        {"directions": []},
        {"directions": ["sideways"]},
        {"tags": ["  "]},
        {"timeout_seconds": 0},
        {"unexpected": True},
    ],
)
def test_request_validation_rejects_invalid_input(overrides: dict[str, Any]) -> None:
    with pytest.raises(PydanticValidationError):
        make_request(**overrides)


def test_run_rejects_non_callable_objective(store: ExperimentStore) -> None:
    with pytest.raises(ValidationError):
        Optimizer(store).run(make_request(), "not-callable")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Import isolation (NFR-01)
# ---------------------------------------------------------------------------


def test_importing_the_optimizer_pulls_in_no_heavy_dependencies() -> None:
    code = (
        "import sys\n"
        "import fsociety.optimizer\n"
        "heavy = [m for m in ('torch', 'transformers', 'optuna') if m in sys.modules]\n"
        "assert not heavy, heavy\n"
    )
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
