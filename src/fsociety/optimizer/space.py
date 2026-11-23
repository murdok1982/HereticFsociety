# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Declarative, validated hyper-parameter search spaces.

A :class:`SearchSpace` is a plain data contract: it describes *what* may vary,
not how it is sampled. Sampling is delegated to Optuna's trial API (the real
search) or to a seeded :class:`random.Random` for offline planning/tests.
Keeping the space declarative means it can be serialized into an experiment
record, replayed deterministically, and validated at the server boundary
before a single trial is executed.

Nothing here imports Optuna: the trial object is passed in by the caller, so
importing ``fsociety.optimizer`` never pulls in torch, transformers or optuna.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from typing import Any, Literal

from pydantic import Field, model_validator

from ..core.models import StrictModel
from ..errors import ValidationError

DimensionKind = Literal["float", "int", "categorical"]
JSONScalar = str | int | float | bool

#: Guards against pathological payloads arriving from the API.
MAX_DIMENSIONS = 64
MAX_CATEGORICAL_CHOICES = 10_000
MAX_NAME_LENGTH = 128

_RANGE_TOLERANCE = 1e-9
_STEP_TOLERANCE = 1e-6


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_integral(value: Any) -> bool:
    return _is_number(value) and float(value).is_integer()


def _quantize(value: float, low: float, step: float | None) -> float:
    """Snap ``value`` onto the ``low + k * step`` lattice, without FP noise."""
    if not step:
        return float(value)
    k = round((value - low) / step)
    return round(low + k * step, 10)


def _check_range(kind: str, low: float | None, high: float | None) -> list[str]:
    problems: list[str] = []
    if low is None or high is None:
        problems.append(f"{kind} dimensions require both 'low' and 'high'.")
        return problems
    if not _is_number(low) or not _is_number(high):
        problems.append(f"{kind} bounds must be numbers, got {low!r} and {high!r}.")
        return problems
    if not (math.isfinite(low) and math.isfinite(high)):
        problems.append(f"{kind} bounds must be finite, got {low!r} and {high!r}.")
        return problems
    if high < low:
        problems.append(
            f"'high' ({high!r}) must be greater than or equal to 'low' ({low!r})."
        )
    elif kind == "float" and high == low:
        problems.append(
            "float dimensions need 'high' > 'low'; use a categorical for constants."
        )
    return problems


class Dimension(StrictModel):
    """One axis of the search space.

    ``float``       continuous range, optionally ``log``-uniform or ``step``-quantized
    ``int``         integer range, optionally log-uniform
    ``categorical`` explicit finite set of JSON scalars
    """

    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    kind: DimensionKind
    low: float | None = None
    high: float | None = None
    log: bool = False
    step: float | None = None
    choices: list[JSONScalar] = Field(default_factory=list)
    description: str | None = None

    @model_validator(mode="after")
    def _validate_dimension(self) -> Dimension:
        if self.name != self.name.strip():
            raise ValueError(
                f"Dimension name {self.name!r} must not have surrounding whitespace."
            )
        if "\x00" in self.name:
            raise ValueError("Dimension name must not contain NUL bytes.")

        if self.kind == "categorical":
            return self._validate_categorical()

        if self.choices:
            raise ValueError(
                f"'{self.name}': 'choices' is only valid for categorical dimensions."
            )
        if self.step is not None:
            if not _is_number(self.step) or not math.isfinite(float(self.step)):
                raise ValueError(f"'{self.name}': 'step' must be a finite number.")
            if self.step <= 0:
                raise ValueError(
                    f"'{self.name}': 'step' must be > 0, got {self.step!r}."
                )

        problems = _check_range(self.kind, self.low, self.high)
        if problems:
            raise ValueError(" ".join(problems))

        assert (
            self.low is not None and self.high is not None
        )  # narrowed by _check_range
        if self.kind == "int":
            if not _is_integral(self.low) or not _is_integral(self.high):
                raise ValueError(
                    f"'{self.name}': int bounds must be whole numbers, "
                    f"got {self.low!r} and {self.high!r}."
                )
            if self.step is not None and not _is_integral(self.step):
                raise ValueError(f"'{self.name}': int 'step' must be a whole number.")
            if self.step is not None and self.step < 1:
                raise ValueError(f"'{self.name}': int 'step' must be >= 1.")
            if self.log and self.low < 1:
                raise ValueError(
                    f"'{self.name}': log-scaled int dimensions require 'low' >= 1."
                )
            if self.log and self.step is not None and self.step != 1:
                raise ValueError(
                    f"'{self.name}': log-scaled int dimensions cannot use a step other than 1."
                )
        else:  # float
            if self.log and self.low <= 0:
                raise ValueError(
                    f"'{self.name}': log-scaled float dimensions require 'low' > 0, "
                    f"got {self.low!r}."
                )
            if self.log and self.step is not None:
                raise ValueError(
                    f"'{self.name}': log-scaled float dimensions cannot be quantized with 'step'."
                )
            if self.step is not None and self.step > (self.high - self.low):
                raise ValueError(
                    f"'{self.name}': 'step' ({self.step!r}) exceeds the range "
                    f"({self.high - self.low:g})."
                )
        return self

    def _validate_categorical(self) -> Dimension:
        if self.low is not None or self.high is not None:
            raise ValueError(
                f"'{self.name}': categorical dimensions must not define 'low'/'high'."
            )
        if self.step is not None:
            raise ValueError(
                f"'{self.name}': categorical dimensions must not define 'step'."
            )
        if self.log:
            raise ValueError(
                f"'{self.name}': categorical dimensions cannot be log-scaled."
            )
        if not self.choices:
            raise ValueError(
                f"'{self.name}': categorical dimensions need at least one choice."
            )
        if len(self.choices) > MAX_CATEGORICAL_CHOICES:
            raise ValueError(
                f"'{self.name}': at most {MAX_CATEGORICAL_CHOICES} choices are supported."
            )
        seen: set[tuple[str, Any]] = set()
        has_boolean = False
        has_numeric = False
        for choice in self.choices:
            if not isinstance(choice, (str, int, float, bool)):
                # Pydantic converts ValueError into a ValidationError; TypeError would escape.
                raise ValueError(  # noqa: TRY004
                    f"'{self.name}': choices must be str/int/float/bool, got {type(choice).__name__}."
                )
            if isinstance(choice, float) and not math.isfinite(choice):
                raise ValueError(f"'{self.name}': numeric choices must be finite.")
            if isinstance(choice, bool):
                has_boolean = True
                key: tuple[str, Any] = ("bool", choice)
            elif isinstance(choice, (int, float)):
                has_numeric = True
                # 1 and 1.0 compare equal in Optuna; treat them as the same choice.
                key = ("number", float(choice))
            else:
                key = ("text", choice)
            if key in seen:
                raise ValueError(f"'{self.name}': duplicate choice {choice!r}.")
            seen.add(key)
        if has_boolean and has_numeric:
            raise ValueError(
                f"'{self.name}': do not mix boolean and numeric choices "
                "(True == 1, which makes sampling ambiguous)."
            )
        return self

    # ---- properties --------------------------------------------------------
    @property
    def n_values(self) -> int | None:
        """Number of distinct points, or ``None`` when the axis is continuous."""
        if self.kind == "categorical":
            return len(self.choices)
        assert self.low is not None and self.high is not None
        if self.kind == "int":
            step = int(self.step or 1)
            return int(self.high - self.low) // step + 1
        if self.step:
            return round((self.high - self.low) / self.step) + 1
        return None

    # ---- sampling ----------------------------------------------------------
    def suggest(self, trial: Any) -> Any:
        """Sample this dimension with the Optuna trial API."""
        if self.kind == "categorical":
            return trial.suggest_categorical(self.name, list(self.choices))
        assert self.low is not None and self.high is not None
        if self.kind == "int":
            return int(
                trial.suggest_int(
                    self.name,
                    int(self.low),
                    int(self.high),
                    step=int(self.step or 1),
                    log=self.log,
                )
            )
        return float(
            trial.suggest_float(
                self.name,
                float(self.low),
                float(self.high),
                step=self.step,
                log=self.log,
            )
        )

    def sample(self, rng: random.Random) -> Any:
        """Draw from this dimension without Optuna (seedable, offline)."""
        if self.kind == "categorical":
            return rng.choice(self.choices)
        assert self.low is not None and self.high is not None
        low, high = float(self.low), float(self.high)
        if self.kind == "int":
            if self.log:
                exponent = rng.uniform(math.log(low), math.log(high))
                return min(int(high), max(int(low), round(math.exp(exponent))))
            step = int(self.step or 1)
            slots = int(high - low) // step + 1
            return int(low) + rng.randrange(slots) * step
        if self.log:
            return math.exp(rng.uniform(math.log(low), math.log(high)))
        if self.step:
            slots = round((high - low) / self.step) + 1
            return _quantize(low + rng.randrange(slots) * self.step, low, self.step)
        return rng.uniform(low, high)

    # ---- validation / serialization ----------------------------------------
    def problems_for(self, value: Any) -> list[str]:
        """Return human-readable problems with ``value`` (empty list == valid)."""
        if self.kind == "categorical":
            if not any(
                candidate == value
                and isinstance(candidate, bool) == isinstance(value, bool)
                for candidate in self.choices
            ):
                return [
                    f"'{self.name}' must be one of {self.choices!r}, got {value!r}."
                ]
            return []

        assert self.low is not None and self.high is not None
        low, high = float(self.low), float(self.high)
        if isinstance(value, bool) or not _is_number(value):
            return [f"'{self.name}' must be a number, got {value!r}."]
        number = float(value)
        if not math.isfinite(number):
            return [f"'{self.name}' must be finite, got {value!r}."]
        if self.kind == "int" and not number.is_integer():
            return [f"'{self.name}' must be a whole number, got {value!r}."]
        if number < low - _RANGE_TOLERANCE or number > high + _RANGE_TOLERANCE:
            return [
                f"'{self.name}' must be within [{self.low:g}, {self.high:g}], got {value!r}."
            ]
        if self.log and number <= 0:
            return [f"'{self.name}' is log-scaled and must be > 0, got {value!r}."]
        step = self.step or (1 if self.kind == "int" else None)
        if step:
            offset = (number - low) / step
            if abs(offset - round(offset)) > _STEP_TOLERANCE:
                return [
                    f"'{self.name}' must be a multiple of step {step:g} from {low:g}, got {value!r}."
                ]
        return []

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)

    def describe(self) -> str:
        if self.kind == "categorical":
            return f"{self.name}: one of {self.choices!r}"
        assert self.low is not None and self.high is not None
        suffix = ""
        if self.log:
            suffix += " (log)"
        if self.step:
            suffix += f" (step {self.step:g})"
        return f"{self.name}: {self.kind} in [{self.low:g}, {self.high:g}]{suffix}"


class SearchSpace(StrictModel):
    """An ordered, uniquely-named collection of :class:`Dimension` objects."""

    schema_version: int = 1
    dimensions: list[Dimension] = Field(min_length=1, max_length=MAX_DIMENSIONS)

    @model_validator(mode="after")
    def _validate_space(self) -> SearchSpace:
        seen: set[str] = set()
        duplicates: list[str] = []
        for dimension in self.dimensions:
            if dimension.name in seen:
                duplicates.append(dimension.name)
            seen.add(dimension.name)
        if duplicates:
            raise ValueError(f"Duplicate dimension names: {sorted(set(duplicates))}.")
        return self

    # ---- introspection -----------------------------------------------------
    def names(self) -> list[str]:
        return [dimension.name for dimension in self.dimensions]

    def dimension(self, name: str) -> Dimension:
        for dimension in self.dimensions:
            if dimension.name == name:
                return dimension
        raise ValidationError(
            f"Unknown dimension {name!r}.",
            details={"available": self.names()},
        )

    def is_finite(self) -> bool:
        return all(dimension.n_values is not None for dimension in self.dimensions)

    def grid_size(self) -> int | None:
        """Total number of distinct configurations.

        Returns ``None`` when any axis is continuous (uncountably many points).
        """
        total = 1
        for dimension in self.dimensions:
            count = dimension.n_values
            if count is None:
                return None
            total *= count
        return total

    def describe(self) -> str:
        return "; ".join(dimension.describe() for dimension in self.dimensions)

    # ---- sampling ----------------------------------------------------------
    def suggest(self, trial: Any) -> dict[str, Any]:
        """Fill the whole space through the Optuna trial API."""
        return {
            dimension.name: dimension.suggest(trial) for dimension in self.dimensions
        }

    def sample(self, seed: int | None = None) -> dict[str, Any]:
        """Deterministic sample for a given ``seed`` (same seed => same result)."""
        rng = random.Random(seed)
        params = {
            dimension.name: dimension.sample(rng) for dimension in self.dimensions
        }
        problems = self.validate_params(params)
        if (
            problems
        ):  # Defensive: a sampler bug must never feed an invalid config downstream.
            raise ValidationError(
                "Sampler produced an invalid configuration.",
                details={"problems": problems, "params": params},
            )
        return params

    # ---- validation --------------------------------------------------------
    def validate_params(self, params: Any) -> list[str]:
        """Validate a parameter mapping against the whole space.

        Returns an ordered list of human-readable problems; empty means valid.
        """
        if not isinstance(params, dict):
            return [f"Params must be a mapping, got {type(params).__name__}."]
        problems: list[str] = []
        known = set(self.names())
        for key in params:
            if key not in known:
                problems.append(
                    f"Unknown parameter {key!r}: not part of the search space."
                )
        for dimension in self.dimensions:
            if dimension.name not in params:
                problems.append(f"Missing parameter {dimension.name!r}.")
                continue
            problems.extend(dimension.problems_for(params[dimension.name]))
        return problems

    # ---- serialization -----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "dimensions": [dimension.to_dict() for dimension in self.dimensions],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SearchSpace:
        if not isinstance(data, dict):
            raise ValidationError(
                f"Search space must be an object, got {type(data).__name__}."
            )
        unknown = set(data) - {"schema_version", "dimensions"}
        if unknown:
            raise ValidationError(f"Unknown search-space keys: {sorted(unknown)}.")
        dimensions = data.get("dimensions")
        if not isinstance(dimensions, list):
            raise ValidationError("'dimensions' must be a list of dimension objects.")
        try:
            return cls.model_validate(data)
        except ValueError as exc:  # pydantic ValidationError subclasses ValueError
            raise ValidationError(
                f"Invalid search space: {exc}",
                details={"schema_version": data.get("schema_version")},
            ) from exc

    # ---- presets -----------------------------------------------------------
    @classmethod
    def preset(cls, name: str) -> SearchSpace:
        """Return a built-in space (see :func:`available_presets`)."""
        key = name.strip().lower()
        key = _PRESET_ALIASES.get(key, key)
        factory = _PRESETS.get(key)
        if factory is None:
            raise ValidationError(
                f"Unknown search-space preset {name!r}.",
                details={"available": available_presets()},
                hint="Pass explicit dimensions instead, or use one of the listed presets.",
            )
        return factory()


def _modifier_space() -> SearchSpace:
    """Modifier (LoRA/abliteration-style) numeric knobs.

    Ranges are deliberately broad: the optimizer narrows them for a concrete
    model via :func:`fsociety.optimizer.planner.recommend_search_space`.
    """
    return SearchSpace(
        dimensions=[
            Dimension(
                name="rank",
                kind="int",
                low=4,
                high=64,
                log=True,
                description="Modifier rank; larger values have more capacity and cost.",
            ),
            Dimension(
                name="alpha",
                kind="int",
                low=4,
                high=128,
                step=4,
                description="Scaling factor; conventionally 1x-2x the rank.",
            ),
            Dimension(
                name="dropout",
                kind="float",
                low=0.0,
                high=0.2,
                step=0.01,
                description="Regularization applied to modifier activations.",
            ),
        ]
    )


def _runtime_space() -> SearchSpace:
    """Execution knobs that trade throughput against memory."""
    return SearchSpace(
        dimensions=[
            Dimension(name="batch_size", kind="int", low=1, high=32, log=True),
            Dimension(
                name="sequence_length",
                kind="categorical",
                choices=[512, 1024, 2048, 4096],
            ),
            Dimension(
                name="dtype",
                kind="categorical",
                choices=["bfloat16", "float16", "float32"],
            ),
        ]
    )


_PRESETS: dict[str, Callable[[], SearchSpace]] = {
    "modifier": _modifier_space,
    "runtime": _runtime_space,
}
_PRESET_ALIASES = {
    "lora": "modifier",
    "lora_rank": "modifier",
    "heretic": "modifier",
}


def available_presets() -> list[str]:
    """Names accepted by :meth:`SearchSpace.preset` (aliases included)."""
    return sorted({*_PRESETS, *_PRESET_ALIASES})


def default_space() -> SearchSpace:
    """The modifier space used when no space is supplied."""
    return SearchSpace.preset("modifier")
