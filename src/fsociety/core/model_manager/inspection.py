# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Torch-free inspection of model directories.

Everything in this module reads bytes from disk: JSON metadata, safetensors
headers and GGUF magic numbers. Nothing imports torch, transformers or
huggingface_hub, which is what allows ``fsociety models inspect`` to run on a
machine that cannot load models at all.

Integrity rules:

* A malformed artifact never aborts a scan; it produces a warning on the
  resulting :class:`~fsociety.core.models.ModelInfo`.
* Direct header parsing (:func:`read_safetensors_header`) raises
  :class:`~fsociety.errors.ModelIntegrityError` so callers that ask for one
  specific file get a typed failure instead of a silent zero.
* Parameter counts are only reported as ``measured`` when they were summed from
  real safetensors headers. Config-derived numbers are marked ``estimated`` and
  carry a warning, because a config alone cannot describe embeddings, biases or
  architecture-specific tensors exactly.
"""

from __future__ import annotations

import json
import math
import os
import re
import struct
from pathlib import Path
from typing import Any

from ...errors import ModelIntegrityError, NotFoundError, ValidationError
from ...utils.hashing import sha256_file
from ..models import ModelFile, ModelFormat, ModelInfo

__all__ = [
    "SAFETENSORS_MAX_HEADER_BYTES",
    "compatibility_notes",
    "count_safetensors_parameters",
    "inspect_hf_cache_snapshot",
    "inspect_local_dir",
    "looks_like_model_dir",
    "read_safetensors_header",
]

#: No legitimate safetensors header is anywhere near this large; the cap stops a
#: corrupt length prefix from making us allocate a multi-gigabyte buffer.
SAFETENSORS_MAX_HEADER_BYTES = 64 * 1024 * 1024

#: Metadata files above this size are not parsed (tokenizer.json is a few MB at
#: most; anything larger is not a tokenizer).
MAX_JSON_BYTES = 64 * 1024 * 1024

#: Hard cap on files walked inside one model directory (protects scans of very
#: large trees such as datasets accidentally placed under the models root).
MAX_WALK_FILES = 5000

GGUF_MAGIC = b"GGUF"

#: Directories that never contain model weights.
SKIP_DIR_NAMES = frozenset(
    {".git", ".hg", ".svn", "__pycache__", ".cache", ".venv", "venv", "node_modules"}
)

#: Weight artifact suffixes we report individually.
WEIGHT_SUFFIXES = frozenset({".safetensors", ".bin", ".gguf", ".npz"})

#: Top-level metadata files worth listing on a ModelInfo.
METADATA_FILE_NAMES = frozenset(
    {
        "config.json",
        "generation_config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "processor_config.json",
        "preprocessor_config.json",
        "model.safetensors.index.json",
        "pytorch_model.bin.index.json",
        "chat_template.jinja",
        "chat_template.json",
        "README.md",
    }
)

#: Keys that hold the language-model configuration inside multimodal bundles.
NESTED_CONFIG_KEYS = (
    "text_config",
    "llm_config",
    "language_config",
    "text_encoder_config",
    "language_model_config",
)

#: Tensor names of routed experts, e.g. ``...block_sparse_moe.experts.3.w1.weight``.
ROUTED_EXPERT_RE = re.compile(r"\.experts\.\d+\.")

#: Safetensors dtype -> bytes per element. Unknown dtypes are accepted without
#: the shape/offset cross-check instead of being rejected.
DTYPE_BYTES: dict[str, int] = {
    "F64": 8,
    "F32": 4,
    "F16": 2,
    "BF16": 2,
    "I64": 8,
    "I32": 4,
    "I16": 2,
    "I8": 1,
    "U64": 8,
    "U32": 4,
    "U16": 2,
    "U8": 1,
    "BOOL": 1,
    "F8_E4M3": 1,
    "F8_E5M2": 1,
    "F8_E8M0": 1,
}

#: Markers that identify a directory as a model rather than a container.
_MODEL_MARKERS = (
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "generation_config.json",
)


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


# ---------------------------------------------------------------------------
# Safetensors
# ---------------------------------------------------------------------------


def _validate_safetensors_header(
    header: dict[str, Any], *, payload_size: int, name: str
) -> None:
    metadata = header.get("__metadata__")
    if metadata is not None and not isinstance(metadata, dict):
        raise ModelIntegrityError(
            f"Malformed safetensors header in {name}: __metadata__ must be an object.",
            details={"file": name},
        )
    for tensor_name, entry in header.items():
        if tensor_name == "__metadata__":
            continue
        if not isinstance(entry, dict):
            raise ModelIntegrityError(
                f"Malformed safetensors header in {name}: tensor {tensor_name!r} "
                "is not an object.",
                details={"file": name, "tensor": tensor_name},
            )
        dtype = entry.get("dtype")
        shape = entry.get("shape")
        offsets = entry.get("data_offsets")
        if not isinstance(dtype, str) or not dtype:
            raise ModelIntegrityError(
                f"Malformed safetensors header in {name}: tensor {tensor_name!r} "
                "has no dtype.",
                details={"file": name, "tensor": tensor_name},
            )
        if not isinstance(shape, list) or any(
            not isinstance(dim, int) or isinstance(dim, bool) or dim < 0
            for dim in shape
        ):
            raise ModelIntegrityError(
                f"Malformed safetensors header in {name}: tensor {tensor_name!r} "
                "has an invalid shape.",
                details={"file": name, "tensor": tensor_name, "shape": shape},
            )
        if (
            not isinstance(offsets, list)
            or len(offsets) != 2
            or any(
                not isinstance(value, int) or isinstance(value, bool)
                for value in offsets
            )
        ):
            raise ModelIntegrityError(
                f"Malformed safetensors header in {name}: tensor {tensor_name!r} "
                "has invalid data_offsets.",
                details={"file": name, "tensor": tensor_name, "data_offsets": offsets},
            )
        start, end = offsets
        if start < 0 or end < start or end > payload_size:
            raise ModelIntegrityError(
                f"Malformed safetensors header in {name}: tensor {tensor_name!r} "
                "points outside the data section (file is truncated or corrupt).",
                details={
                    "file": name,
                    "tensor": tensor_name,
                    "data_offsets": [start, end],
                    "payload_bytes": payload_size,
                },
            )
        element_size = DTYPE_BYTES.get(dtype)
        if element_size is not None:
            expected = math.prod(shape) * element_size
            if expected != end - start:
                raise ModelIntegrityError(
                    f"Malformed safetensors header in {name}: tensor {tensor_name!r} "
                    f"declares {end - start} bytes but shape {shape} with dtype "
                    f"{dtype} requires {expected}.",
                    details={"file": name, "tensor": tensor_name},
                )


def read_safetensors_header(path: Path) -> dict[str, Any]:
    """Parse and validate the JSON header of a safetensors file.

    The format is an 8-byte little-endian unsigned header length followed by a
    UTF-8 JSON object describing every tensor, then the raw tensor data.
    """
    source = Path(path)
    try:
        file_size = source.stat().st_size
        with source.open("rb") as handle:
            prefix = handle.read(8)
            if len(prefix) != 8:
                raise ModelIntegrityError(
                    f"{source.name} is too small to be a safetensors file.",
                    details={"file": str(source), "size_bytes": file_size},
                )
            (header_size,) = struct.unpack("<Q", prefix)
            if (
                header_size == 0
                or header_size > SAFETENSORS_MAX_HEADER_BYTES
                or header_size + 8 > file_size
            ):
                raise ModelIntegrityError(
                    f"Invalid safetensors header length in {source.name}: {header_size}.",
                    details={
                        "file": str(source),
                        "header_bytes": header_size,
                        "size_bytes": file_size,
                        "max_header_bytes": SAFETENSORS_MAX_HEADER_BYTES,
                    },
                )
            raw = handle.read(header_size)
    except OSError as exc:
        raise ModelIntegrityError(
            f"Could not read safetensors file {source.name}: {exc.strerror or exc}",
            details={"file": str(source)},
        ) from exc

    if len(raw) != header_size:
        raise ModelIntegrityError(
            f"Truncated safetensors header in {source.name}.",
            details={
                "file": str(source),
                "expected_bytes": header_size,
                "read": len(raw),
            },
        )
    try:
        header = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelIntegrityError(
            f"Malformed safetensors header in {source.name}: {exc}",
            details={"file": str(source)},
        ) from exc
    if not isinstance(header, dict):
        raise ModelIntegrityError(
            f"Malformed safetensors header in {source.name}: root must be an object.",
            details={"file": str(source)},
        )
    _validate_safetensors_header(
        header, payload_size=file_size - 8 - header_size, name=source.name
    )
    return header


def _header_parameter_counts(header: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for tensor_name, entry in header.items():
        if tensor_name == "__metadata__" or not isinstance(entry, dict):
            continue
        shape = entry.get("shape")
        if isinstance(shape, list) and all(isinstance(dim, int) for dim in shape):
            counts[tensor_name] = math.prod(shape) if shape else 1
    return counts


def count_safetensors_parameters(path: Path) -> int:
    """Exact number of elements stored in one safetensors file."""
    header = read_safetensors_header(path)
    return sum(_header_parameter_counts(header).values())


# ---------------------------------------------------------------------------
# Small parsing helpers
# ---------------------------------------------------------------------------


def _load_json_file(
    root: Path, name: str, warnings: list[str]
) -> dict[str, Any] | None:
    path = root / name
    if not path.is_file():
        return None
    try:
        size = path.stat().st_size
    except OSError as exc:
        warnings.append(f"Could not stat {name}: {exc.strerror or exc}.")
        return None
    if size > MAX_JSON_BYTES:
        warnings.append(
            f"{name} is {size} bytes; files larger than {MAX_JSON_BYTES} bytes "
            "are not parsed."
        )
        return None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            data = json.load(handle)
    except OSError as exc:
        warnings.append(f"Could not read {name}: {exc.strerror or exc}.")
        return None
    except json.JSONDecodeError as exc:
        warnings.append(
            f"{name} is not valid JSON ({exc.msg} at line {exc.lineno}); "
            "dependent metadata was skipped."
        )
        return None
    if not isinstance(data, dict):
        warnings.append(
            f"{name} must contain a JSON object; dependent metadata was skipped."
        )
        return None
    return data


def _lookup(config: dict[str, Any], keys: tuple[str, ...]) -> Any:
    """Read ``keys`` from the text-model config first, then the root config.

    Multimodal bundles keep the language-model dimensions in ``text_config``;
    reading the nested table first avoids reporting the vision tower's sizes.
    """
    sources: list[dict[str, Any]] = []
    for nested_key in NESTED_CONFIG_KEYS:
        nested = config.get(nested_key)
        if isinstance(nested, dict):
            sources.append(nested)
    sources.append(config)
    for source in sources:
        for key in keys:
            if key in source and source[key] is not None:
                return source[key]
    return None


def _first_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                return item.strip()
    return None


def _as_positive_int(value: Any, *, field: str, warnings: list[str]) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        warnings.append(f"Ignored boolean value for {field}.")
        return None
    if isinstance(value, int):
        if value > 0:
            return value
        warnings.append(f"Ignored non-positive value for {field}: {value}.")
        return None
    if isinstance(value, float) and value.is_integer():
        if value > 0:
            return int(value)
        warnings.append(f"Ignored non-positive value for {field}: {value}.")
        return None
    if isinstance(value, str):
        try:
            parsed = int(value.strip())
        except ValueError:
            warnings.append(f"Ignored non-integer value for {field}: {value!r}.")
            return None
        if parsed > 0:
            return parsed
        warnings.append(f"Ignored non-positive value for {field}: {value!r}.")
        return None
    warnings.append(f"Ignored non-integer value for {field}: {value!r}.")
    return None


def _walk_files(root: Path, warnings: list[str]) -> list[Path]:
    """All regular files under ``root``, skipping symlinks and junk directories."""
    files: list[Path] = []
    skipped_symlinks = 0

    def _on_error(exc: OSError) -> None:
        warnings.append(
            f"Could not read directory {exc.filename}: {exc.strerror or exc}."
        )

    for dirpath, dirnames, filenames in os.walk(
        root, followlinks=False, onerror=_on_error
    ):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in SKIP_DIR_NAMES and not name.startswith(".")
        )
        for filename in sorted(filenames):
            if len(files) >= MAX_WALK_FILES:
                warnings.append(
                    f"Directory contains more than {MAX_WALK_FILES} files; remaining "
                    "files were not measured."
                )
                return files
            candidate = Path(dirpath) / filename
            if candidate.is_symlink():
                skipped_symlinks += 1
                continue
            try:
                if not candidate.is_file():
                    continue
            except OSError:
                continue
            files.append(candidate)
    if skipped_symlinks:
        warnings.append(
            f"Skipped {skipped_symlinks} symlink(s) inside the model directory; "
            "symlinks are never followed."
        )
    return files


def _file_size(path: Path, warnings: list[str]) -> int:
    try:
        return path.stat().st_size
    except OSError as exc:
        warnings.append(f"Could not stat {path.name}: {exc.strerror or exc}.")
        return 0


def _detect_license(root: Path, warnings: list[str]) -> str | None:
    candidates: list[Path] = []
    try:
        for entry in sorted(root.iterdir()):
            if not entry.is_file():
                continue
            upper = entry.name.upper()
            if upper.startswith(("LICENSE", "COPYING", "LICENCE")):
                candidates.append(entry)
    except OSError as exc:
        warnings.append(f"Could not list license files: {exc.strerror or exc}.")
        return None
    if not candidates:
        return None
    try:
        text = candidates[0].read_text(encoding="utf-8", errors="replace")[:8000]
    except OSError as exc:
        warnings.append(f"Could not read {candidates[0].name}: {exc.strerror or exc}.")
        return None
    compact = re.sub(r"\s+", " ", text).lower()
    if "gnu affero general public license" in compact:
        return "AGPL-3.0"
    if "gnu lesser general public license" in compact:
        return "LGPL-3.0"
    if "gnu general public license" in compact:
        return "GPL-3.0" if "version 3" in compact else "GPL"
    if "apache license" in compact and "2.0" in compact:
        return "Apache-2.0"
    if (
        "mit license" in compact
        or "permission is hereby granted, free of charge" in compact
    ):
        return "MIT"
    if "redistribution and use in source and binary forms" in compact:
        return "BSD-3-Clause"
    if "llama" in compact and "community license" in compact:
        return "Llama Community License"
    if "creative commons" in compact:
        return "CC-BY-4.0"
    if "openrail" in compact:
        return "OpenRAIL"
    warnings.append(
        f"{candidates[0].name} is present but its license text was not recognized; "
        "review it manually."
    )
    return None


def _estimate_parameters_from_config(config: dict[str, Any]) -> int | None:
    """Rough parameter estimate used only when no safetensors headers exist.

    The result is never presented as exact: the caller labels it ``estimated``
    and adds a warning.
    """
    warnings: list[str] = []
    hidden = _as_positive_int(
        _lookup(config, ("hidden_size", "n_embd", "d_model")),
        field="hidden_size",
        warnings=warnings,
    )
    layers = _as_positive_int(
        _lookup(config, ("num_hidden_layers", "n_layer", "num_layers")),
        field="num_hidden_layers",
        warnings=warnings,
    )
    if not hidden or not layers:
        return None
    heads = _as_positive_int(
        _lookup(config, ("num_attention_heads", "n_head")),
        field="num_attention_heads",
        warnings=warnings,
    )
    kv_heads = _as_positive_int(
        _lookup(config, ("num_key_value_heads", "num_kv_heads", "n_head_kv")),
        field="num_key_value_heads",
        warnings=warnings,
    )
    head_dim = _as_positive_int(
        _lookup(config, ("head_dim", "attention_head_dim")),
        field="head_dim",
        warnings=warnings,
    )
    if head_dim is None and heads:
        head_dim = hidden // heads
    intermediate = _as_positive_int(
        _lookup(config, ("intermediate_size", "n_inner", "ffn_dim", "ffn_hidden_size")),
        field="intermediate_size",
        warnings=warnings,
    )
    vocab = _as_positive_int(
        _lookup(config, ("vocab_size",)),
        field="vocab_size",
        warnings=warnings,
    )
    if not head_dim or head_dim <= 0:
        return None
    heads = heads or 1
    kv_heads = kv_heads or heads
    attention = (
        hidden * heads * head_dim
        + 2 * hidden * kv_heads * head_dim
        + heads * head_dim * hidden
    )
    mlp = 3 * hidden * intermediate if intermediate else hidden * hidden * 4
    experts = _as_positive_int(
        _lookup(config, ("num_local_experts", "num_experts", "n_routed_experts")),
        field="num_local_experts",
        warnings=warnings,
    )
    if experts and experts > 1:
        mlp *= experts
    per_layer = attention + mlp + 2 * hidden
    total = layers * per_layer + hidden
    if vocab:
        total += vocab * hidden  # token embeddings
        tie = _lookup(config, ("tie_word_embeddings",))
        if tie is not True:
            total += vocab * hidden  # untied lm_head
    return int(total) if total > 0 else None


def looks_like_model_dir(path: Path) -> bool:
    """Whether ``path`` directly contains model metadata or weight files."""
    for marker in _MODEL_MARKERS:
        if (path / marker).is_file():
            return True
    try:
        for entry in path.iterdir():
            if entry.is_file() and entry.suffix in {".safetensors", ".gguf", ".bin"}:
                return True
    except OSError:
        return False
    return False


def _detect_gguf(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(4) == GGUF_MAGIC
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Local inspection
# ---------------------------------------------------------------------------


def inspect_local_dir(path: Path, *, hash_files: bool = False) -> ModelInfo:
    """Build a :class:`ModelInfo` from a model directory on disk."""
    root = Path(path).expanduser()
    if not root.exists():
        raise NotFoundError(
            f"Model directory not found: {root}",
            details={"path": str(root)},
            hint="Check the path or run `fsociety models scan` to list known models.",
        )
    if not root.is_dir():
        raise ValidationError(
            f"Model path is not a directory: {root}",
            details={"path": str(root)},
            hint="Pass the directory that contains config.json and the weights.",
        )
    root = root.resolve()

    warnings: list[str] = []
    config = _load_json_file(root, "config.json", warnings)
    if config is None:
        warnings.append(
            "config.json is missing or unreadable; architecture metadata is unavailable."
        )
        config = {}
    tokenizer_config = _load_json_file(root, "tokenizer_config.json", warnings) or {}
    _load_json_file(root, "generation_config.json", warnings)
    tokenizer = _load_json_file(root, "tokenizer.json", warnings) or {}
    processor_config = _load_json_file(root, "processor_config.json", warnings) or {}
    index = _load_json_file(root, "model.safetensors.index.json", warnings)

    architecture = _first_string(_lookup(config, ("architectures",)))
    model_type = _first_string(_lookup(config, ("model_type",)))
    hidden_size = _as_positive_int(
        _lookup(config, ("hidden_size", "n_embd", "d_model")),
        field="hidden_size",
        warnings=warnings,
    )
    num_hidden_layers = _as_positive_int(
        _lookup(config, ("num_hidden_layers", "n_layer", "num_layers")),
        field="num_hidden_layers",
        warnings=warnings,
    )
    num_attention_heads = _as_positive_int(
        _lookup(config, ("num_attention_heads", "n_head")),
        field="num_attention_heads",
        warnings=warnings,
    )
    num_key_value_heads = _as_positive_int(
        _lookup(config, ("num_key_value_heads", "num_kv_heads", "n_head_kv")),
        field="num_key_value_heads",
        warnings=warnings,
    )
    head_dim = _as_positive_int(
        _lookup(config, ("head_dim", "attention_head_dim")),
        field="head_dim",
        warnings=warnings,
    )
    intermediate_size = _as_positive_int(
        _lookup(config, ("intermediate_size", "n_inner", "ffn_dim", "ffn_hidden_size")),
        field="intermediate_size",
        warnings=warnings,
    )
    vocab_size = _as_positive_int(
        _lookup(config, ("vocab_size",)),
        field="vocab_size",
        warnings=warnings,
    )
    if vocab_size is None and isinstance(tokenizer.get("model"), dict):
        tokens = tokenizer["model"].get("vocab")
        if isinstance(tokens, dict) and tokens:
            vocab_size = len(tokens)
            warnings.append(
                "vocab_size was taken from tokenizer.json because config.json does "
                "not declare it."
            )
    max_position_embeddings = _as_positive_int(
        _lookup(
            config,
            (
                "max_position_embeddings",
                "max_seq_len",
                "n_positions",
                "seq_length",
                "max_sequence_length",
            ),
        ),
        field="max_position_embeddings",
        warnings=warnings,
    )
    torch_dtype_value = _lookup(config, ("torch_dtype", "dtype"))
    torch_dtype = torch_dtype_value if isinstance(torch_dtype_value, str) else None
    if torch_dtype_value is not None and torch_dtype is None:
        warnings.append("Ignored non-string torch_dtype in config.json.")

    quantization: str | None = None
    quantization_config = _lookup(config, ("quantization_config",))
    if isinstance(quantization_config, dict):
        raw_quant = quantization_config.get("quant_method") or quantization_config.get(
            "quant_type"
        )
        if isinstance(raw_quant, str) and raw_quant:
            quantization = raw_quant
        elif isinstance(raw_quant, list) and raw_quant:
            quantization = str(raw_quant[0])
    elif isinstance(quantization_config, str) and quantization_config:
        quantization = quantization_config

    auto_map = config.get("auto_map")
    requires_remote_code = isinstance(auto_map, dict) and bool(auto_map)

    multimodal_markers = (
        "vision_config",
        "audio_config",
        "speech_config",
        "image_config",
        "vision_tower",
        "audio_tower",
        "multi_modal_projector",
    )
    is_multimodal = (
        any(isinstance(config.get(marker), dict) for marker in multimodal_markers)
        or bool(processor_config.get("image_processor"))
        or (root / "preprocessor_config.json").is_file()
    )

    num_experts = _as_positive_int(
        _lookup(
            config,
            (
                "num_local_experts",
                "num_experts",
                "n_routed_experts",
                "num_routed_experts",
            ),
        ),
        field="num_local_experts",
        warnings=warnings,
    )
    experts_per_tok = _as_positive_int(
        _lookup(
            config,
            (
                "num_experts_per_tok",
                "moe_top_k",
                "num_experts_per_token",
                "top_k_experts",
            ),
        ),
        field="num_experts_per_tok",
        warnings=warnings,
    )

    if num_experts and experts_per_tok and experts_per_tok > num_experts:
        warnings.append(
            f"config.json declares num_experts_per_tok={experts_per_tok} but only "
            f"{num_experts} experts; the top-k value was ignored."
        )
        experts_per_tok = None

    chat_template = tokenizer_config.get("chat_template")
    has_chat_template = False
    if (
        isinstance(chat_template, str)
        and chat_template.strip()
        or isinstance(chat_template, list)
        and any(
            isinstance(entry, dict) and entry.get("template") for entry in chat_template
        )
    ):
        has_chat_template = True
    if not has_chat_template:
        has_chat_template = (root / "chat_template.jinja").is_file() or (
            root / "chat_template.json"
        ).is_file()

    all_files = _walk_files(root, warnings)
    size_bytes = sum(_file_size(candidate, warnings) for candidate in all_files)

    safetensors_files = [
        candidate
        for candidate in all_files
        if candidate.suffix == ".safetensors"
        and candidate.name != "adapter_model.safetensors"
    ]
    adapter_files = [
        candidate
        for candidate in all_files
        if candidate.name == "adapter_model.safetensors"
    ]
    if adapter_files:
        warnings.append(
            "Adapter weights (adapter_model.safetensors) are present but excluded "
            "from the parameter count; this directory looks like a PEFT adapter, "
            "not a full model."
        )
    pytorch_files = [candidate for candidate in all_files if candidate.suffix == ".bin"]
    gguf_files = [candidate for candidate in all_files if candidate.suffix == ".gguf"]
    npz_files = [candidate for candidate in all_files if candidate.suffix == ".npz"]

    parameter_count: int | None = None
    active_parameter_count: int | None = None
    metadata_source = "declared"
    expert_parameters = 0
    measured = False

    if safetensors_files:
        total = 0
        for weight_file in safetensors_files:
            try:
                header = read_safetensors_header(weight_file)
            except ModelIntegrityError as exc:
                warnings.append(f"{_relative(root, weight_file)}: {exc.message}")
                continue
            counts = _header_parameter_counts(header)
            total += sum(counts.values())
            for tensor_name, count in counts.items():
                if ROUTED_EXPERT_RE.search(tensor_name):
                    expert_parameters += count
        if total > 0:
            parameter_count = total
            measured = True
        else:
            warnings.append(
                "Safetensors files were found but no tensor metadata could be read; "
                "the parameter count is unknown."
            )

    if isinstance(index, dict):
        weight_map = index.get("weight_map")
        if isinstance(weight_map, dict) and weight_map:
            present = {candidate.name for candidate in safetensors_files}
            invalid = sorted(
                str(value)
                for value in weight_map.values()
                if not isinstance(value, str)
                or not value
                or "/" in value
                or "\\" in value
                or ".." in value
            )
            if invalid:
                warnings.append(
                    "model.safetensors.index.json contains invalid shard names; "
                    "only present files were counted."
                )
            expected = {
                value
                for value in weight_map.values()
                if isinstance(value, str) and value not in invalid
            }
            missing = sorted(expected - present)
            if missing:
                preview = ", ".join(missing[:5])
                suffix = "" if len(missing) <= 5 else ", ..."
                warnings.append(
                    f"Shard index references {len(missing)} missing shard(s): "
                    f"{preview}{suffix}. The model is incomplete; the parameter "
                    "count only covers the shards present on disk."
                )
        else:
            warnings.append(
                "model.safetensors.index.json has no usable weight_map object."
            )

    if pytorch_files and safetensors_files:
        warnings.append(
            "Both safetensors and PyTorch .bin weights are present; the parameter "
            "count uses the safetensors headers."
        )

    if parameter_count is None and pytorch_files:
        estimate = _estimate_parameters_from_config(config)
        if estimate is not None:
            parameter_count = estimate
            metadata_source = "estimated"
            warnings.append(
                "Parameter count estimated from config.json because only PyTorch "
                ".bin weights are available; the true count may differ. Convert to "
                "safetensors for an exact measurement."
            )
        else:
            warnings.append(
                "PyTorch .bin weights are present but config.json lacks the "
                "dimensions needed to estimate a parameter count."
            )

    if gguf_files:
        invalid_gguf = [
            candidate for candidate in gguf_files if not _detect_gguf(candidate)
        ]
        if invalid_gguf:
            names = ", ".join(candidate.name for candidate in invalid_gguf[:5])
            warnings.append(
                f"{len(invalid_gguf)} GGUF file(s) do not start with the 'GGUF' "
                f"magic number: {names}. They may be corrupt."
            )
        if parameter_count is None:
            warnings.append(
                "GGUF weights detected: parameter counts require a GGUF metadata "
                "parser, which this inspection does not attempt. The count stays "
                "unknown rather than being guessed."
            )

    if not safetensors_files and not pytorch_files and not gguf_files and not npz_files:
        warnings.append(
            "No recognized weight files (.safetensors, .bin, .gguf, .npz) were found."
        )

    if parameter_count is None and config:
        metadata_source = "declared"
    elif parameter_count is None:
        metadata_source = "unknown"

    if measured:
        metadata_source = "measured"

    is_mixture_of_experts = bool(
        (num_experts and num_experts > 1) or experts_per_tok or expert_parameters > 0
    )
    if is_mixture_of_experts:
        if num_experts and num_experts > 1 and expert_parameters > 0:
            if experts_per_tok:
                routed = int(expert_parameters * experts_per_tok / num_experts)
                total_parameters = parameter_count or 0
                active_parameter_count = total_parameters - expert_parameters + routed
                warnings.append(
                    "active_parameter_count is derived from routed expert tensors "
                    "and the declared top-k; it is an estimate, not a measurement."
                )
            else:
                warnings.append(
                    "Mixture-of-Experts model without num_experts_per_tok in "
                    "config.json; the active parameter count is unknown."
                )
        elif expert_parameters > 0 and not num_experts:
            warnings.append(
                "Expert tensors were found but config.json does not declare how "
                "many experts exist; the active parameter count is unknown."
            )

    formats: list[ModelFormat] = []
    if safetensors_files:
        formats.append("safetensors")
    if pytorch_files:
        formats.append("pytorch")
    if gguf_files:
        formats.append("gguf")
    if not formats and npz_files:
        formats.append("mlx")
    if not formats:
        formats.append("unknown")

    file_entries: list[ModelFile] = []
    for candidate in all_files:
        rel = _relative(root, candidate)
        is_weight = candidate.suffix in WEIGHT_SUFFIXES
        is_metadata = (
            len(Path(rel).parts) == 1 and candidate.name in METADATA_FILE_NAMES
        )
        if not (is_weight or is_metadata):
            continue
        digest: str | None = None
        if hash_files:
            try:
                digest = sha256_file(candidate)
            except OSError as exc:
                warnings.append(f"Could not hash {rel}: {exc.strerror or exc}.")
        if candidate.suffix == ".safetensors":
            entry_format: ModelFormat = "safetensors"
        elif candidate.suffix == ".bin":
            entry_format = "pytorch"
        elif candidate.suffix == ".gguf":
            entry_format = "gguf"
        elif candidate.suffix == ".npz":
            entry_format = "mlx"
        else:
            entry_format = "unknown"
        file_entries.append(
            ModelFile(
                path=rel,
                size_bytes=_file_size(candidate, warnings),
                sha256=digest,
                format=entry_format,
            )
        )
    file_entries.sort(key=lambda entry: entry.path)

    license_id = _detect_license(root, warnings)

    tags: list[str] = []

    def _add_tag(value: str | None) -> None:
        if value and value not in tags:
            tags.append(value)

    _add_tag(model_type)
    _add_tag(architecture)
    for model_format in formats:
        _add_tag(model_format)
    if quantization:
        _add_tag(f"quant-{quantization}")
    if is_mixture_of_experts:
        _add_tag("mixture-of-experts")
    if is_multimodal:
        _add_tag("multimodal")
    if has_chat_template:
        _add_tag("chat-template")
    if requires_remote_code:
        _add_tag("remote-code")
    if license_id:
        _add_tag(f"license-{license_id}")

    info = ModelInfo(
        model_id=root.name,
        source="local",
        path=str(root),
        architecture=architecture,
        model_type=model_type,
        parameter_count=parameter_count,
        active_parameter_count=active_parameter_count,
        hidden_size=hidden_size,
        num_hidden_layers=num_hidden_layers,
        num_attention_heads=num_attention_heads,
        num_key_value_heads=num_key_value_heads,
        head_dim=head_dim,
        intermediate_size=intermediate_size,
        vocab_size=vocab_size,
        max_position_embeddings=max_position_embeddings,
        torch_dtype=torch_dtype,
        quantization=quantization,
        size_bytes=size_bytes,
        files=file_entries,
        formats=formats,
        has_chat_template=has_chat_template,
        is_multimodal=is_multimodal,
        is_mixture_of_experts=is_mixture_of_experts,
        license=license_id,
        tags=tags,
        requires_remote_code=requires_remote_code,
        metadata_source=metadata_source,
        warnings=warnings,
    )
    info.compatibility = compatibility_notes(info)
    return info


def inspect_hf_cache_snapshot(
    snapshot: Path, repo_id: str, commit: str, ref: str | None
) -> ModelInfo:
    """Inspect one immutable snapshot directory from the Hugging Face cache."""
    if not isinstance(repo_id, str) or not repo_id.strip():
        raise ValidationError("repo_id is required to inspect a cached snapshot.")
    if not isinstance(commit, str) or not commit.strip():
        raise ValidationError(
            "commit is required to inspect a cached snapshot.",
            hint="The snapshot directory name is the commit hash.",
        )
    info = inspect_local_dir(snapshot)
    info.source = "huggingface_cache"
    info.model_id = repo_id
    info.commit = commit
    info.revision = ref
    return info


# ---------------------------------------------------------------------------
# Compatibility hints
# ---------------------------------------------------------------------------


def compatibility_notes(info: ModelInfo) -> list[str]:
    """Report architecture hints and risks. Never promises compatibility."""
    notes: list[str] = []
    architecture = info.architecture or info.model_type
    if architecture:
        notes.append(
            f"Declared architecture: {architecture}. The engine loads models through "
            "transformers' AutoModel/AutoTokenizer pipeline, so the class must exist "
            "in the installed transformers version."
        )
    else:
        notes.append(
            "No architecture is declared (config.json missing or incomplete); "
            "the engine cannot choose a model class automatically."
        )
    if info.is_mixture_of_experts:
        notes.append(
            "Mixture-of-Experts: only a subset of experts runs per token, so "
            "throughput and memory differ from a dense model of the same total size."
        )
    if info.is_multimodal:
        notes.append(
            "Multimodal checkpoints carry vision/audio towers. The modification "
            "engine works on the language model; non-text components are unsupported."
        )
    if info.requires_remote_code:
        notes.append(
            "The checkpoint requires remote code (auto_map). Remote code execution "
            "is denied unless the repository is explicitly allowlisted "
            "(trust_remote_code policy)."
        )
    if info.quantization:
        notes.append(
            f"Pre-quantized weights ({info.quantization}) were declared. Loading may "
            "require the matching quantization runtime and can fail in the engine's "
            "dtype loop."
        )
    if "gguf" in info.formats:
        notes.append(
            "GGUF weights are for llama.cpp-style runtimes; the engine expects "
            "Hugging Face transformers weights."
        )
    if info.parameter_count is None:
        notes.append(
            "Parameter count is unknown; memory estimation and optimization planning "
            "will be refused until it is determined."
        )
    elif info.metadata_source == "estimated":
        notes.append(
            "Parameter count is estimated from config.json, not measured from "
            "weight headers; treat memory estimates as approximate."
        )
    if not info.has_chat_template:
        notes.append(
            "No chat template was found; conversational prompts may render "
            "differently than the model expects."
        )
    if not info.files:
        notes.append("No model files were detected in this directory.")
    notes.append(
        "Compatibility is not guaranteed. Load the model with the engine and run a "
        "short trial before committing to a long optimization run."
    )
    return notes
