# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Shared fixtures: synthetic model directories with valid safetensors headers."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Iterable

import pytest


def write_safetensors(
    path: Path, tensors: Iterable[tuple[str, tuple[int, ...]]]
) -> int:
    """Write a valid (zero-filled) safetensors file and return its parameter count."""
    tensors = list(tensors)
    header: dict[str, object] = {}
    offset = 0
    parameter_count = 0
    for name, shape in tensors:
        elements = 1
        for dimension in shape:
            elements *= dimension
        parameter_count += elements
        size = elements * 2  # F16 payload
        header[name] = {
            "dtype": "F16",
            "shape": list(shape),
            "data_offsets": [offset, offset + size],
        }
        offset += size
    raw = json.dumps(header).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(struct.pack("<Q", len(raw)))
        handle.write(raw)
        handle.write(b"\x00" * offset)
    return parameter_count


@pytest.fixture
def tiny_model(tmp_path: Path) -> tuple[Path, int]:
    """A tiny Llama-like model directory with exact parameter count from headers."""
    directory = tmp_path / "tiny-model"
    directory.mkdir()
    parameter_count = write_safetensors(
        directory / "model.safetensors",
        [
            ("model.embed_tokens.weight", (1000, 128)),
            ("model.layers.0.self_attn.q_proj.weight", (128, 128)),
            ("model.layers.0.mlp.down_proj.weight", (128, 256)),
            ("lm_head.weight", (1000, 128)),
        ],
    )
    (directory / "config.json").write_text(
        json.dumps(
            {
                "architectures": ["LlamaForCausalLM"],
                "model_type": "llama",
                "hidden_size": 128,
                "num_hidden_layers": 4,
                "num_attention_heads": 4,
                "num_key_value_heads": 2,
                "intermediate_size": 256,
                "vocab_size": 1000,
                "max_position_embeddings": 512,
                "torch_dtype": "bfloat16",
            }
        ),
        encoding="utf-8",
    )
    (directory / "tokenizer_config.json").write_text(
        json.dumps({"chat_template": "{{ messages }}"}),
        encoding="utf-8",
    )
    return directory, parameter_count


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    return root
