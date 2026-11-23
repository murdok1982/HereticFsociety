# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""CLI integration tests: real subprocess, JSON output, exit codes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run_cli(
    *args: str, workspace: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = {
        "PYTHONPATH": str(ROOT / "src"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "PYTHONUNBUFFERED": "1",
    }
    command = [sys.executable, "-m", "fsociety.interfaces.cli.main"]
    if workspace is not None:
        command += ["--workspace", str(workspace)]
    command += list(args)
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=env,
        timeout=120,
    )


def test_version_json(workspace: Path) -> None:
    result = run_cli("--json", "version", workspace=workspace)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["version"] == "3.0.0"
    assert payload["plugin_api_version"] == 1


def test_doctor_json(workspace: Path) -> None:
    result = run_cli("--json", "doctor", workspace=workspace)
    payload = json.loads(result.stdout)
    checks = {check["check"]: check for check in payload["checks"]}
    assert checks["workspace"]["status"] == "ok"
    assert checks["database"]["status"] == "ok"
    assert checks["hardware"]["status"] in {"ok", "warn"}


def test_hardware_json(workspace: Path) -> None:
    result = run_cli("--json", "hardware", workspace=workspace)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["fingerprint"]
    assert "backends" in payload


def test_models_and_estimate(workspace: Path, tiny_model: tuple[Path, int]) -> None:
    model_dir, parameter_count = tiny_model
    target = workspace / "models" / "tiny"
    target.parent.mkdir(parents=True, exist_ok=True)
    model_dir.rename(target)

    listed = run_cli("--json", "models", "list", workspace=workspace)
    assert listed.returncode == 0, listed.stderr
    items = json.loads(listed.stdout)
    assert any(item["parameter_count"] == parameter_count for item in items)

    estimate = run_cli(
        "--json",
        "models",
        "estimate",
        str(target),
        "--sequence-length",
        "256",
        workspace=workspace,
    )
    assert estimate.returncode == 0, estimate.stderr
    body = json.loads(estimate.stdout)
    assert body["kind"] == "estimate"
    assert body["breakdown"]["total_bytes"] > 0


def test_optimize_persists_experiment(
    workspace: Path, tiny_model: tuple[Path, int]
) -> None:
    model_dir, _ = tiny_model
    space = {
        "dimensions": [
            {
                "name": "dtype",
                "kind": "categorical",
                "choices": ["float32", "bfloat16"],
            },
            {"name": "batch_size", "kind": "int", "low": 1, "high": 4},
        ]
    }
    space_path = workspace / "space.json"
    space_path.write_text(json.dumps(space), encoding="utf-8")
    result = run_cli(
        "--json",
        "optimize",
        "run",
        "--model",
        str(model_dir),
        "--space",
        str(space_path),
        "--trials",
        "3",
        "--seed",
        "5",
        workspace=workspace,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["experiment_id"]
    assert len(payload["trials"]) == 3
    assert payload["model_id"]

    experiments = run_cli("--json", "experiments", "list", workspace=workspace)
    assert experiments.returncode == 0
    items = json.loads(experiments.stdout)
    assert any(item["experiment_id"] == payload["experiment_id"] for item in items)


def test_security_policy_and_sbom(workspace: Path) -> None:
    policy = run_cli("--json", "security", "policy", workspace=workspace)
    assert policy.returncode == 0, policy.stderr
    body = json.loads(policy.stdout)
    assert body["trust_remote_code"] == "never"
    assert "warnings" in body

    sbom_path = workspace / "sbom.json"
    sbom = run_cli(
        "--json", "security", "sbom", "--output", str(sbom_path), workspace=workspace
    )
    assert sbom.returncode == 0, sbom.stderr
    payload = json.loads(sbom.stdout)
    assert payload["bomFormat"] == "CycloneDX"
    assert payload["components"]


def test_config_init(workspace: Path) -> None:
    config = workspace / "fsociety.toml"
    result = run_cli("config", "init", "--path", str(config), workspace=workspace)
    assert result.returncode == 0, result.stderr
    assert config.is_file()
    assert "trust_remote_code" in config.read_text(encoding="utf-8")
    again = run_cli("config", "init", "--path", str(config), workspace=workspace)
    assert again.returncode == 1
