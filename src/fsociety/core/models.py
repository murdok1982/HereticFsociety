# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Shared, typed contracts across the platform.

These models are the boundary between the CLI, the HTTP API, the persistence
layer and the dashboard. They are deliberately dependency-free (Pydantic only)
so they can be imported without PyTorch.
"""

from __future__ import annotations

import platform
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

Confidence = Literal["measured", "reported", "estimated", "unknown"]
SourceKind = Literal["measured", "reported", "estimated", "declared", "unknown"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utcnow_iso() -> str:
    return utcnow().isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    """Time-ordered, human-scannable identifier."""
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}_{stamp}_{uuid.uuid4().hex[:8]}"


class StrictModel(BaseModel):
    """Base model that rejects unknown fields at trust boundaries."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Hardware
# ---------------------------------------------------------------------------


class GpuInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    index: int = 0
    name: str
    backend: str = "unknown"  # cuda | rocm | mps | xpu | mlu | musa | sdaa
    total_memory_bytes: int | None = None
    free_memory_bytes: int | None = None
    compute_capability: str | None = None
    driver_version: str | None = None
    source: SourceKind = "unknown"
    confidence: Confidence = "unknown"


class CpuInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str | None = None
    physical_cores: int | None = None
    logical_cores: int | None = None
    frequency_mhz: float | None = None
    architecture: str | None = None
    source: SourceKind = "unknown"
    confidence: Confidence = "unknown"


class TorchInfo(BaseModel):
    installed: bool = False
    version: str | None = None
    cuda_available: bool = False
    cuda_version: str | None = None
    mps_available: bool = False
    device_count: int = 0
    dtype_support: list[str] = Field(default_factory=list)
    error: str | None = None


class HardwareProfile(BaseModel):
    """Detected hardware. Every value carries provenance and confidence."""

    schema_version: int = 1
    profile_id: str = Field(default_factory=lambda: new_id("hw"))
    captured_at: str = Field(default_factory=utcnow_iso)
    hostname: str
    os: str
    os_release: str
    architecture: str
    python_version: str
    cpu: CpuInfo = Field(default_factory=CpuInfo)
    ram_total_bytes: int | None = None
    ram_available_bytes: int | None = None
    swap_total_bytes: int | None = None
    gpus: list[GpuInfo] = Field(default_factory=list)
    backends: dict[str, bool] = Field(default_factory=dict)
    torch: TorchInfo = Field(default_factory=TorchInfo)
    detection_mode: Literal["measured", "partial"] = "partial"
    warnings: list[str] = Field(default_factory=list)
    fingerprint: str = ""

    def accelerators(self) -> list[GpuInfo]:
        return [g for g in self.gpus if g.backend != "cpu"]

    def total_accelerator_memory_bytes(self) -> int:
        return sum(g.total_memory_bytes or 0 for g in self.gpus)

    def summary(self) -> str:
        gpu = (
            ", ".join(f"{g.name} ({_gib(g.total_memory_bytes)})" for g in self.gpus)
            or "no accelerator"
        )
        return (
            f"{self.cpu.model or 'CPU'} · {_gib(self.ram_total_bytes)} RAM · {gpu} · "
            f"{self.os} {self.architecture}"
        )


def _gib(value: int | None) -> str:
    if not value:
        return "unknown"
    return f"{value / (1024**3):.1f} GiB"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

ModelFormat = Literal["safetensors", "pytorch", "gguf", "mlx", "unknown"]
ModelSource = Literal["local", "huggingface_cache", "huggingface_hub", "unknown"]


class ModelFile(BaseModel):
    path: str
    size_bytes: int
    sha256: str | None = None
    format: ModelFormat = "unknown"


class ModelInfo(BaseModel):
    """Metadata extracted from a model directory or the HF cache."""

    schema_version: int = 1
    model_id: str
    source: ModelSource = "local"
    path: str | None = None
    revision: str | None = None
    commit: str | None = None
    architecture: str | None = None
    model_type: str | None = None
    parameter_count: int | None = None
    active_parameter_count: int | None = None
    hidden_size: int | None = None
    num_hidden_layers: int | None = None
    num_attention_heads: int | None = None
    num_key_value_heads: int | None = None
    head_dim: int | None = None
    intermediate_size: int | None = None
    vocab_size: int | None = None
    max_position_embeddings: int | None = None
    torch_dtype: str | None = None
    quantization: str | None = None
    size_bytes: int | None = None
    files: list[ModelFile] = Field(default_factory=list)
    formats: list[ModelFormat] = Field(default_factory=list)
    has_chat_template: bool = False
    is_multimodal: bool = False
    is_mixture_of_experts: bool = False
    license: str | None = None
    tags: list[str] = Field(default_factory=list)
    requires_remote_code: bool = False
    metadata_source: SourceKind = "declared"
    compatibility: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    inspected_at: str = Field(default_factory=utcnow_iso)

    def resolved_head_dim(self) -> int | None:
        if self.head_dim:
            return self.head_dim
        if self.hidden_size and self.num_attention_heads:
            return self.hidden_size // self.num_attention_heads
        return None


class ModelValidation(BaseModel):
    model_id: str
    path: str | None = None
    checked_at: str = Field(default_factory=utcnow_iso)
    files_checked: int = 0
    hashes_verified: int = 0
    mismatches: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    ok: bool = False
    manifest_path: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Resource estimation
# ---------------------------------------------------------------------------

Component = Literal["weights", "kv_cache", "activations", "overhead", "total"]


class MemoryBreakdown(BaseModel):
    weights_bytes: int
    kv_cache_bytes: int
    activations_bytes: int
    overhead_bytes: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_bytes(self) -> int:
        return (
            self.weights_bytes
            + self.kv_cache_bytes
            + self.activations_bytes
            + self.overhead_bytes
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "weights": self.weights_bytes,
            "kv_cache": self.kv_cache_bytes,
            "activations": self.activations_bytes,
            "overhead": self.overhead_bytes,
            "total": self.total_bytes,
        }


class ResourceEstimate(BaseModel):
    schema_version: int = 1
    kind: Literal["estimate"] = "estimate"
    model_id: str
    parameter_count: int
    dtype: str = "bfloat16"
    quantization: str = "none"
    batch_size: int = 1
    sequence_length: int = 2048
    breakdown: MemoryBreakdown
    fits: bool | None = None
    headroom_bytes: int | None = None
    recommended_dtype: str | None = None
    recommended_quantization: str | None = None
    recommended_batch_size: int | None = None
    recommended_device_map: str | int | dict[str, int | str] | None = None
    alternatives: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    confidence: Confidence = "estimated"
    hardware_profile_id: str | None = None
    created_at: str = Field(default_factory=utcnow_iso)


# ---------------------------------------------------------------------------
# Experiments, jobs, trials
# ---------------------------------------------------------------------------

ExperimentKind = Literal[
    "optimization", "evaluation", "modification", "export", "verification"
]
JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class TrialRecord(BaseModel):
    trial_id: str
    experiment_id: str
    number: int
    state: str
    params: dict[str, Any] = Field(default_factory=dict)
    values: list[float] = Field(default_factory=list)
    objective_names: list[str] = Field(default_factory=list)
    duration_seconds: float | None = None
    created_at: str = Field(default_factory=utcnow_iso)


class ExperimentRecord(BaseModel):
    experiment_id: str
    name: str
    kind: ExperimentKind
    status: JobStatus = "queued"
    model_id: str | None = None
    created_at: str = Field(default_factory=utcnow_iso)
    started_at: str | None = None
    finished_at: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    seed: int | None = None
    config_hash: str | None = None
    suite_hash: str | None = None
    hardware_fingerprint: str | None = None
    software: dict[str, str] = Field(default_factory=dict)
    error: str | None = None
    notes: str | None = None


class JobRecord(BaseModel):
    job_id: str
    kind: str
    status: JobStatus = "queued"
    experiment_id: str | None = None
    created_at: str = Field(default_factory=utcnow_iso)
    started_at: str | None = None
    finished_at: str | None = None
    progress: float = 0.0
    message: str = ""
    request: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    cancellable: bool = True
    cancel_requested: bool = False


class EventRecord(BaseModel):
    event_id: int | None = None
    created_at: str = Field(default_factory=utcnow_iso)
    level: Literal["debug", "info", "warning", "error", "audit"] = "info"
    category: str = "system"
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


class ArtifactRecord(BaseModel):
    artifact_id: str
    experiment_id: str | None = None
    kind: str
    path: str
    sha256: str
    size_bytes: int
    created_at: str = Field(default_factory=utcnow_iso)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


class BenchmarkItem(BaseModel):
    item_id: str
    prompt: str
    expected: str | None = None
    category: str = "general"
    metadata: dict[str, Any] = Field(default_factory=dict)


class BenchmarkSuiteInfo(BaseModel):
    schema_version: int = 1
    suite_id: str
    name: str
    version: str
    description: str
    grader: str
    language: str = "en"
    categories: list[str] = Field(default_factory=list)
    item_count: int
    sha256: str
    tags: list[str] = Field(default_factory=list)
    source: str = "builtin"
    provenance: str = ""
    limitations: list[str] = Field(default_factory=list)


class BenchmarkItemResult(BaseModel):
    item_id: str
    category: str
    prompt_sha256: str
    response: str | None = None
    score: float | None = None
    passed: bool | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class BenchmarkReport(BaseModel):
    schema_version: int = 1
    report_id: str = Field(default_factory=lambda: new_id("eval"))
    experiment_id: str | None = None
    model_id: str
    backend: str
    suite: BenchmarkSuiteInfo
    suite_hash: str
    config: dict[str, Any] = Field(default_factory=dict)
    config_hash: str
    seed: int
    hardware: HardwareProfile | None = None
    hardware_fingerprint: str | None = None
    software: dict[str, str] = Field(default_factory=dict)
    started_at: str = Field(default_factory=utcnow_iso)
    finished_at: str | None = None
    duration_seconds: float | None = None
    peak_memory_bytes: int | None = None
    results: list[BenchmarkItemResult] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    category_metrics: dict[str, dict[str, Any]] = Field(default_factory=dict)
    status: JobStatus = "running"
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)


class MetricDelta(BaseModel):
    metric: str
    baseline: float | None
    candidate: float | None
    delta: float | None
    relative_delta: float | None
    direction: Literal["higher_is_better", "lower_is_better", "neutral"] = (
        "higher_is_better"
    )
    regressed: bool = False


class ComparisonResult(BaseModel):
    schema_version: int = 1
    baseline_report_id: str
    candidate_report_id: str
    comparable: bool
    incomparability_reasons: list[str] = Field(default_factory=list)
    metric_deltas: list[MetricDelta] = Field(default_factory=list)
    regressed_items: list[str] = Field(default_factory=list)
    improved_items: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    verdict: Literal["improved", "regressed", "mixed", "equivalent", "incomparable"] = (
        "incomparable"
    )
    threshold: float = 0.02


# ---------------------------------------------------------------------------
# Plugins / system
# ---------------------------------------------------------------------------

PluginKind = Literal[
    "scorer", "modifier", "evaluator", "hardware_provider", "exporter", "integration"
]


class PluginInfo(BaseModel):
    name: str
    version: str
    api_version: int
    kind: PluginKind
    class_name: str
    module: str
    path: str | None = None
    description: str = ""
    author: str | None = None
    license: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    builtin: bool = False
    valid: bool = True
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SystemInfo(BaseModel):
    platform_name: str = "FSOCIETY AI"
    version: str
    plugin_api_version: int
    python_version: str = platform.python_version()
    engine_available: bool = False
    engine_version: str | None = None
    api_version: str = "v1"
    started_at: str = Field(default_factory=utcnow_iso)
    workspace: str | None = None
    source_url: str | None = None


class HealthStatus(BaseModel):
    status: Literal["ok", "degraded"] = "ok"
    checks: dict[str, str] = Field(default_factory=dict)
    version: str
    uptime_seconds: float | None = None
