# SPDX-License-Identifier: AGPL-3.0-or-later
"""Export bundle and GGUF planning/execution tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fsociety.errors import ExportError
from fsociety.exporters import gguf
from fsociety.exporters.gguf import (
    detect_llama_cpp_tools,
    plan_gguf_export,
    run_gguf_export,
)
from fsociety.exporters.manifest import create_export_bundle, verify_export


def _make_sources(tmp_path: Path) -> list[Path]:
    first = tmp_path / "source-a" / "weights.bin"
    second = tmp_path / "source-b" / "config.json"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b"weights-payload")
    second.write_text('{"model_type": "test"}', encoding="utf-8")
    return [first, second]


def test_create_and_verify_export_bundle(tmp_path: Path) -> None:
    sources = _make_sources(tmp_path)
    destination = tmp_path / "bundle"
    result = create_export_bundle(
        destination,
        files=sources,
        metadata={"model_id": "test/model", "quantization": "none"},
        license_text="AGPL-3.0-or-later",
    )

    assert destination.is_dir()
    assert Path(result["path"]) == destination
    assert result["artifact_id"].startswith("artifact_")
    assert (destination / "manifest.json").is_file()
    assert (destination / "README.md").is_file()
    assert len(result["files"]) == 2

    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["platform"]["version"]
    assert manifest["license"] == "AGPL-3.0-or-later"
    assert all(entry["sha256"] for entry in manifest["files"])

    verification = verify_export(destination)
    assert verification["ok"] is True
    assert verification["files_checked"] == 2
    assert verification["hashes_verified"] == 2
    assert verification["mismatches"] == []
    assert verification["extra"] == []


def test_verify_detects_tampering(tmp_path: Path) -> None:
    sources = _make_sources(tmp_path)
    destination = tmp_path / "bundle"
    create_export_bundle(destination, files=sources, metadata={"k": "v"})
    tampered = destination / "files" / "weights.bin"
    tampered.write_bytes(b"different")

    verification = verify_export(destination)
    assert verification["ok"] is False
    assert verification["mismatches"]


def test_verify_detects_missing_and_extra_files(tmp_path: Path) -> None:
    sources = _make_sources(tmp_path)
    destination = tmp_path / "bundle"
    create_export_bundle(destination, files=sources, metadata={})
    (destination / "files" / "weights.bin").unlink()
    (destination / "files" / "surprise.txt").write_text("extra", encoding="utf-8")

    verification = verify_export(destination)
    assert verification["ok"] is False
    assert verification["missing"] == ["files/weights.bin"]
    assert verification["extra"] == ["files/surprise.txt"]


def test_verify_rejects_traversal_in_manifest(tmp_path: Path) -> None:
    sources = _make_sources(tmp_path)
    destination = tmp_path / "bundle"
    create_export_bundle(destination, files=sources, metadata={})
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"].append({"path": "../../etc/passwd", "sha256": "0" * 64})
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    verification = verify_export(destination)
    assert verification["ok"] is False
    assert any("unsafe path" in entry for entry in verification["mismatches"])


def test_export_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"secret")
    plugin_dir = tmp_path / "inside"
    plugin_dir.mkdir()
    link = plugin_dir / "link.bin"
    link.symlink_to(outside)

    with pytest.raises(ExportError, match="symlink"):
        create_export_bundle(tmp_path / "bundle", files=[link], metadata={"k": "v"})


def test_export_rejects_duplicate_safe_names(tmp_path: Path) -> None:
    first = tmp_path / "one" / "same.bin"
    second = tmp_path / "two" / "same.bin"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    with pytest.raises(ExportError, match="Duplicate"):
        create_export_bundle(tmp_path / "bundle", files=[first, second], metadata={})


def test_export_refuses_existing_destination(tmp_path: Path) -> None:
    sources = _make_sources(tmp_path)
    destination = tmp_path / "bundle"
    destination.mkdir()
    with pytest.raises(ExportError, match="already exists"):
        create_export_bundle(destination, files=sources, metadata={})


def test_export_file_size_cap(tmp_path: Path) -> None:
    sources = _make_sources(tmp_path)
    with pytest.raises(ExportError, match="cap"):
        create_export_bundle(
            tmp_path / "bundle",
            files=sources,
            metadata={},
            max_file_bytes=2,
        )


# ---------------------------------------------------------------------------
# GGUF boundary
# ---------------------------------------------------------------------------


def test_detect_tools_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gguf.shutil, "which", lambda name: None)
    monkeypatch.setattr(gguf, "_resolve_convert_script", lambda: None)
    tools = detect_llama_cpp_tools()
    assert set(tools) >= {
        "llama_quantize",
        "llama_cli",
        "convert_script",
        "llama_cpp_python",
        "available",
    }
    assert tools["available"] is False


def test_plan_gguf_quantization_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF")
    monkeypatch.setattr(
        gguf,
        "detect_llama_cpp_tools",
        lambda: {
            "llama_quantize": "/usr/bin/llama-quantize",
            "llama_cli": None,
            "convert_script": None,
            "llama_cpp_python": False,
            "available": True,
            "hint": None,
        },
    )
    plan = plan_gguf_export(model, tmp_path / "out.gguf", "Q4_K_M")
    assert plan["available"] is True
    assert plan["input_kind"] == "gguf"
    assert plan["argv"] == [
        "/usr/bin/llama-quantize",
        str(model),
        str(tmp_path / "out.gguf"),
        "Q4_K_M",
    ]


def test_plan_hf_directory_needs_converter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_dir = tmp_path / "hf"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        gguf,
        "detect_llama_cpp_tools",
        lambda: {
            "llama_quantize": None,
            "llama_cli": None,
            "convert_script": "/opt/llama.cpp/convert_hf_to_gguf.py",
            "llama_cpp_python": False,
            "available": False,
            "hint": "install",
        },
    )
    plan = plan_gguf_export(model_dir, tmp_path / "out.gguf")
    assert plan["available"] is False
    assert plan["input_kind"] == "hf_directory"
    assert len(plan["steps"]) == 2
    assert plan["steps"][0]["argv"][1] == "/opt/llama.cpp/convert_hf_to_gguf.py"


def test_unknown_quantization_rejected(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF")
    with pytest.raises(ExportError, match="Unsupported"):
        plan_gguf_export(model, tmp_path / "out.gguf", "Q9_FAKE")


def test_gguf_unavailable_raises_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF")
    monkeypatch.setattr(
        gguf,
        "detect_llama_cpp_tools",
        lambda: {
            "llama_quantize": None,
            "llama_cli": None,
            "convert_script": None,
            "llama_cpp_python": False,
            "available": False,
            "hint": "install llama.cpp",
        },
    )
    with pytest.raises(ExportError) as excinfo:
        run_gguf_export(model, tmp_path / "out.gguf")
    assert "no file was produced" in str(excinfo.value)
    assert excinfo.value.hint
    assert not (tmp_path / "out.gguf").exists()
