# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Unit tests for the planning layer: estimates, constraints, recommendations.

The planner only *estimates*: these tests assert decisions and explanations,
never measured memory.
"""

from __future__ import annotations

import pytest

from fsociety.core.models import (
    GpuInfo,
    HardwareProfile,
    ModelInfo,
    ResourceEstimate,
    TorchInfo,
)
from fsociety.errors import ValidationError
from fsociety.optimizer import (
    ConstraintReport,
    ExecutionPlan,
    SearchSpace,
    describe_plan,
    merge_plan_with_params,
    plan,
    recommend_execution_plan,
    recommend_search_space,
    validate_configuration,
)

GIB = 1024**3


@pytest.fixture()
def tiny_model() -> ModelInfo:
    return ModelInfo(
        model_id="tiny-1m",
        parameter_count=1_000_000,
        hidden_size=1024,
        num_hidden_layers=16,
        num_attention_heads=16,
        num_key_value_heads=8,
        intermediate_size=4096,
        max_position_embeddings=2048,
        torch_dtype="bfloat16",
    )


@pytest.fixture()
def huge_model() -> ModelInfo:
    return ModelInfo(
        model_id="huge-70b",
        parameter_count=70_000_000_000,
        hidden_size=8192,
        num_hidden_layers=80,
        num_attention_heads=64,
        num_key_value_heads=8,
        intermediate_size=28672,
        max_position_embeddings=4096,
    )


@pytest.fixture()
def gpu_hardware() -> HardwareProfile:
    return HardwareProfile(
        hostname="planner-host",
        os="Linux",
        os_release="6.8.0",
        architecture="x86_64",
        python_version="3.13.0",
        ram_total_bytes=32 * GIB,
        ram_available_bytes=24 * GIB,
        gpus=[
            GpuInfo(
                index=0,
                name="NVIDIA RTX 4060",
                backend="cuda",
                total_memory_bytes=8 * GIB,
                free_memory_bytes=7 * GIB,
            )
        ],
        backends={"cpu": True, "cuda_driver": True, "cuda_torch": True},
        torch=TorchInfo(
            installed=True, cuda_available=True, dtype_support=["bfloat16", "float16"]
        ),
        fingerprint="hw-planner-fixture",
    )


@pytest.fixture()
def dual_gpu_hardware(gpu_hardware: HardwareProfile) -> HardwareProfile:
    second = GpuInfo(
        index=1,
        name="NVIDIA RTX 4060",
        backend="cuda",
        total_memory_bytes=8 * GIB,
        free_memory_bytes=7 * GIB,
    )
    return gpu_hardware.model_copy(update={"gpus": [*gpu_hardware.gpus, second]})


# ---------------------------------------------------------------------------
# plan()
# ---------------------------------------------------------------------------


def test_plan_small_model_fits_with_estimate_label(
    tiny_model: ModelInfo, gpu_hardware: HardwareProfile
) -> None:
    estimate = plan(
        tiny_model,
        gpu_hardware,
        batch_size=1,
        sequence_length=1024,
        reserve_ratio=0.1,
    )

    assert isinstance(estimate, ResourceEstimate)
    assert estimate.kind == "estimate"
    assert estimate.fits is True
    assert estimate.dtype == "bfloat16"
    assert estimate.quantization == "none"
    assert estimate.batch_size == 1
    assert estimate.sequence_length == 1024
    assert estimate.breakdown.weights_bytes > 0
    assert estimate.breakdown.total_bytes == estimate.breakdown.total_bytes
    assert estimate.headroom_bytes is not None and estimate.headroom_bytes > 0
    assert estimate.hardware_profile_id == gpu_hardware.profile_id
    assert any(
        "estimate" in note or "Weights use" in note for note in estimate.assumptions
    )

    report = validate_configuration(estimate)
    assert isinstance(report, ConstraintReport)
    assert report.ok is True
    assert report.fit == "fits"
    assert report.violations == []
    assert report.total_bytes == estimate.breakdown.total_bytes
    assert 0.0 < report.utilization <= 1.0
    assert report.warnings, "reports must state that the figures are estimates"
    assert any("not measurements" in warning for warning in report.warnings)


def test_plan_warns_when_context_exceeds_model_limit(
    tiny_model: ModelInfo, gpu_hardware: HardwareProfile
) -> None:
    estimate = plan(tiny_model, gpu_hardware, batch_size=1, sequence_length=4096)
    assert any(
        "max_position_embeddings" in assumption for assumption in estimate.assumptions
    )


def test_plan_70b_does_not_fit_and_explains_why(
    huge_model: ModelInfo, gpu_hardware: HardwareProfile
) -> None:
    estimate = plan(huge_model, gpu_hardware, batch_size=1, sequence_length=2048)
    assert estimate.fits is False
    assert estimate.headroom_bytes is not None and estimate.headroom_bytes < 0

    report = validate_configuration(estimate)
    assert report.ok is False
    assert report.fit == "does_not_fit"
    assert report.violations
    assert "exceeds" in report.violations[0]
    assert "GiB" in report.violations[0]
    assert report.recommendations
    assert any(
        "quantization" in recommendation.lower()
        or "smaller model" in recommendation.lower()
        for recommendation in report.recommendations
    )


def test_plan_without_hardware_reports_unknown_fit(tiny_model: ModelInfo) -> None:
    estimate = plan(
        tiny_model, None, batch_size=1, sequence_length=2048, dtype="float16"
    )
    assert estimate.fits is None
    assert estimate.headroom_bytes is None
    assert estimate.hardware_profile_id is None
    assert any(
        "No hardware profile" in assumption for assumption in estimate.assumptions
    )

    report = validate_configuration(estimate)
    assert report.ok is True
    assert report.fit == "unknown"
    assert report.warnings
    assert any("could not be determined" in warning for warning in report.warnings)
    assert report.recommendations
    assert report.budget_bytes is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"dtype": "int4"},
        {"dtype": 4},
        {"quantization": "awq"},
        {"quantization": 8},
        {"batch_size": 0},
        {"batch_size": True},
        {"sequence_length": 0},
        {"sequence_length": "1024"},
        {"reserve_ratio": 1.0},
        {"reserve_ratio": -0.1},
        {"reserve_ratio": "0.1"},
    ],
)
def test_plan_validates_its_inputs(
    tiny_model: ModelInfo, gpu_hardware: HardwareProfile, kwargs: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        plan(tiny_model, gpu_hardware, **kwargs)  # type: ignore[arg-type]


def test_plan_rejects_models_without_parameter_count() -> None:
    with pytest.raises(ValidationError):
        plan(ModelInfo(model_id="unknown-shape"), None)
    with pytest.raises(ValidationError):
        plan({"model_id": "not-a-model-info"}, None)  # type: ignore[arg-type]


def test_validate_configuration_rejects_foreign_objects() -> None:
    with pytest.raises(ValidationError):
        validate_configuration({"fits": True})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# recommend_execution_plan()
# ---------------------------------------------------------------------------


def test_recommend_execution_plan_for_a_fitting_model(
    tiny_model: ModelInfo, gpu_hardware: HardwareProfile
) -> None:
    execution = recommend_execution_plan(
        tiny_model, gpu_hardware, sequence_length=1024, batch_size=2
    )

    assert isinstance(execution, ExecutionPlan)
    assert execution.kind == "estimate"
    assert execution.report.fit == "fits"
    assert execution.report.ok is True
    assert execution.device == "cuda"
    assert execution.device_map == 0
    assert execution.batch_size >= 1
    assert execution.sequence_length == 1024
    assert execution.dtype in {"bfloat16", "float16", "float32"}
    assert execution.rationale and any(
        "Baseline" in line for line in execution.rationale
    )
    assert execution.estimate.fits is True
    assert "kind='estimate'" in describe_plan(execution)


def test_recommend_execution_plan_uses_auto_device_map_with_several_gpus(
    tiny_model: ModelInfo, dual_gpu_hardware: HardwareProfile
) -> None:
    execution = recommend_execution_plan(
        tiny_model, dual_gpu_hardware, sequence_length=512
    )
    assert execution.device == "cuda"
    assert execution.device_map == "auto"


def test_recommend_execution_plan_flags_an_unrunnable_model(
    huge_model: ModelInfo, gpu_hardware: HardwareProfile
) -> None:
    execution = recommend_execution_plan(huge_model, gpu_hardware)

    assert execution.kind == "estimate"
    assert execution.report.fit == "does_not_fit"
    assert execution.report.ok is False
    assert execution.warnings
    assert any("does not fit" in warning for warning in execution.warnings)
    assert execution.rationale


def test_recommend_execution_plan_warns_without_hardware(tiny_model: ModelInfo) -> None:
    execution = recommend_execution_plan(tiny_model, None)

    assert execution.report.fit == "unknown"
    assert execution.device == "cpu"
    assert execution.device_map is None
    assert any("unknown" in warning.lower() for warning in execution.warnings)
    assert any("CPU" in line for line in execution.rationale)


# ---------------------------------------------------------------------------
# recommend_search_space()
# ---------------------------------------------------------------------------


def test_recommend_search_space_bounds_execution_dimensions(
    tiny_model: ModelInfo, gpu_hardware: HardwareProfile
) -> None:
    space = recommend_search_space(tiny_model, gpu_hardware)

    assert isinstance(space, SearchSpace)
    names = set(space.names())
    assert {
        "rank",
        "alpha",
        "dropout",
        "batch_size",
        "sequence_length",
        "dtype",
    } <= names
    assert space.is_finite() is True

    sample = space.sample(seed=11)
    assert space.validate_params(sample) == []
    assert sample == space.sample(seed=11)
    assert sample != space.sample(seed=12)

    batch_dimension = space.dimension("batch_size")
    assert batch_dimension.low == 1
    assert batch_dimension.high is not None and batch_dimension.high >= 1
    assert "quantization" in names  # 8 GiB cannot hold every combination we may want

    sequence_dimension = space.dimension("sequence_length")
    assert 4096 not in sequence_dimension.choices  # exceeds max_position_embeddings


def test_recommend_search_space_collapses_batch_for_an_oversized_model(
    huge_model: ModelInfo, gpu_hardware: HardwareProfile
) -> None:
    space = recommend_search_space(huge_model, gpu_hardware)

    batch_dimension = space.dimension("batch_size")
    assert batch_dimension.high == 1
    assert "bnb_4bit" in space.dimension("quantization").choices
    assert space.sample(seed=3)["batch_size"] == 1


def test_recommend_search_space_without_hardware_stays_generic(
    tiny_model: ModelInfo,
) -> None:
    space = recommend_search_space(tiny_model, None)
    assert space.grid_size() is not None
    assert space.validate_params(space.sample(seed=2)) == []


# ---------------------------------------------------------------------------
# merge_plan_with_params()
# ---------------------------------------------------------------------------


def test_merge_plan_with_params_records_overrides(
    tiny_model: ModelInfo, gpu_hardware: HardwareProfile
) -> None:
    execution = recommend_execution_plan(tiny_model, gpu_hardware, sequence_length=1024)

    merged = merge_plan_with_params(execution, {"rank": 16, "dropout": 0.05})
    assert merged["model_id"] == tiny_model.model_id
    assert merged["plan_kind"] == "estimate"
    assert merged["dtype"] == execution.dtype
    assert merged["device"] == execution.device
    assert merged["rank"] == 16
    assert merged["dropout"] == 0.05
    assert merged["overrides"] == []
    assert merged["estimate_total_bytes"] == execution.estimate.breakdown.total_bytes
    assert merged["hardware_profile_id"] == gpu_hardware.profile_id

    overridden = merge_plan_with_params(execution, {"batch_size": 4})
    assert overridden["batch_size"] == 4
    assert overridden["overrides"] == ["batch_size"]


def test_merge_plan_with_params_rejects_reserved_and_invalid_keys(
    tiny_model: ModelInfo, gpu_hardware: HardwareProfile
) -> None:
    execution = recommend_execution_plan(tiny_model, gpu_hardware)

    with pytest.raises(ValidationError):
        merge_plan_with_params(execution, {"model_id": "spoofed"})
    with pytest.raises(ValidationError):
        merge_plan_with_params(execution, {"overrides": ["model_id"]})
    with pytest.raises(ValidationError):
        merge_plan_with_params(execution, {"": 1})
    with pytest.raises(ValidationError):
        merge_plan_with_params(execution, ["not", "a", "mapping"])  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        merge_plan_with_params({"dtype": "bfloat16"}, {})  # type: ignore[arg-type]
