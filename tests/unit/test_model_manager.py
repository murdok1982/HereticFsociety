# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Unit tests for the torch-free model manager.

All fixtures are real files written to ``tmp_path``: safetensors headers are
built by hand byte for byte, the Hugging Face cache uses the real on-disk
layout, and verification actually mutates files after a manifest is computed.
"""

from __future__ import annotations

import json
import math
import os
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from fsociety.config import PlatformSettings
from fsociety.core.model_manager import ModelManager
from fsociety.core.model_manager.inspection import (
    compatibility_notes,
    count_safetensors_parameters,
    inspect_local_dir,
    read_safetensors_header,
)
from fsociety.errors import (
    ModelIntegrityError,
    NotFoundError,
    PathSecurityError,
    ValidationError,
)
from fsociety.logging import AuditLogger

REPO_ROOT = Path(__file__).resolve().parents[2]
DTYPE_BYTES = {"F32": 4, "F16": 2, "BF16": 2, "I64": 8}


def write_safetensors(
    path: Path,
    tensors: dict[str, tuple[str, tuple[int, ...]]],
    *,
    metadata: dict[str, str] | None = None,
) -> Path:
    """Write a valid safetensors file (header + zero-filled data)."""
    header: dict[str, Any] = {}
    offset = 0
    for name, (dtype, shape) in tensors.items():
        elements = math.prod(shape) if shape else 1
        size = elements * DTYPE_BYTES[dtype]
        header[name] = {
            "dtype": dtype,
            "shape": list(shape),
            "data_offsets": [offset, offset + size],
        }
        offset += size
    if metadata is not None:
        header["__metadata__"] = metadata
    raw = json.dumps(header).encode("utf-8")
    path.write_bytes(struct.pack("<Q", len(raw)) + raw + b"\x00" * offset)
    return path


def write_json(path: Path, payload: Any) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def llama_config(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "architectures": ["LlamaForCausalLM"],
        "model_type": "llama",
        "hidden_size": 32,
        "num_hidden_layers": 2,
        "num_attention_heads": 4,
        "num_key_value_heads": 2,
        "head_dim": 8,
        "intermediate_size": 64,
        "vocab_size": 100,
        "max_position_embeddings": 2048,
        "torch_dtype": "bfloat16",
        "tie_word_embeddings": False,
    }
    config.update(overrides)
    return config


def make_model(directory: Path, *, config: dict[str, Any] | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    write_json(
        directory / "config.json", config if config is not None else llama_config()
    )
    write_json(
        directory / "tokenizer_config.json",
        {"chat_template": "{{ messages }}", "model_max_length": 2048},
    )
    write_safetensors(
        directory / "model.safetensors",
        {
            "model.embed_tokens.weight": ("F32", (100, 32)),
            "model.norm.weight": ("F32", (32,)),
        },
    )
    return directory


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PlatformSettings:
    monkeypatch.delenv("HERETIC_CONFIG_FILE", raising=False)
    return PlatformSettings(
        workspace=tmp_path / "workspace",
        models_dir=tmp_path / "workspace" / "models",
        hf_cache_dir=tmp_path / "hf-cache",
    )


@pytest.fixture
def manager(settings: PlatformSettings, tmp_path: Path) -> ModelManager:
    logger = AuditLogger(tmp_path / "audit.jsonl", level="INFO", echo=False)
    return ModelManager(settings, logger)


# ---------------------------------------------------------------------------
# Safetensors parsing
# ---------------------------------------------------------------------------


def test_read_safetensors_header_and_exact_parameter_count(tmp_path: Path) -> None:
    path = write_safetensors(
        tmp_path / "model.safetensors",
        {"a": ("F32", (2, 3)), "b": ("F16", (5,))},
        metadata={"format": "pt"},
    )
    header = read_safetensors_header(path)
    assert set(header) == {"a", "b", "__metadata__"}
    assert header["a"]["shape"] == [2, 3]
    assert count_safetensors_parameters(path) == 11


def test_malformed_safetensors_raises_typed_error(tmp_path: Path) -> None:
    truncated = tmp_path / "short.safetensors"
    truncated.write_bytes(b"\x01\x02\x03")
    with pytest.raises(ModelIntegrityError):
        read_safetensors_header(truncated)

    bogus_length = tmp_path / "length.safetensors"
    bogus_length.write_bytes(struct.pack("<Q", 10**12) + b"{}")
    with pytest.raises(ModelIntegrityError):
        read_safetensors_header(bogus_length)

    bad_json = tmp_path / "json.safetensors"
    raw = b"{not json"
    bad_json.write_bytes(struct.pack("<Q", len(raw)) + raw)
    with pytest.raises(ModelIntegrityError):
        read_safetensors_header(bad_json)


# ---------------------------------------------------------------------------
# Local inspection
# ---------------------------------------------------------------------------


def test_inspect_local_dir_reads_metadata_exactly(tmp_path: Path) -> None:
    model = make_model(tmp_path / "llama-tiny")
    (model / "LICENSE").write_text(
        "Apache License\nVersion 2.0, January 2004\n", encoding="utf-8"
    )
    info = inspect_local_dir(model)

    assert info.model_id == "llama-tiny"
    assert info.source == "local"
    assert info.path == str(model.resolve())
    assert info.architecture == "LlamaForCausalLM"
    assert info.model_type == "llama"
    assert info.parameter_count == 100 * 32 + 32
    assert info.metadata_source == "measured"
    assert info.hidden_size == 32
    assert info.num_hidden_layers == 2
    assert info.num_attention_heads == 4
    assert info.num_key_value_heads == 2
    assert info.head_dim == 8
    assert info.intermediate_size == 64
    assert info.vocab_size == 100
    assert info.max_position_embeddings == 2048
    assert info.torch_dtype == "bfloat16"
    assert info.formats == ["safetensors"]
    assert info.has_chat_template is True
    assert info.is_multimodal is False
    assert info.is_mixture_of_experts is False
    assert info.requires_remote_code is False
    assert info.license == "Apache-2.0"
    assert "chat-template" in info.tags
    assert "license-Apache-2.0" in info.tags
    assert info.size_bytes and info.size_bytes > 0
    listed = {entry.path for entry in info.files}
    assert "config.json" in listed
    assert "model.safetensors" in listed
    assert info.compatibility
    assert "not guaranteed" in info.compatibility[-1].lower()


def test_inspect_detects_multimodal_and_quantization(tmp_path: Path) -> None:
    model = make_model(
        tmp_path / "multimodal",
        config=llama_config(
            vision_config={"hidden_size": 16},
            quantization_config={"quant_method": "gptq"},
        ),
    )
    info = inspect_local_dir(model)
    assert info.is_multimodal is True
    assert info.quantization == "gptq"
    assert "multimodal" in info.tags
    notes = compatibility_notes(info)
    assert any("multimodal" in note.lower() for note in notes)
    assert any("gptq" in note for note in notes)


def test_moe_shards_index_and_remote_code(tmp_path: Path) -> None:
    model = tmp_path / "moe-sharded"
    model.mkdir()
    write_json(
        model / "config.json",
        {
            "architectures": ["MixtralForCausalLM"],
            "model_type": "mixtral",
            "hidden_size": 16,
            "num_hidden_layers": 2,
            "num_attention_heads": 4,
            "num_key_value_heads": 2,
            "head_dim": 4,
            "intermediate_size": 32,
            "vocab_size": 50,
            "max_position_embeddings": 4096,
            "num_local_experts": 4,
            "num_experts_per_tok": 2,
            "auto_map": {"AutoConfig": "configuration_mixtral.MixtralConfig"},
        },
    )
    shard_a = {
        "model.embed_tokens.weight": ("F32", (50, 16)),
        "model.layers.0.block_sparse_moe.experts.0.w1.weight": ("F32", (32, 16)),
        "model.layers.0.block_sparse_moe.experts.1.w1.weight": ("F32", (32, 16)),
    }
    shard_b = {
        "model.layers.0.block_sparse_moe.experts.2.w1.weight": ("F32", (32, 16)),
        "model.layers.0.block_sparse_moe.experts.3.w1.weight": ("F32", (32, 16)),
        "lm_head.weight": ("F32", (50, 16)),
    }
    write_safetensors(model / "model-00001-of-00002.safetensors", shard_a)
    write_safetensors(model / "model-00002-of-00002.safetensors", shard_b)
    weight_map = {name: "model-00001-of-00002.safetensors" for name in shard_a} | {
        name: "model-00002-of-00002.safetensors" for name in shard_b
    }
    weight_map["model.norm.weight"] = "model-00003-of-00003.safetensors"
    write_json(
        model / "model.safetensors.index.json",
        {"metadata": {"total_size": 0}, "weight_map": weight_map},
    )

    info = inspect_local_dir(model)

    assert info.parameter_count == 800 + 4 * 512 + 800
    assert info.active_parameter_count == 2624
    assert info.active_parameter_count < info.parameter_count
    assert info.is_mixture_of_experts is True
    assert info.requires_remote_code is True
    assert info.num_key_value_heads == 2
    assert info.vocab_size == 50
    assert info.max_position_embeddings == 4096
    assert info.metadata_source == "measured"
    assert "mixture-of-experts" in info.tags
    assert "remote-code" in info.tags
    assert any("missing shard" in warning for warning in info.warnings)
    assert any("remote code" in note.lower() for note in info.compatibility)


def test_malformed_config_warns_without_crashing(tmp_path: Path) -> None:
    model = tmp_path / "broken-config"
    model.mkdir()
    (model / "config.json").write_text("{ this is not json", encoding="utf-8")
    write_safetensors(model / "model.safetensors", {"w": ("F32", (4, 4))})
    info = inspect_local_dir(model)
    assert info.parameter_count == 16
    assert info.metadata_source == "measured"
    assert any("config.json is not valid JSON" in warning for warning in info.warnings)
    assert info.architecture is None


def test_pytorch_bin_fallback_is_marked_estimated(tmp_path: Path) -> None:
    model = tmp_path / "bin-only"
    model.mkdir()
    write_json(
        model / "config.json",
        llama_config(
            hidden_size=8,
            num_hidden_layers=2,
            num_attention_heads=2,
            num_key_value_heads=1,
            head_dim=4,
            intermediate_size=16,
            vocab_size=32,
            tie_word_embeddings=True,
        ),
    )
    (model / "pytorch_model.bin").write_bytes(b"\x00" * 32)
    info = inspect_local_dir(model)
    assert info.parameter_count == 1448
    assert info.metadata_source == "estimated"
    assert info.formats == ["pytorch"]
    assert any("estimated" in warning.lower() for warning in info.warnings)


def test_gguf_is_detected_but_not_counted(tmp_path: Path) -> None:
    model = tmp_path / "gguf"
    model.mkdir()
    write_json(model / "config.json", llama_config())
    (model / "model.gguf").write_bytes(b"GGUF" + b"\x00" * 32)
    (model / "broken.gguf").write_bytes(b"NOPE" + b"\x00" * 8)
    info = inspect_local_dir(model)
    assert info.parameter_count is None
    assert info.formats == ["gguf"]
    assert info.metadata_source == "declared"
    assert any("GGUF" in warning for warning in info.warnings)
    assert any("magic number" in warning for warning in info.warnings)


# ---------------------------------------------------------------------------
# Hugging Face cache
# ---------------------------------------------------------------------------


def make_cached_model(cache: Path, repo_id: str, commit: str) -> Path:
    organization, name = repo_id.split("/", 1)
    snapshot = cache / f"models--{organization}--{name}" / "snapshots" / commit
    make_model(snapshot)
    refs = cache / f"models--{organization}--{name}" / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "main").write_text(commit + "\n", encoding="utf-8")
    return snapshot


def test_hf_cache_layout_is_discovered(
    manager: ModelManager, settings: PlatformSettings, tmp_path: Path
) -> None:
    commit = "a" * 40
    snapshot = make_cached_model(
        settings.hf_cache_dir or tmp_path / "hf-cache", "acme/tiny", commit
    )

    cached = manager.list_cached()
    assert len(cached) == 1
    entry = cached[0]
    assert entry.model_id == "acme/tiny"
    assert entry.commit == commit
    assert entry.revision == "main"
    assert entry.source == "huggingface_cache"
    assert entry.parameter_count == 100 * 32 + 32

    inspected = manager.inspect("acme/tiny")
    assert inspected.commit == commit
    assert inspected.path == str(snapshot.resolve())

    scanned = manager.scan(roots=[], include_hf_cache=True, limit=10)
    assert [info.model_id for info in scanned] == ["acme/tiny"]


def test_inspect_missing_cached_repo_offline_raises(tmp_path: Path) -> None:
    settings = PlatformSettings(
        workspace=tmp_path / "ws",
        models_dir=tmp_path / "ws" / "models",
        hf_cache_dir=tmp_path / "empty-cache",
        offline=True,
    )
    manager = ModelManager(settings)
    with pytest.raises(NotFoundError) as excinfo:
        manager.inspect("acme/absent")
    assert "cache" in excinfo.value.message.lower()


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def test_verify_builds_manifest_and_detects_tampering(
    manager: ModelManager, tmp_path: Path
) -> None:
    model = make_model(tmp_path / "verified")
    reference = str(model)

    baseline = manager.verify(reference)
    assert baseline.ok is False
    assert baseline.details["mode"] == "manifest_created"
    manifest = baseline.details["manifest"]
    assert "config.json" in manifest
    assert baseline.files_checked == len(manifest)
    assert baseline.hashes_verified == 0

    verified = manager.verify(reference, manifest=manifest)
    assert verified.ok is True
    assert verified.mismatches == []
    assert verified.missing == []
    assert verified.hashes_verified == verified.files_checked
    assert verified.details["mode"] == "verified"

    config = model / "config.json"
    config.write_text(config.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    tampered = manager.verify(reference, manifest=manifest)
    assert tampered.ok is False
    assert "config.json" in tampered.mismatches


def test_verify_from_manifest_file_and_missing_files(
    manager: ModelManager, tmp_path: Path
) -> None:
    model = make_model(tmp_path / "manifest-file")
    manifest = manager.manifest(str(model))
    manifest_file = write_json(
        tmp_path / "manifest.json",
        {"schema_version": 1, "model_id": "manifest-file", "files": manifest},
    )

    result = manager.verify(str(model), manifest_path=manifest_file)
    assert result.ok is True
    assert result.manifest_path == str(manifest_file)

    (model / "model.safetensors").unlink()
    missing = manager.verify(str(model), manifest_path=manifest_file)
    assert missing.ok is False
    assert "model.safetensors" in missing.missing

    with pytest.raises(ValidationError):
        manager.verify(str(model), manifest=manifest, manifest_path=manifest_file)


def test_verify_rejects_unsafe_manifest_entries(
    manager: ModelManager, tmp_path: Path
) -> None:
    model = make_model(tmp_path / "unsafe-manifest")
    with pytest.raises(PathSecurityError):
        manager.verify(str(model), manifest={"../escape": {"sha256": "0" * 64}})
    with pytest.raises(ValidationError):
        manager.verify(str(model), manifest={"config.json": {"sha256": "not-a-hash"}})


# ---------------------------------------------------------------------------
# Scanning, resolution and safety
# ---------------------------------------------------------------------------


def test_scan_finds_models_and_survives_broken_ones(
    manager: ModelManager, settings: PlatformSettings, tmp_path: Path
) -> None:
    root = settings.resolved_models_dir()
    root.mkdir(parents=True, exist_ok=True)
    make_model(root / "good-model")
    broken = root / "broken-model"
    broken.mkdir()
    (broken / "config.json").write_text("{not json", encoding="utf-8")
    write_safetensors(broken / "model.safetensors", {"w": ("F32", (2, 2))})
    (root / "not-a-model").mkdir()
    (root / "not-a-model" / "notes.txt").write_text("nothing to see", encoding="utf-8")

    infos = manager.scan(include_hf_cache=False)
    assert {info.model_id for info in infos} == {"good-model", "broken-model"}
    broken_info = next(info for info in infos if info.model_id == "broken-model")
    assert broken_info.parameter_count == 4
    assert broken_info.warnings

    limited = manager.scan(include_hf_cache=False, limit=1)
    assert len(limited) == 1

    assert manager.logger is not None
    events = manager.logger.read_recent(limit=50)
    assert any(event["category"] == "models" for event in events)


def test_reference_safety_and_policies(
    manager: ModelManager, settings: PlatformSettings, tmp_path: Path
) -> None:
    model = make_model(settings.resolved_models_dir() / "inside")
    relative = "models/inside"
    assert manager.inspect(relative).model_id == "inside"
    assert manager.resolve(relative) == model.resolve()

    with pytest.raises(ValidationError):
        manager.inspect("   ")
    with pytest.raises(PathSecurityError):
        manager.inspect("../escape")
    with pytest.raises(NotFoundError):
        manager.inspect("does-not-exist")

    strict = ModelManager(
        PlatformSettings(
            workspace=tmp_path / "strict-ws",
            models_dir=tmp_path / "strict-ws" / "models",
            hf_cache_dir=tmp_path / "strict-cache",
            allow_absolute_model_paths=False,
        )
    )
    with pytest.raises(PathSecurityError):
        strict.inspect(str(model))


def test_download_requires_explicit_scope(
    manager: ModelManager, tmp_path: Path
) -> None:
    with pytest.raises(ValidationError, match="Refusing to download"):
        manager.download("acme/tiny")
    with pytest.raises(ValidationError, match="allow_patterns"):
        manager.download("acme/tiny", allow_patterns=[])
    with pytest.raises(ValidationError, match="Unsafe"):
        manager.download("acme/tiny", allow_patterns=["../secrets"])
    with pytest.raises(ValidationError, match="repository id"):
        manager.download("not-a-repository")

    offline = ModelManager(
        PlatformSettings(
            workspace=tmp_path / "offline-ws",
            hf_cache_dir=tmp_path / "offline-cache",
            offline=True,
        )
    )
    with pytest.raises(ValidationError, match="Offline"):
        offline.download("acme/tiny", confirm_weights=True)


def test_hash_files_populates_digests(tmp_path: Path) -> None:
    model = make_model(tmp_path / "hashed")
    info = inspect_local_dir(model, hash_files=True)
    entries = {entry.path: entry for entry in info.files}
    assert entries["config.json"].sha256 is not None
    assert len(entries["config.json"].sha256 or "") == 64
    assert entries["model.safetensors"].sha256 is not None


# ---------------------------------------------------------------------------
# Import hygiene
# ---------------------------------------------------------------------------


def test_importing_model_manager_does_not_import_torch() -> None:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(REPO_ROOT / "src")
    code = (
        "import sys; import fsociety.core.model_manager; "
        "assert 'torch' not in sys.modules, 'torch was imported'; "
        "assert 'transformers' not in sys.modules, 'transformers was imported'; "
        "assert 'huggingface_hub' not in sys.modules, 'hub was imported eagerly'; "
        "print('ok')"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=environment,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "ok" in completed.stdout


def test_inspect_local_dir_rejects_missing_and_files(tmp_path: Path) -> None:
    with pytest.raises(NotFoundError):
        inspect_local_dir(tmp_path / "absent")
    file_path = tmp_path / "a-file"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(ValidationError):
        inspect_local_dir(file_path)
