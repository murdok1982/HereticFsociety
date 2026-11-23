# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Architecture- and hardware-aware configuration planning.

The planner never re-implements memory math: it composes
:mod:`fsociety.core.estimator` (weights, KV cache, activations, overhead) with
hardware budgets and turns the result into either a validated
:class:`ConstraintReport` or an :class:`ExecutionPlan`.

Every number produced here is an **estimate** (``kind="estimate"``), derived
from declared architecture metadata and analytical constants. Nothing in this
module measures memory or throughput; reports must not present it as such.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import Field, model_validator

from ..core.estimator import BYTES_PER_PARAM, EstimationRequest, estimate_memory
from ..core.hardware import primary_device
from ..core.models import HardwareProfile, ModelInfo, ResourceEstimate, StrictModel
from ..errors import ValidationError
from .space import Dimension, SearchSpace

SUPPORTED_DTYPES = ("float32", "float16", "bfloat16")
SUPPORTED_QUANTIZATION = ("none", "int8", "bnb_4bit", "gguf_q8_0", "gguf_q4_k_m")
#: Candidate context lengths explored when recommending a search space.
SEQUENCE_CANDIDATES = (512, 1024, 2048, 4096)
#: Upper bound for a recommended batch dimension; larger batches rarely help.
MAX_RECOMMENDED_BATCH = 32
#: Keys owned by :func:`merge_plan_with_params`; callers may not overwrite them.
RESERVED_CONFIG_KEYS = frozenset(
    {
        "model_id",
        "plan_kind",
        "hardware_profile_id",
        "estimate_total_bytes",
        "overrides",
    }
)


def _gib(value: int | None) -> str:
    if value is None:
        return "unknown"
    return f"{value / (1024**3):.2f} GiB"


def _require_model(model_info: Any) -> ModelInfo:
    if not isinstance(model_info, ModelInfo):
        raise ValidationError(
            f"model_info must be a ModelInfo, got {type(model_info).__name__}."
        )
    if not model_info.parameter_count or model_info.parameter_count <= 0:
        raise ValidationError(
            f"Model {model_info.model_id!r} has no parameter count.",
            hint="Inspect the model first (`fsociety models inspect <path>`) so the "
            "planner has real metadata instead of guessing.",
            details={"model_id": model_info.model_id},
        )
    return model_info


def _normalize_dtype_name(value: str) -> str:
    """Accept ``torch.bfloat16``/``BFloat16`` style names from model metadata."""
    cleaned = value.strip().lower().replace("torch.", "")
    return cleaned


def _resolve_dtype(
    requested: str | None,
    model_info: ModelInfo,
    hardware: HardwareProfile | None,
    notes: list[str],
) -> str:
    if requested is not None:
        dtype = _normalize_dtype_name(requested)
        if dtype not in BYTES_PER_PARAM:
            raise ValidationError(
                f"Unsupported dtype/quantization {requested!r}.",
                details={"supported": sorted(BYTES_PER_PARAM)},
            )
        return dtype

    if model_info.torch_dtype:
        declared = _normalize_dtype_name(model_info.torch_dtype)
        if declared in SUPPORTED_DTYPES:
            notes.append(f"Using the dtype declared by the model config ({declared}).")
            return declared
        notes.append(
            f"Model dtype {model_info.torch_dtype!r} is not usable for estimation; "
            "falling back to a runtime-supported dtype."
        )

    supported = [
        dtype
        for dtype in (hardware.torch.dtype_support if hardware else [])
        if dtype in SUPPORTED_DTYPES
    ]
    if supported:
        notes.append(
            f"Hardware reports dtype support for {supported}; using {supported[0]}."
        )
        return supported[0]
    return "bfloat16"


def _resolve_quantization(
    requested: str | None,
    model_info: ModelInfo,
    notes: list[str],
) -> str:
    if requested is not None:
        quantization = requested.strip().lower()
        if quantization != "none" and quantization not in BYTES_PER_PARAM:
            raise ValidationError(
                f"Unsupported quantization {requested!r}.",
                details={"supported": ["none", *sorted(BYTES_PER_PARAM)]},
            )
        return quantization

    declared = (model_info.quantization or "").strip().lower()
    if not declared or declared in {"none", "null", "fp16", "bf16"}:
        return "none"
    if declared in BYTES_PER_PARAM:
        notes.append(f"Using the quantization declared by the model ({declared}).")
        return declared
    notes.append(
        f"Model quantization {declared!r} cannot be modeled analytically; "
        "assuming unquantized weights (estimate may understate memory)."
    )
    return "none"


def plan(
    model_info: ModelInfo,
    hardware: HardwareProfile | None,
    *,
    batch_size: int = 1,
    sequence_length: int = 2048,
    dtype: str | None = None,
    quantization: str | None = None,
    reserve_ratio: float = 0.1,
) -> ResourceEstimate:
    """Estimate memory for one concrete configuration.

    Delegates all arithmetic to :func:`fsociety.core.estimator.estimate_memory`
    and annotates the returned estimate with the resolution decisions taken
    here (declared dtype, unsupported quantization, context clamping).
    """
    model_info = _require_model(model_info)
    if hardware is not None and not isinstance(hardware, HardwareProfile):
        raise ValidationError(
            f"hardware must be a HardwareProfile or None, got {type(hardware).__name__}."
        )
    for label, value in (("dtype", dtype), ("quantization", quantization)):
        if value is not None and not isinstance(value, str):
            raise ValidationError(
                f"{label} must be a string or None, got {type(value).__name__}."
            )
    if (
        isinstance(batch_size, bool)
        or not isinstance(batch_size, int)
        or batch_size < 1
    ):
        raise ValidationError(
            f"batch_size must be an integer >= 1, got {batch_size!r}."
        )
    if (
        isinstance(sequence_length, bool)
        or not isinstance(sequence_length, int)
        or sequence_length < 1
    ):
        raise ValidationError(
            f"sequence_length must be an integer >= 1, got {sequence_length!r}."
        )
    if (
        isinstance(reserve_ratio, bool)
        or not isinstance(reserve_ratio, (int, float))
        or not 0.0 <= float(reserve_ratio) < 1.0
    ):
        raise ValidationError(
            f"reserve_ratio must be in [0, 1), got {reserve_ratio!r}."
        )
    reserve_ratio = float(reserve_ratio)

    notes: list[str] = []
    resolved_dtype = _resolve_dtype(dtype, model_info, hardware, notes)
    resolved_quantization = _resolve_quantization(quantization, model_info, notes)

    max_positions = model_info.max_position_embeddings
    if max_positions and sequence_length > max_positions:
        notes.append(
            f"Requested sequence_length {sequence_length} exceeds the model's "
            f"max_position_embeddings ({max_positions}); the runtime will reject it."
        )

    estimate = estimate_memory(
        model_info,
        EstimationRequest(
            batch_size=batch_size,
            sequence_length=sequence_length,
            dtype=resolved_dtype,
            quantization=resolved_quantization,
            reserve_ratio=reserve_ratio,
        ),
        hardware,
    )
    if hardware is None:
        notes.append(
            "No hardware profile was supplied, so `fits` is unknown; run "
            "`fsociety hardware --json` and pass the stored profile to get a verdict."
        )
    estimate.assumptions.extend(notes)
    return estimate


class ConstraintReport(StrictModel):
    """Human-readable verdict for one :class:`ResourceEstimate`."""

    ok: bool
    fit: Literal["fits", "does_not_fit", "unknown"]
    model_id: str
    dtype: str
    quantization: str
    batch_size: int
    sequence_length: int
    total_bytes: int
    budget_bytes: int | None = None
    headroom_bytes: int | None = None
    utilization: float | None = Field(default=None, ge=0.0)
    violations: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def validate_configuration(estimate: ResourceEstimate) -> ConstraintReport:
    """Check an estimate against the available budget and explain any mismatch."""
    if not isinstance(estimate, ResourceEstimate):
        raise ValidationError(
            f"estimate must be a ResourceEstimate, got {type(estimate).__name__}."
        )

    total = estimate.breakdown.total_bytes
    budget = (
        None if estimate.headroom_bytes is None else total + estimate.headroom_bytes
    )
    utilization = None
    if budget is not None and budget > 0:
        utilization = round(total / budget, 4)

    if estimate.fits is True:
        fit: Literal["fits", "does_not_fit", "unknown"] = "fits"
    elif estimate.fits is False:
        fit = "does_not_fit"
    else:
        fit = "unknown"

    violations: list[str] = []
    recommendations: list[str] = list(estimate.alternatives)
    warnings: list[str] = [
        (
            "Memory figures are analytical estimates (kind='estimate') derived from model "
            "metadata and constant factors; they are not measurements."
        )
    ]

    if estimate.batch_size < 1:
        violations.append(f"batch_size must be >= 1, got {estimate.batch_size}.")
    if estimate.sequence_length < 1:
        violations.append(
            f"sequence_length must be >= 1, got {estimate.sequence_length}."
        )

    if fit == "does_not_fit":
        over_by = abs(estimate.headroom_bytes or 0)
        violations.append(
            f"Estimated peak memory {_gib(total)} exceeds the {_gib(budget)} budget "
            f"by {_gib(over_by)} at dtype={estimate.dtype}, "
            f"quantization={estimate.quantization}, batch={estimate.batch_size}, "
            f"sequence_length={estimate.sequence_length}."
        )
        if (
            estimate.recommended_quantization
            and estimate.recommended_quantization != estimate.quantization
        ):
            recommendations.append(
                f"Switch quantization to {estimate.recommended_quantization!r} "
                f"(from {estimate.quantization!r})."
            )
        if estimate.recommended_dtype and estimate.recommended_dtype != estimate.dtype:
            recommendations.append(
                f"Switch dtype to {estimate.recommended_dtype!r} (from {estimate.dtype!r})."
            )
        if estimate.sequence_length > 2048:
            recommendations.append(
                f"Lower sequence_length from {estimate.sequence_length} to 2048 or less; "
                "the KV cache grows linearly with context."
            )
        if estimate.batch_size > 1:
            recommendations.append(
                f"Lower batch_size from {estimate.batch_size} to 1 to remove the "
                "activation and KV duplication per request."
            )
        if estimate.quantization == "none":
            recommendations.append(
                "If a bitsandbytes runtime is available, 4-bit quantization "
                "(bnb_4bit) is the largest single memory reduction."
            )
        recommendations.append(
            "Offload part of the model with device_map='auto' or run a smaller model; "
            "no precision change can make an oversized model fit."
        )
    elif fit == "fits":
        if (
            estimate.recommended_batch_size
            and estimate.recommended_batch_size > estimate.batch_size
        ):
            recommendations.append(
                f"Estimated to fit with batch_size up to {estimate.recommended_batch_size}; "
                f"currently configured for {estimate.batch_size}."
            )
        if estimate.headroom_bytes is not None and estimate.headroom_bytes < 0:
            violations.append(
                "Internal inconsistency: a fitting estimate has negative headroom."
            )
    else:
        warnings.append(
            "Fit could not be determined: no usable memory budget was available. "
            "Run `fsociety hardware` and re-plan with the detected profile."
        )
        recommendations.append(
            "Pass a HardwareProfile so memory can be compared against a real budget."
        )

    if estimate.dtype not in BYTES_PER_PARAM:
        warnings.append(
            f"dtype {estimate.dtype!r} is not part of the estimation table."
        )
    if estimate.quantization not in {"none", *BYTES_PER_PARAM}:
        warnings.append(
            f"quantization {estimate.quantization!r} is not part of the estimation table."
        )

    return ConstraintReport(
        ok=not violations,
        fit=fit,
        model_id=estimate.model_id,
        dtype=estimate.dtype,
        quantization=estimate.quantization,
        batch_size=estimate.batch_size,
        sequence_length=estimate.sequence_length,
        total_bytes=total,
        budget_bytes=budget,
        headroom_bytes=estimate.headroom_bytes,
        utilization=utilization,
        violations=_dedupe(violations),
        recommendations=_dedupe(recommendations),
        warnings=_dedupe(warnings),
    )


class ExecutionPlan(StrictModel):
    """A ready-to-run (estimated) execution configuration plus its rationale."""

    schema_version: int = 1
    kind: Literal["estimate"] = "estimate"
    model_id: str
    dtype: str
    quantization: str
    batch_size: int = Field(ge=1)
    sequence_length: int = Field(ge=1)
    device: str
    device_map: int | str | dict[str, int | str] | None = None
    reserve_ratio: float = Field(default=0.1, ge=0.0, lt=1.0)
    estimate: ResourceEstimate
    report: ConstraintReport
    rationale: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_consistency(self) -> ExecutionPlan:
        if self.estimate.model_id != self.model_id:
            raise ValueError("ExecutionPlan.estimate belongs to a different model.")
        if self.report.model_id != self.model_id:
            raise ValueError("ExecutionPlan.report belongs to a different model.")
        return self

    def runtime_params(self) -> dict[str, Any]:
        """The subset of fields that configures an actual run."""
        return {
            "model_id": self.model_id,
            "dtype": self.dtype,
            "quantization": self.quantization,
            "batch_size": self.batch_size,
            "sequence_length": self.sequence_length,
            "device": self.device,
            "device_map": self.device_map,
        }


def _resolve_device(hardware: HardwareProfile | None) -> str:
    if hardware is None:
        return "cpu"
    return primary_device(hardware)


def _resolve_device_map(
    device: str, hardware: HardwareProfile | None
) -> int | str | dict[str, int | str] | None:
    if hardware is None or device == "cpu":
        return None
    if device in {"cuda", "rocm"}:
        accelerators = [gpu for gpu in hardware.gpus if gpu.backend in {"cuda", "rocm"}]
        return "auto" if len(accelerators) > 1 else 0
    return None


def _default_sequence_length(model_info: ModelInfo) -> int:
    if model_info.max_position_embeddings:
        return min(2048, model_info.max_position_embeddings)
    return 2048


def recommend_execution_plan(
    model_info: ModelInfo,
    hardware: HardwareProfile | None,
    *,
    sequence_length: int | None = None,
    batch_size: int | None = None,
    dtype: str | None = None,
    quantization: str | None = None,
    reserve_ratio: float = 0.1,
) -> ExecutionPlan:
    """Build the best estimated configuration that fits the detected hardware."""
    model_info = _require_model(model_info)
    target_sequence = sequence_length or _default_sequence_length(model_info)
    target_batch = batch_size or 1
    rationale: list[str] = []
    warnings: list[str] = []

    estimate = plan(
        model_info,
        hardware,
        batch_size=target_batch,
        sequence_length=target_sequence,
        dtype=dtype,
        quantization=quantization,
        reserve_ratio=reserve_ratio,
    )
    rationale.append(
        f"Baseline: dtype={estimate.dtype}, quantization={estimate.quantization}, "
        f"batch={estimate.batch_size}, sequence_length={estimate.sequence_length} "
        f"=> {_gib(estimate.breakdown.total_bytes)} estimated peak."
    )

    if estimate.fits is False and (
        estimate.recommended_dtype or estimate.recommended_quantization
    ):
        downgraded_sequence = min(estimate.sequence_length, 2048)
        downgraded = plan(
            model_info,
            hardware,
            batch_size=1,
            sequence_length=downgraded_sequence,
            dtype=estimate.recommended_dtype or estimate.dtype,
            quantization=estimate.recommended_quantization or estimate.quantization,
            reserve_ratio=reserve_ratio,
        )
        rationale.append(
            f"Downgraded to dtype={downgraded.dtype}, "
            f"quantization={downgraded.quantization}, batch=1, "
            f"sequence_length={downgraded_sequence} to search for a fitting "
            f"configuration ({_gib(downgraded.breakdown.total_bytes)})."
        )
        estimate = downgraded
    elif estimate.fits is False:
        warnings.append(
            "Even the smallest modeled precision does not fit the detected memory; "
            "the plan is not runnable on this machine."
        )
        rationale.append(
            "No precision/quantization downgrade reduced the footprint below the budget."
        )
    elif estimate.fits is None:
        warnings.append(
            "Fit is unknown: no hardware budget was available. Treat this plan as a "
            "starting point, not a validated configuration."
        )

    if estimate.batch_size > 1 and estimate.fits is False:
        reduced = plan(
            model_info,
            hardware,
            batch_size=1,
            sequence_length=estimate.sequence_length,
            dtype=estimate.dtype,
            quantization=estimate.quantization,
            reserve_ratio=reserve_ratio,
        )
        rationale.append(
            f"Reduced batch size from {estimate.batch_size} to 1 to remove per-request "
            "activation and KV duplication."
        )
        estimate = reduced

    if estimate.fits is True and estimate.recommended_batch_size:
        rationale.append(
            f"Estimator indicates batch sizes up to {estimate.recommended_batch_size} "
            f"still fit at sequence_length {estimate.sequence_length}."
        )

    device = _resolve_device(hardware)
    device_map = _resolve_device_map(device, hardware)
    if hardware is None:
        rationale.append(
            "Device defaulted to CPU because no hardware profile was supplied."
        )
    else:
        accelerator_names = (
            ", ".join(gpu.name for gpu in hardware.accelerators()) or "none"
        )
        rationale.append(
            f"Device {device!r} selected from detected accelerators ({accelerator_names}) "
            f"and torch runtime availability."
        )

    report = validate_configuration(estimate)
    warnings.extend(report.warnings)
    if report.fit == "fits" and report.utilization is not None:
        rationale.append(
            f"Estimated memory utilisation {report.utilization:.0%} of the "
            f"{_gib(report.budget_bytes)} budget "
            f"(headroom {_gib(report.headroom_bytes)})."
        )

    return ExecutionPlan(
        model_id=model_info.model_id,
        dtype=estimate.dtype,
        quantization=estimate.quantization,
        batch_size=estimate.batch_size,
        sequence_length=estimate.sequence_length,
        device=device,
        device_map=device_map,
        reserve_ratio=reserve_ratio,
        estimate=estimate,
        report=report,
        rationale=_dedupe(rationale),
        warnings=_dedupe(warnings),
    )


def recommend_search_space(
    model_info: ModelInfo,
    hardware: HardwareProfile | None,
    *,
    reserve_ratio: float = 0.1,
) -> SearchSpace:
    """Suggest a search space bounded by what the hardware can plausibly run.

    Modifier knobs (rank/alpha/dropout) are unconstrained by memory and keep
    their full preset range. Execution knobs (batch size, context length,
    dtype, quantization) are narrowed using :func:`plan` estimates so that most
    trials are runnable; every bound is an estimate, not a measurement.
    """
    model_info = _require_model(model_info)

    base = plan(
        model_info,
        hardware,
        batch_size=1,
        sequence_length=_default_sequence_length(model_info),
        reserve_ratio=reserve_ratio,
    )
    max_batch = MAX_RECOMMENDED_BATCH
    if base.fits is True and base.recommended_batch_size:
        max_batch = max(1, min(MAX_RECOMMENDED_BATCH, base.recommended_batch_size))
    elif base.fits is False:
        max_batch = 1

    feasible_sequences: list[int] = []
    for candidate in SEQUENCE_CANDIDATES:
        if (
            model_info.max_position_embeddings
            and candidate > model_info.max_position_embeddings
        ):
            continue
        if hardware is None:
            feasible_sequences.append(candidate)
            continue
        try:
            candidate_estimate = plan(
                model_info,
                hardware,
                batch_size=1,
                sequence_length=candidate,
                dtype=base.dtype,
                quantization=base.quantization,
                reserve_ratio=reserve_ratio,
            )
        except ValidationError:
            continue
        if candidate_estimate.fits is not False:
            feasible_sequences.append(candidate)
    if not feasible_sequences:
        feasible_sequences = [
            min(
                SEQUENCE_CANDIDATES[0],
                model_info.max_position_embeddings or SEQUENCE_CANDIDATES[0],
            )
        ]

    dtypes = [
        dtype
        for dtype in (hardware.torch.dtype_support if hardware else [])
        if dtype in SUPPORTED_DTYPES
    ]
    if not dtypes:
        dtypes = list(SUPPORTED_DTYPES)
    if base.dtype in SUPPORTED_DTYPES and base.dtype not in dtypes:
        dtypes.insert(0, base.dtype)
    dtypes = _dedupe(dtypes)

    quantization_choices = ["none"]
    if base.fits is False:
        quantization_choices.append("bnb_4bit")

    return SearchSpace(
        dimensions=[
            Dimension(
                name="rank",
                kind="int",
                low=4,
                high=64,
                log=True,
                description="Modifier rank.",
            ),
            Dimension(
                name="alpha",
                kind="int",
                low=4,
                high=128,
                step=4,
                description="Modifier scaling factor.",
            ),
            Dimension(
                name="dropout",
                kind="float",
                low=0.0,
                high=0.2,
                step=0.01,
                description="Modifier dropout.",
            ),
            Dimension(
                name="batch_size",
                kind="int",
                low=1,
                high=max_batch,
                description=(
                    "Estimated maximum batch that fits this hardware "
                    f"(capped at {MAX_RECOMMENDED_BATCH})."
                ),
            ),
            Dimension(
                name="sequence_length",
                kind="categorical",
                choices=sorted(feasible_sequences),
                description="Context lengths estimated to fit at batch size 1.",
            ),
            Dimension(
                name="dtype",
                kind="categorical",
                choices=dtypes,
                description="Dtypes reported as supported by the runtime.",
            ),
            Dimension(
                name="quantization",
                kind="categorical",
                choices=quantization_choices,
                description="Quantization modes worth exploring for this model/size.",
            ),
        ]
    )


def merge_plan_with_params(
    plan: ExecutionPlan, params: Mapping[str, Any]
) -> dict[str, Any]:
    """Flatten an execution plan into an experiment config, applying overrides.

    ``params`` (for example the best parameters of an optimization study) wins
    over the plan for any key it defines; the overridden keys are recorded under
    ``overrides`` so a report can explain where the config came from.
    """
    if not isinstance(plan, ExecutionPlan):
        raise ValidationError(
            f"plan must be an ExecutionPlan, got {type(plan).__name__}."
        )
    if not isinstance(params, Mapping):
        raise ValidationError(f"params must be a mapping, got {type(params).__name__}.")
    for key in params:
        if not isinstance(key, str) or not key.strip():
            raise ValidationError(
                f"params keys must be non-empty strings, got {key!r}."
            )
    reserved = sorted(set(params) & RESERVED_CONFIG_KEYS)
    if reserved:
        raise ValidationError(
            f"params must not override plan-owned keys: {reserved}.",
            details={"reserved": sorted(RESERVED_CONFIG_KEYS)},
        )

    merged = plan.runtime_params()
    overrides = sorted(key for key in params if key in merged)
    merged.update(dict(params))
    merged["model_id"] = plan.model_id
    merged["plan_kind"] = plan.kind
    merged["hardware_profile_id"] = plan.estimate.hardware_profile_id
    merged["estimate_total_bytes"] = plan.estimate.breakdown.total_bytes
    merged["overrides"] = overrides
    return merged


def describe_plan(plan: ExecutionPlan) -> str:
    """One-line, human-readable summary for CLI output and reports."""
    verdict = {
        "fits": "estimated to fit",
        "does_not_fit": "estimated NOT to fit",
        "unknown": "fit unknown",
    }[plan.report.fit]
    return (
        f"{plan.model_id}: {plan.dtype}/{plan.quantization}, batch={plan.batch_size}, "
        f"seq={plan.sequence_length}, device={plan.device} — {verdict} "
        f"({_gib(plan.estimate.breakdown.total_bytes)} estimated, kind='estimate')"
    )
