# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Optional adapter for EleutherAI's ``lm-evaluation-harness``.

This module never fabricates results. It builds a real ``python -m lm_eval``
command, executes it as a subprocess, and returns the harness' own JSON output
together with provenance (harness version, tasks, few-shot setting, commit).

The harness is an optional dependency; when it is absent the adapter raises
:class:`EngineUnavailableError` with an actionable hint. This integration is not
exercised in CI because it requires model weights and network access.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from ..config import PlatformSettings
from ..errors import (
    EngineUnavailableError,
    EvaluationError,
    TrustPolicyError,
    ValidationError,
)
from ..security.redaction import redact

DEFAULT_TIMEOUT_SECONDS = 3600
_TASK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/\-]*$")
_INSTALL_HINT = (
    "Install the evaluation extra: `pip install 'lm-eval[hf]'` (or `uv sync` in a "
    "source checkout) and re-run."
)


def lm_eval_available() -> bool:
    """True when the ``lm_eval`` module can be imported."""
    return importlib.util.find_spec("lm_eval") is not None


def harness_version() -> str | None:
    """Installed harness version, if reported by the distribution metadata."""
    for distribution in ("lm-eval", "lm_eval"):
        try:
            return importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            continue
    return None


def _validate_tasks(tasks: list[str]) -> list[str]:
    if not isinstance(tasks, (list, tuple)) or not tasks:
        raise ValidationError("tasks must be a non-empty list of harness task names.")
    cleaned: list[str] = []
    for task in tasks:
        if not isinstance(task, str) or not _TASK_RE.match(task):
            raise ValidationError(
                f"Invalid lm-eval task name: {task!r}",
                details={"allowed_pattern": _TASK_RE.pattern},
            )
        cleaned.append(task)
    return cleaned


def _validate_positive_int(value: Any, name: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValidationError(f"{name} must be an integer >= {minimum}.")
    return value


def build_command(
    model_id: str,
    tasks: list[str],
    *,
    num_fewshot: int = 0,
    batch_size: int = 1,
    device: str = "cpu",
    output_path: str | Path = "lm_eval_results",
    limit: int | None = None,
    seed: int | None = None,
    trust_remote_code: bool = False,
    model_args: dict[str, str] | None = None,
) -> list[str]:
    """Build the exact subprocess argv for ``lm-evaluation-harness``."""
    if not isinstance(model_id, str) or not model_id.strip():
        raise ValidationError("model_id must be a non-empty string.")
    cleaned_tasks = _validate_tasks(tasks)
    _validate_positive_int(num_fewshot, "num_fewshot", 0)
    _validate_positive_int(batch_size, "batch_size", 1)
    if limit is not None:
        _validate_positive_int(limit, "limit", 1)
    if seed is not None:
        _validate_positive_int(seed, "seed", 0)
    if not isinstance(device, str) or not device.strip():
        raise ValidationError("device must be a non-empty string.")

    model_arg_parts = [f"pretrained={model_id.strip()}"]
    extras = dict(model_args or {})
    if trust_remote_code:
        extras["trust_remote_code"] = "True"
    for key, value in sorted(extras.items()):
        if not re.fullmatch(r"[A-Za-z0-9_]+", key):
            raise ValidationError(f"Invalid model_args key: {key!r}")
        if any(character in str(value) for character in (",", "\n", "\r")):
            raise ValidationError(
                f"model_args value for {key!r} must not contain commas or newlines."
            )
        model_arg_parts.append(f"{key}={value}")

    command = [
        sys.executable,
        "-m",
        "lm_eval",
        "--model",
        "hf",
        "--model_args",
        ",".join(model_arg_parts),
        "--tasks",
        ",".join(cleaned_tasks),
        "--num_fewshot",
        str(num_fewshot),
        "--batch_size",
        str(batch_size),
        "--device",
        device.strip(),
        "--output_path",
        str(output_path),
    ]
    if limit is not None:
        command.extend(["--limit", str(limit)])
    if seed is not None:
        command.extend(["--seed", str(seed)])
    return command


def _newest_results_file(directory: Path) -> Path | None:
    candidates = sorted(
        directory.rglob("results_*.json"), key=lambda path: path.stat().st_mtime
    )
    return candidates[-1] if candidates else None


def _extract_provenance(
    payload: dict[str, Any],
    results_file: Path,
    requested_tasks: list[str],
    num_fewshot: int,
    model_id: str,
    command: list[str],
    trust_remote_code: bool,
) -> dict[str, Any]:
    configs = payload.get("configs") if isinstance(payload.get("configs"), dict) else {}
    fewshot_by_task: dict[str, Any] = {}
    for task in requested_tasks:
        config = configs.get(task) if isinstance(configs, dict) else None
        if isinstance(config, dict):
            fewshot_by_task[task] = config.get("num_fewshot", num_fewshot)
    return {
        "harness": "lm-evaluation-harness",
        "harness_version": harness_version(),
        "model_id": payload.get("model_name") or model_id,
        "requested_tasks": list(requested_tasks),
        "evaluated_tasks": sorted(
            payload.get("results", {}).keys()
            if isinstance(payload.get("results"), dict)
            else []
        ),
        "num_fewshot": num_fewshot,
        "num_fewshot_by_task": fewshot_by_task,
        "git_hash": payload.get("git_hash"),
        "date": payload.get("date"),
        "trust_remote_code": trust_remote_code,
        "output_file": str(results_file),
        "command": redact(" ".join(command)),
    }


def run_lm_eval(
    model_id: str,
    tasks: list[str],
    *,
    num_fewshot: int = 0,
    batch_size: int = 1,
    device: str = "cpu",
    limit: int | None = None,
    seed: int = 42,
    settings: PlatformSettings | None = None,
    output_dir: str | Path | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Run the harness and return its results plus provenance.

    Raises :class:`EngineUnavailableError` when ``lm_eval`` is not installed and
    :class:`EvaluationError` when the harness fails or emits unreadable output.
    Never returns fabricated scores.
    """
    if not lm_eval_available():
        raise EngineUnavailableError(
            "lm-evaluation-harness is not installed; the adapter cannot run.",
            details={"missing": ["lm_eval"], "tasks": list(tasks) if tasks else []},
            hint=_INSTALL_HINT,
        )
    _validate_tasks(tasks)
    _validate_positive_int(timeout, "timeout", 1)

    resolved_settings = settings or PlatformSettings()
    # The platform trust policy is authoritative: the harness only receives
    # trust_remote_code=True when settings explicitly allow this repo id.
    trust = resolved_settings.remote_code_allowed(model_id)

    target_dir = (
        Path(output_dir).expanduser()
        if output_dir is not None
        else Path(tempfile.mkdtemp(prefix="fsociety-lm-eval-"))
    )
    target_dir.mkdir(parents=True, exist_ok=True)

    command = build_command(
        model_id,
        tasks,
        num_fewshot=num_fewshot,
        batch_size=batch_size,
        device=device,
        output_path=target_dir,
        limit=limit,
        seed=seed,
        trust_remote_code=trust,
    )
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise EvaluationError(
            f"lm-evaluation-harness timed out after {timeout} seconds.",
            details={"tasks": list(tasks), "model_id": model_id},
            hint="Reduce the task set, raise the timeout, or run the harness manually.",
        ) from exc
    except OSError as exc:
        raise EvaluationError(
            f"Could not start lm-evaluation-harness: {exc}",
            details={"command": redact(" ".join(command))},
            hint="Verify the current Python environment can run `python -m lm_eval`.",
        ) from exc

    if completed.returncode != 0:
        stderr_tail = redact((completed.stderr or completed.stdout or "")[-2_000:])
        raise EvaluationError(
            f"lm-evaluation-harness exited with code {completed.returncode}.",
            details={
                "returncode": completed.returncode,
                "stderr_tail": stderr_tail,
                "tasks": list(tasks),
            },
            hint="Inspect the harness output above; fix model access or task names.",
        )

    results_file = _newest_results_file(target_dir)
    if results_file is None:
        raise EvaluationError(
            "lm-evaluation-harness completed but produced no results_*.json file.",
            details={"output_dir": str(target_dir), "tasks": list(tasks)},
            hint="Check the harness logs; it may have failed to write its output.",
        )
    try:
        payload = json.loads(results_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvaluationError(
            f"Could not parse harness results file {results_file.name}: {exc}",
            details={"path": str(results_file)},
        ) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), dict):
        raise EvaluationError(
            "Harness results file has an unexpected shape.",
            details={"path": str(results_file)},
        )
    if not payload["results"]:
        raise EvaluationError(
            "Harness produced an empty results object; refusing to report scores.",
            details={"path": str(results_file), "tasks": list(tasks)},
        )

    provenance = _extract_provenance(
        payload, results_file, list(tasks), num_fewshot, model_id, command, trust
    )
    return {
        "provenance": provenance,
        "results": payload["results"],
        "n_samples": payload.get("n-samples", {}),
        "raw_output": {
            "stdout_tail": redact((completed.stdout or "")[-4_000:]),
            "stderr_tail": redact((completed.stderr or "")[-4_000:]),
        },
    }


def ensure_trust(settings: PlatformSettings, model_id: str, requested: bool) -> bool:
    """Resolve the effective ``trust_remote_code`` flag for the harness.

    Requesting remote code without an explicit allow decision raises
    :class:`TrustPolicyError` instead of silently weakening the policy.
    """
    allowed = settings.remote_code_allowed(model_id)
    if requested and not allowed:
        raise TrustPolicyError(
            f"trust_remote_code was requested for {model_id!r} but the platform policy "
            "denies it.",
            details={
                "model_id": model_id,
                "policy": settings.trust_remote_code,
                "trusted_repositories": list(settings.trusted_repositories),
            },
            hint=(
                'Set trust_remote_code = "allowlist" and add the exact repo id to '
                "trusted_repositories, or run a model that needs no remote code."
            ),
        )
    return allowed


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "build_command",
    "ensure_trust",
    "harness_version",
    "lm_eval_available",
    "run_lm_eval",
]
