# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Resource-planning objective shared by the CLI and the HTTP API.

The objective is an explicit analytical model (bytes estimated by
``core.estimator``), not a benchmark. Trials therefore optimize a *plan*, and
the resulting numbers are always labelled as estimates.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from ..config import PlatformSettings
from ..core.estimator import EstimationRequest, estimate_memory
from ..core.models import HardwareProfile, ModelInfo
from ..errors import ValidationError

Objective = Callable[[dict[str, Any]], "tuple[list[float], dict[str, Any]]"]

OBJECTIVE_NAMES = ["estimated_total_bytes", "does_not_fit"]
OBJECTIVE_DIRECTIONS: list[Literal["minimize", "maximize"]] = ["minimize", "minimize"]


def build_resource_objective(
    info: ModelInfo,
    hardware: HardwareProfile,
    settings: PlatformSettings,
    base: dict[str, Any] | None = None,
) -> Objective:
    if not info.parameter_count:
        raise ValidationError(
            "The model has no known parameter count; resource planning cannot run.",
            details={"model_id": info.model_id},
            hint="Choose a model with safetensors weights or inspect it first.",
        )
    base = dict(base or {})

    def objective(params: dict[str, Any]) -> tuple[list[float], dict[str, Any]]:
        merged = {**base, **params}
        dtype = str(merged.get("dtype", settings.default_dtype))
        if dtype == "auto":
            dtype = "bfloat16"
        quantization = str(merged.get("quantization", settings.default_quantization))
        try:
            batch_size = int(merged.get("batch_size", settings.default_batch_size) or 1)
            sequence_length = int(
                merged.get("sequence_length", settings.default_sequence_length)
            )
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"Invalid planning parameter: {exc}") from exc
        estimate = estimate_memory(
            info,
            EstimationRequest(
                batch_size=max(1, batch_size),
                sequence_length=max(16, sequence_length),
                dtype=dtype,
                quantization=quantization,
            ),
            hardware,
        )
        if estimate.fits is None:
            fits_value = 0.5
        else:
            fits_value = 0.0 if estimate.fits else 1.0
        metrics = {
            "fits": estimate.fits,
            "total_bytes": estimate.breakdown.total_bytes,
            "headroom_bytes": estimate.headroom_bytes,
            "model_id": info.model_id,
            "kind": "estimate",
        }
        return [float(estimate.breakdown.total_bytes), fits_value], metrics

    return objective


PLAN_FIELDS = {"dtype", "quantization", "batch_size", "sequence_length"}


def validate_plan_space(dimension_names: set[str]) -> None:
    if not dimension_names & PLAN_FIELDS:
        raise ValidationError(
            "The search space must vary at least one planning parameter.",
            details={"allowed": sorted(PLAN_FIELDS)},
            hint="Add a dimension named dtype, quantization, batch_size or sequence_length.",
        )
