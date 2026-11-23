# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""HTTP request/response schemas.

These models are strict (``extra="forbid"``) at the trust boundary and are the
only place where raw HTTP input becomes domain objects.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScanRequest(ApiModel):
    roots: list[str] = Field(default_factory=list, max_length=32)
    include_hf_cache: bool = True

    @field_validator("roots")
    @classmethod
    def _no_control_chars(cls, value: list[str]) -> list[str]:
        for root in value:
            if any(ord(ch) < 32 for ch in root) or len(root) > 1024:
                raise ValueError("Invalid scan root.")
        return value


class VerifyRequest(ApiModel):
    reference: str = Field(min_length=1, max_length=1024)
    manifest_path: str | None = Field(default=None, max_length=1024)


class DimensionSpec(ApiModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    kind: Literal["float", "int", "categorical"]
    low: float | None = None
    high: float | None = None
    log: bool = False
    step: float | None = None
    choices: list[str] = Field(default_factory=list, max_length=64)


class SearchSpaceSpec(ApiModel):
    dimensions: list[DimensionSpec] = Field(min_length=1, max_length=24)


class OptimizationJobRequest(ApiModel):
    name: str = Field(default="resource-plan", min_length=1, max_length=120)
    model_id: str = Field(min_length=1, max_length=1024)
    space: SearchSpaceSpec
    base: dict[str, Any] = Field(default_factory=dict)
    n_trials: int = Field(default=20, ge=1, le=500)
    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    sampler: Literal["tpe", "random"] = "tpe"
    timeout_seconds: int | None = Field(default=None, ge=1, le=86_400)


class BackendSpec(ApiModel):
    kind: Literal["transformers", "llama_cpp"]
    model_id: str | None = Field(default=None, max_length=1024)
    path: str | None = Field(default=None, max_length=1024)
    device: str | None = Field(default=None, max_length=32)
    dtype: str | None = Field(default=None, max_length=32)
    revision: str | None = Field(default=None, max_length=128)
    n_ctx: int | None = Field(default=None, ge=128, le=1_048_576)
    n_gpu_layers: int | None = Field(default=None, ge=-1, le=10_000)


class EvaluationJobRequest(ApiModel):
    suite_id: str = Field(min_length=1, max_length=128)
    backend: BackendSpec
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)
    max_new_tokens: int | None = Field(default=None, ge=1, le=8192)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    limit: int | None = Field(default=None, ge=1, le=5000)
    batch_size: int | None = Field(default=None, ge=1, le=512)
    system_prompt: str | None = Field(default=None, max_length=8192)


class CompareRequest(ApiModel):
    baseline_report_id: str = Field(min_length=1, max_length=128)
    candidate_report_id: str = Field(min_length=1, max_length=128)
    threshold: float = Field(default=0.02, ge=0.0, le=1.0)


class ExportBundleRequest(ApiModel):
    files: list[str] = Field(min_length=1, max_length=200)
    destination: str = Field(min_length=1, max_length=512)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PluginRunRequest(ApiModel):
    payload: dict[str, Any] = Field(default_factory=dict)
    method: str = Field(default="score", max_length=64)
    allow_in_process: bool = False
