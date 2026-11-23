# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Analytical memory estimation.

These are *estimates*, never measurements. The estimator exists to reject
configurations that cannot fit before a multi-gigabyte model is downloaded or
loaded, and to propose a workable alternative.

Assumptions are returned with every estimate and rendered in reports/UI.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import ValidationError
from .models import HardwareProfile, MemoryBreakdown, ModelInfo, ResourceEstimate

GIB = 1024**3
MIB = 1024**2

#: Empirical bytes-per-parameter for the supported precision/quantization modes.
BYTES_PER_PARAM: dict[str, float] = {
    "float32": 4.0,
    "float16": 2.0,
    "bfloat16": 2.0,
    "int8": 1.05,  # weights + per-channel scales/zero points
    "bnb_4bit": 0.55,  # NF4 + absmax scales + double quantization
    "gguf_q8_0": 1.05,
    "gguf_q4_k_m": 0.60,
}

#: Bytes per KV cache element. KV cache is not affected by weight quantization
#: unless the runtime explicitly supports quantized caches.
KV_BYTES: dict[str, float] = {
    "float32": 4.0,
    "float16": 2.0,
    "bfloat16": 2.0,
    "int8": 1.0,
}

CUDA_CONTEXT_BYTES = 600 * MIB
CPU_RUNTIME_BYTES = 300 * MIB
FRAGMENTATION_RATIO = 0.08


@dataclass
class EstimationRequest:
    batch_size: int = 1
    sequence_length: int = 2048
    dtype: str = "bfloat16"
    quantization: str = "none"
    kv_cache_dtype: str = "bfloat16"
    activation_factor: float = 2.0
    reserve_ratio: float = 0.10
    multi_gpu: bool = False


def _normalize_dtype(dtype: str) -> str:
    dtype = dtype.lower()
    if dtype not in BYTES_PER_PARAM:
        raise ValidationError(
            f"Unsupported dtype/quantization for estimation: {dtype!r}",
            details={"supported": sorted(BYTES_PER_PARAM)},
        )
    return dtype


def _weights_bytes(params: int, dtype: str) -> int:
    # +2% for embeddings/buffers/lm-head padding effects.
    return int(params * BYTES_PER_PARAM[dtype] * 1.02)


def _kv_cache_bytes(
    info: ModelInfo,
    batch: int,
    seq: int,
    kv_dtype: str,
) -> tuple[int, str]:
    layers = info.num_hidden_layers
    kv_heads = info.num_key_value_heads or info.num_attention_heads
    head_dim = info.resolved_head_dim()
    if not layers or not kv_heads or not head_dim:
        # Fall back to a conservative assumption for 7B-class models.
        return int(2 * 32 * 8 * 128 * seq * batch * KV_BYTES.get(kv_dtype, 2.0)), (
            "KV cache assumed 32 layers × 8 KV heads × 128 head_dim "
            "(architecture metadata incomplete)."
        )
    per_element = KV_BYTES.get(kv_dtype, 2.0)
    total = int(2 * layers * kv_heads * head_dim * seq * batch * per_element)
    return total, (
        f"KV cache = 2 (K,V) × {layers} layers × {kv_heads} KV heads × "
        f"{head_dim} head_dim × {seq} tokens × {batch} batch × {per_element:g} B."
    )


def _activation_bytes(
    info: ModelInfo,
    batch: int,
    seq: int,
    dtype: str,
    factor: float,
) -> tuple[int, str]:
    hidden = info.hidden_size or 4096
    intermediate = info.intermediate_size or hidden * 4
    element = BYTES_PER_PARAM.get(dtype, 2.0)
    # Two layers in flight, QKV + MLP buffers, plus attention scores.
    per_layer = hidden * 4 + intermediate * 2 + seq * 2
    total = int(batch * seq * per_layer * element * factor)
    return total, (
        f"Activations ≈ batch {batch} × seq {seq} × (4·hidden + 2·intermediate + 2·seq) "
        f"× {element:g} B × factor {factor:g}; hidden/intermediate from config."
    )


def _budget_bytes(
    hardware: HardwareProfile | None, reserve_ratio: float
) -> tuple[int | None, str]:
    if hardware is None:
        return None, "No hardware profile supplied; fit cannot be determined."
    accelerators = [
        g.total_memory_bytes or 0 for g in hardware.gpus if g.backend != "cpu"
    ]
    if accelerators and max(accelerators) > 0:
        usable = int(max(accelerators) * (1 - reserve_ratio))
        return (
            usable,
            f"Single-accelerator budget = {max(accelerators) / GIB:.1f} GiB × (1 − {reserve_ratio:.0%}).",
        )
    if hardware.ram_total_bytes:
        # Do not assume the whole machine is free; use current availability when known.
        available = hardware.ram_available_bytes or hardware.ram_total_bytes
        usable = int(available * (1 - reserve_ratio))
        return usable, (
            f"CPU/RAM budget = {available / GIB:.1f} GiB currently available × "
            f"(1 − {reserve_ratio:.0%}); other processes may compete for memory."
        )
    return None, "Hardware memory could not be determined."


def estimate_memory(
    info: ModelInfo,
    request: EstimationRequest | None = None,
    hardware: HardwareProfile | None = None,
) -> ResourceEstimate:
    estimate = _compute_estimate(info, request, hardware)
    _recommend(estimate, info, request or EstimationRequest(), hardware)
    return estimate


def _compute_estimate(
    info: ModelInfo,
    request: EstimationRequest | None = None,
    hardware: HardwareProfile | None = None,
) -> ResourceEstimate:
    request = request or EstimationRequest()
    if not info.parameter_count or info.parameter_count <= 0:
        raise ValidationError(
            f"Model {info.model_id!r} has no parameter count; run `fsociety models inspect` first.",
            details={"model_id": info.model_id},
        )
    batch = max(1, int(request.batch_size))
    seq = max(1, int(request.sequence_length))

    effective_dtype = (
        request.quantization if request.quantization != "none" else request.dtype
    )
    effective_dtype = _normalize_dtype(effective_dtype)

    weights = _weights_bytes(info.parameter_count, effective_dtype)
    kv, kv_note = _kv_cache_bytes(info, batch, seq, request.kv_cache_dtype)
    activations, act_note = _activation_bytes(
        info, batch, seq, effective_dtype, request.activation_factor
    )
    accelerators = len(
        [g for g in (hardware.gpus if hardware else []) if g.backend != "cpu"]
    )
    context = (
        CUDA_CONTEXT_BYTES * max(1, accelerators) if accelerators else CPU_RUNTIME_BYTES
    )
    fragmentation = int((weights + kv + activations) * FRAGMENTATION_RATIO)
    breakdown = MemoryBreakdown(
        weights_bytes=weights,
        kv_cache_bytes=kv,
        activations_bytes=activations,
        overhead_bytes=context + fragmentation,
    )

    budget, budget_note = _budget_bytes(hardware, request.reserve_ratio)
    fits = None if budget is None else breakdown.total_bytes <= budget
    headroom = None if budget is None else budget - breakdown.total_bytes

    assumptions = [
        f"Weights use {BYTES_PER_PARAM[effective_dtype]:g} bytes/param for {effective_dtype}.",
        kv_note,
        act_note,
        f"Runtime overhead = {context / GIB:.2f} GiB context + "
        f"{FRAGMENTATION_RATIO:.0%} fragmentation.",
        budget_note,
        "Estimates exclude tokenizer, dataset buffers and the training-free "
        "modification workspace (residuals/logits) used by the Heretic engine.",
    ]

    estimate = ResourceEstimate(
        model_id=info.model_id,
        parameter_count=info.parameter_count,
        dtype=request.dtype,
        quantization=request.quantization,
        batch_size=batch,
        sequence_length=seq,
        breakdown=breakdown,
        fits=fits,
        headroom_bytes=headroom,
        confidence="estimated",
        hardware_profile_id=hardware.profile_id if hardware else None,
        assumptions=assumptions,
    )
    return estimate


def _candidate_modes(
    request: EstimationRequest, hardware: HardwareProfile | None
) -> list[tuple[str, str]]:
    """Ordered fallbacks, most-preferred first. Undervalues precision, not quality claims."""
    dtype_support = set(hardware.torch.dtype_support) if hardware else set()
    dtypes = [request.dtype, "bfloat16", "float16", "float32"]
    seen: set[tuple[str, str]] = set()
    modes: list[tuple[str, str]] = []
    for dtype in dtypes:
        if dtype not in BYTES_PER_PARAM:
            continue
        key = (dtype, "none")
        if key not in seen:
            seen.add(key)
            modes.append(key)
    # Quantization only makes sense when bitsandbytes-class runtime is available;
    # it is reported as an option even without torch so users can plan.
    for quant in ("bnb_4bit", "int8"):
        key = ("auto", quant)
        if key not in seen:
            seen.add(key)
            modes.append(key)
    if hardware is not None and dtype_support:
        modes.sort(key=lambda m: 0 if (m[0] == "auto" or m[0] in dtype_support) else 1)
    return modes


def _recommend(
    estimate: ResourceEstimate,
    info: ModelInfo,
    request: EstimationRequest,
    hardware: HardwareProfile | None,
) -> None:
    if estimate.fits is False:
        budget = estimate.breakdown.total_bytes + (estimate.headroom_bytes or 0)
        for dtype, quant in _candidate_modes(request, hardware):
            if dtype == "auto":
                dtype = request.dtype if request.dtype != "auto" else "bfloat16"
            try:
                candidate = _compute_estimate(
                    info,
                    EstimationRequest(
                        batch_size=1,
                        sequence_length=min(request.sequence_length, 2048),
                        dtype=dtype,
                        quantization=quant,
                        kv_cache_dtype=request.kv_cache_dtype,
                    ),
                    hardware,
                )
            except ValidationError:
                continue
            if candidate.fits:
                estimate.recommended_dtype = candidate.dtype
                estimate.recommended_quantization = candidate.quantization
                estimate.recommended_batch_size = 1
                estimate.recommended_device_map = (
                    "auto" if hardware is not None and len(hardware.gpus) > 1 else 0
                )
                estimate.alternatives.append(
                    f"Use dtype={candidate.dtype}, quantization={candidate.quantization}, "
                    f"batch=1, sequence_length={min(request.sequence_length, 2048)} "
                    f"(≈{candidate.breakdown.total_bytes / GIB:.1f} GiB vs "
                    f"{budget / GIB:.1f} GiB available)."
                )
                break
        if not estimate.alternatives:
            estimate.alternatives.append(
                "Even the smallest supported mode does not fit. Reduce sequence length, "
                "use a smaller model, increase offload/CPU memory, or add accelerator memory."
            )
        if request.sequence_length > 2048:
            estimate.alternatives.append(
                "Reducing context length is the cheapest lever: KV cache scales linearly."
            )
        return

    # It fits: recommend the largest batch that still fits, up to 32.
    if hardware is not None and estimate.fits:
        best_batch = 1
        for batch in (2, 4, 8, 12, 16, 24, 32):
            trial = _compute_estimate(
                info,
                EstimationRequest(
                    batch_size=batch,
                    sequence_length=request.sequence_length,
                    dtype=request.dtype,
                    quantization=request.quantization,
                    kv_cache_dtype=request.kv_cache_dtype,
                ),
                hardware,
            )
            if trial.fits:
                best_batch = batch
            else:
                break
        estimate.recommended_batch_size = best_batch
        estimate.recommended_dtype = request.dtype
        estimate.recommended_quantization = request.quantization
        estimate.recommended_device_map = "auto" if len(hardware.gpus) > 1 else 0
        estimate.alternatives.append(
            f"Estimated to fit with batch size up to {best_batch} at "
            f"sequence length {request.sequence_length}."
        )
    else:
        estimate.recommended_dtype = request.dtype
        estimate.recommended_quantization = request.quantization
        estimate.recommended_batch_size = request.batch_size
        estimate.alternatives.append(
            "No hardware profile supplied; fit is unknown. Run `fsociety hardware` "
            "and pass the profile to obtain a recommendation."
        )


def estimate_from_parameter_count(
    model_id: str,
    parameter_count: int,
    *,
    hidden_size: int | None = None,
    num_hidden_layers: int | None = None,
    num_attention_heads: int | None = None,
    num_key_value_heads: int | None = None,
    request: EstimationRequest | None = None,
    hardware: HardwareProfile | None = None,
) -> ResourceEstimate:
    """Convenience wrapper for quick planning when only a parameter count is known."""
    info = ModelInfo(
        model_id=model_id,
        parameter_count=parameter_count,
        hidden_size=hidden_size,
        num_hidden_layers=num_hidden_layers,
        num_attention_heads=num_attention_heads,
        num_key_value_heads=num_key_value_heads,
        metadata_source="declared",
    )
    return estimate_memory(info, request, hardware)
