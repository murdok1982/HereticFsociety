# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Optimization: declarative search spaces, seeded Optuna studies, planning.

Public surface::

    from fsociety.optimizer import Optimizer, OptimizationRequest, SearchSpace

Importing this package is cheap: Optuna is imported lazily when a study starts,
and nothing here imports torch/transformers.
"""

from __future__ import annotations

from .engine import (
    CancelCallback,
    Direction,
    Objective,
    OptimizationError,
    OptimizationRequest,
    OptimizationResult,
    Optimizer,
    ProgressCallback,
    SamplerName,
    TrialOutcome,
)
from .planner import (
    ConstraintReport,
    ExecutionPlan,
    describe_plan,
    merge_plan_with_params,
    plan,
    recommend_execution_plan,
    recommend_search_space,
    validate_configuration,
)
from .space import (
    Dimension,
    DimensionKind,
    SearchSpace,
    available_presets,
    default_space,
)

__all__ = [
    "CancelCallback",
    "ConstraintReport",
    "Dimension",
    "DimensionKind",
    "Direction",
    "ExecutionPlan",
    "Objective",
    "OptimizationError",
    "OptimizationRequest",
    "OptimizationResult",
    "Optimizer",
    "ProgressCallback",
    "SamplerName",
    "SearchSpace",
    "TrialOutcome",
    "available_presets",
    "default_space",
    "describe_plan",
    "merge_plan_with_params",
    "plan",
    "recommend_execution_plan",
    "recommend_search_space",
    "validate_configuration",
]
