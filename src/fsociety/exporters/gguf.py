# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Honest llama.cpp export boundary.

FSOCIETY never synthesises a GGUF file. It can only build a plan and execute the
real ``convert_hf_to_gguf.py`` / ``llama-quantize`` tools when they are present.
When they are absent the caller gets an actionable :class:`ExportError` instead
of a fake artifact.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..errors import ExportError
from ..security.redaction import redact

#: Quantization types accepted by llama-quantize (upper/lower case tolerated).
SUPPORTED_QUANTIZATIONS: frozenset[str] = frozenset(
    {
        "Q2_K",
        "Q3_K_S",
        "Q3_K_M",
        "Q3_K_L",
        "Q4_0",
        "Q4_K_S",
        "Q4_K_M",
        "Q5_0",
        "Q5_K_S",
        "Q5_K_M",
        "Q6_K",
        "Q8_0",
        "F16",
        "BF16",
        "F32",
    }
)

_INSTALL_HINT = (
    "Install llama.cpp and put `llama-quantize` on PATH; for Hugging Face "
    "directories also provide `convert_hf_to_gguf.py` (set LLAMA_CPP_DIR or "
    "LLAMA_CPP_CONVERT_SCRIPT). `llama-cpp-python` is a runtime, not a converter."
)


def _resolve_convert_script() -> str | None:
    explicit = os.environ.get("LLAMA_CPP_CONVERT_SCRIPT")
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("convert_hf_to_gguf.py")
    if found:
        return found
    roots = []
    env_dir = os.environ.get("LLAMA_CPP_DIR")
    if env_dir:
        roots.append(Path(env_dir).expanduser())
    roots += [Path.cwd() / "llama.cpp", Path.home() / "llama.cpp"]
    for root in roots:
        candidate = root / "convert_hf_to_gguf.py"
        if candidate.is_file():
            return str(candidate)
    return None


def detect_llama_cpp_tools() -> dict[str, Any]:
    """Report which real llama.cpp tools are available on this host."""
    quantize = shutil.which("llama-quantize") or shutil.which("llama_quantize")
    cli = shutil.which("llama-cli") or shutil.which("llama_cli")
    convert_script = _resolve_convert_script()
    try:
        llama_cpp_python = importlib.util.find_spec("llama_cpp") is not None
    except (ImportError, ValueError):
        llama_cpp_python = False
    return {
        "llama_quantize": quantize,
        "llama_cli": cli,
        "convert_script": convert_script,
        "llama_cpp_python": llama_cpp_python,
        "available": bool(quantize),
        "hint": None if quantize else _INSTALL_HINT,
    }


def _normalize_quantization(quantization: str) -> str:
    value = str(quantization).strip().upper()
    if value not in SUPPORTED_QUANTIZATIONS:
        raise ExportError(
            f"Unsupported GGUF quantization {quantization!r}.",
            details={"supported": sorted(SUPPORTED_QUANTIZATIONS)},
            hint="Choose one of the supported llama.cpp quantization types.",
        )
    return value


def plan_gguf_export(
    model_path: str | Path,
    out_path: str | Path,
    quantization: str = "Q4_K_M",
) -> dict[str, Any]:
    """Return the exact argv steps for a real GGUF export.

    ``available`` reflects tool presence only; it never implies the export has
    been performed. ``argv`` is the final quantize command, using the resolved
    binary name or the conventional ``llama-quantize`` fallback in the plan.
    """
    source = Path(model_path).expanduser()
    output = Path(out_path).expanduser()
    quantization_type = _normalize_quantization(quantization)
    if not source.exists():
        raise ExportError(f"Model path does not exist: {source}.")
    if output.exists() and output.resolve() == source.resolve():
        raise ExportError("GGUF output path must differ from the input path.")

    tools = detect_llama_cpp_tools()
    quantize = tools["llama_quantize"] or "llama-quantize"

    if source.is_dir():
        input_kind = "hf_directory"
        if not (source / "config.json").is_file():
            raise ExportError(
                "Hugging Face model directory has no config.json.",
                details={"path": str(source)},
            )
        convert_script = tools["convert_script"] or "convert_hf_to_gguf.py"
        intermediate = output.parent / f"{output.stem}.f16.gguf"
        steps = [
            {
                "kind": "convert",
                "argv": [
                    sys.executable,
                    convert_script,
                    str(source),
                    "--outfile",
                    str(intermediate),
                    "--outtype",
                    "f16",
                ],
            },
            {
                "kind": "quantize",
                "argv": [quantize, str(intermediate), str(output), quantization_type],
            },
        ]
        available = bool(tools["llama_quantize"] and tools["convert_script"])
        required = ["llama_quantize", "convert_script"]
    elif source.is_file() and source.suffix.lower() == ".gguf":
        input_kind = "gguf"
        intermediate = None
        steps = [
            {
                "kind": "quantize",
                "argv": [quantize, str(source), str(output), quantization_type],
            }
        ]
        available = bool(tools["llama_quantize"])
        required = ["llama_quantize"]
    else:
        raise ExportError(
            "GGUF export input must be a Hugging Face directory or a .gguf file.",
            details={"path": str(source)},
        )

    return {
        "model_path": str(source),
        "output_path": str(output),
        "quantization": quantization_type,
        "input_kind": input_kind,
        "tools": tools,
        "available": available,
        "required_tools": required,
        "steps": steps,
        "argv": steps[-1]["argv"],
        "intermediate_path": str(intermediate) if intermediate else None,
        "hint": None if available else _INSTALL_HINT,
    }


def run_gguf_export(
    model_path: str | Path,
    out_path: str | Path,
    quantization: str = "Q4_K_M",
    *,
    timeout_seconds: int = 3600,
) -> dict[str, Any]:
    """Execute a real GGUF export. Raises when tools are missing or fail."""
    if not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
        raise ExportError("GGUF export timeout must be a positive integer.")
    execution = plan_gguf_export(model_path, out_path, quantization)
    if not execution["available"]:
        missing = [
            name
            for name in execution["required_tools"]
            if not execution["tools"].get(name)
        ]
        raise ExportError(
            "GGUF export tools are not available; no file was produced.",
            details={"missing": missing},
            hint=_INSTALL_HINT,
        )

    output = Path(execution["output_path"])
    if output.exists():
        raise ExportError(
            f"GGUF output path already exists: {output.name}.",
            hint="Choose a new output path; exports never overwrite existing artifacts.",
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    completed_steps: list[dict[str, Any]] = []
    try:
        for step in execution["steps"]:
            try:
                completed = subprocess.run(
                    step["argv"],
                    shell=False,
                    capture_output=True,
                    timeout=timeout_seconds,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise ExportError(
                    f"GGUF {step['kind']} step could not run.",
                    details={"error": redact(str(exc))},
                    hint=_INSTALL_HINT,
                ) from exc
            stdout = completed.stdout.decode("utf-8", "replace")[-2000:]
            stderr = completed.stderr.decode("utf-8", "replace")[-2000:]
            completed_steps.append(
                {
                    "kind": step["kind"],
                    "argv": step["argv"],
                    "returncode": completed.returncode,
                    "stdout_tail": redact(stdout),
                    "stderr_tail": redact(stderr),
                }
            )
            if completed.returncode != 0:
                raise ExportError(
                    f"GGUF {step['kind']} step failed with exit code {completed.returncode}.",
                    details={"argv": step["argv"], "stderr": redact(stderr)},
                    hint=_INSTALL_HINT,
                )
    except BaseException:
        # Never leave a partial artifact behind that could be mistaken for a
        # successful export.
        if output.exists():
            output.unlink(missing_ok=True)
        raise

    if not output.is_file() or output.stat().st_size == 0:
        raise ExportError(
            "GGUF export finished but no non-empty output file was produced.",
            details={"output_path": str(output)},
        )

    intermediate = execution.get("intermediate_path")
    if intermediate:
        intermediate_path = Path(intermediate)
        if (
            intermediate_path.exists()
            and intermediate_path.resolve() != output.resolve()
        ):
            intermediate_path.unlink(missing_ok=True)

    return {
        "ok": True,
        "output_path": str(output),
        "size_bytes": output.stat().st_size,
        "quantization": execution["quantization"],
        "input_kind": execution["input_kind"],
        "steps": completed_steps,
        "note": "Generated by the real llama.cpp toolchain; not a synthetic GGUF.",
    }
