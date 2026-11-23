# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Unit tests for the Heretic engine adapter.

The engine itself is never executed here: torch is not available in the light
profile, and running a real optimization is far outside a unit test. What is
verified is everything around it: availability probing without torch, argument
validation, generated TOML against the real Heretic settings model, and the
subprocess wiring (no shell, allowlisted environment, config file passed).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from fsociety.config import PlatformSettings
from fsociety.core import engine
from fsociety.core.engine import (
    HereticEngineAdapter,
    build_engine_command,
    engine_status,
    heretic_settings_keys,
    trust_decision,
)
from fsociety.errors import (
    EngineUnavailableError,
    PathSecurityError,
    TrustPolicyError,
    ValidationError,
)

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PlatformSettings:
    monkeypatch.delenv("HERETIC_CONFIG_FILE", raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return PlatformSettings(workspace=workspace, hf_cache_dir=tmp_path / "hf-cache")


@pytest.fixture
def adapter(settings: PlatformSettings) -> HereticEngineAdapter:
    return HereticEngineAdapter(settings)


# ---------------------------------------------------------------------------
# Availability probing
# ---------------------------------------------------------------------------


def test_engine_status_reports_modules_without_torch() -> None:
    status = engine_status()
    assert set(status["modules"]) >= {"torch", "transformers", "huggingface_hub"}
    for module, entry in status["modules"].items():
        assert isinstance(entry["installed"], bool), module
    assert isinstance(status["missing"], list)
    assert isinstance(status["optional_missing"], list)
    assert status["available"] == (
        not status["missing"] and status["heretic"]["installed"]
    )
    assert status["python"]["executable"] == sys.executable
    assert status["message"]


def test_engine_status_does_not_import_torch() -> None:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(REPO_ROOT / "src")
    code = (
        "import sys; import fsociety.core.engine as e; "
        "e.engine_status(); "
        "assert 'torch' not in sys.modules, 'torch was imported'; print('ok')"
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


def test_build_engine_command_never_uses_a_shell() -> None:
    command = build_engine_command(["--help"])
    assert isinstance(command, list)
    assert command[0] == sys.executable
    assert "--help" in command
    if command[1] == "-m":
        assert command[2] == "heretic.main"
    else:
        assert command[1] == "-c"
        assert "heretic.main" in command[2]


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


def test_validate_invocation_accepts_normal_arguments(
    adapter: HereticEngineAdapter,
) -> None:
    adapter.validate_invocation(["--model", "acme/tiny", "--batch-size", "2"])
    adapter.validate_invocation(["--help"])
    adapter.validate_invocation([])


@pytest.mark.parametrize(
    "argument",
    [
        "acme/tiny; rm -rf /",
        "$(whoami)",
        "`id`",
        "a|b",
        "a&b",
        "a\nb",
        "a>b",
        "a*b",
    ],
)
def test_validate_invocation_rejects_shell_metacharacters(
    adapter: HereticEngineAdapter, argument: str
) -> None:
    with pytest.raises(ValidationError):
        adapter.validate_invocation(["--model", argument])


def test_validate_invocation_rejects_remote_code_flags(
    adapter: HereticEngineAdapter,
) -> None:
    with pytest.raises(TrustPolicyError):
        adapter.validate_invocation(["--trust-remote-code"])
    with pytest.raises(TrustPolicyError):
        adapter.validate_invocation(["--trust_remote_code=1"])


def test_validate_invocation_rejects_traversal(adapter: HereticEngineAdapter) -> None:
    with pytest.raises(ValidationError):
        adapter.validate_invocation(["--model", "../../etc/passwd"])
    with pytest.raises(ValidationError):
        adapter.validate_invocation([""])


# ---------------------------------------------------------------------------
# Configuration writing
# ---------------------------------------------------------------------------


def test_write_platform_config_is_valid_heretic_toml(
    adapter: HereticEngineAdapter, settings: PlatformSettings, tmp_path: Path
) -> None:
    config_module = pytest.importorskip("heretic.config")
    target = tmp_path / "engine" / "heretic.toml"
    written = adapter.write_platform_config(
        target,
        "acme/tiny",
        dtype="bfloat16",
        quantization="bnb_4bit",
        batch_size=2,
        max_response_length=512,
        extra={"seed": 7, "system_prompt": "Be brief."},
    )
    assert written == target
    data = tomllib.loads(written.read_text(encoding="utf-8"))

    valid_fields = set(config_module.Settings.model_fields)
    assert set(data) <= valid_fields
    assert data["model"] == "acme/tiny"
    assert data["dtypes"][0] == "bfloat16"
    assert set(data["dtypes"]) == {"auto", "float16", "bfloat16", "float32"}
    assert data["quantization"] == "bnb_4bit"
    assert data["batch_size"] == 2
    assert data["max_response_length"] == 512
    assert data["seed"] == 7
    assert data["system_prompt"] == "Be brief."
    assert data["enable_shell"] is False
    assert data["print_debug_information"] is False
    assert data["device_map"] == "auto"


def test_write_platform_config_defaults(
    adapter: HereticEngineAdapter, settings: PlatformSettings, tmp_path: Path
) -> None:
    written = adapter.write_platform_config(tmp_path / "default.toml", "acme/tiny")
    data = tomllib.loads(written.read_text(encoding="utf-8"))
    assert data["dtypes"][0] == "auto"
    assert data["quantization"] == "none"
    assert data["batch_size"] == 0
    assert data["max_response_length"] == 100
    assert data["seed"] == settings.evaluation_seed
    assert set(heretic_settings_keys()) >= {"model", "dtypes", "max_response_length"}


def test_write_platform_config_rejects_invalid_input(
    adapter: HereticEngineAdapter, tmp_path: Path
) -> None:
    target = tmp_path / "engine" / "heretic.toml"
    with pytest.raises(ValidationError):
        adapter.write_platform_config(target, "acme/tiny", dtype="float8")
    with pytest.raises(ValidationError):
        adapter.write_platform_config(target, "acme/tiny", quantization="int4")
    with pytest.raises(ValidationError):
        adapter.write_platform_config(target, "acme/tiny", batch_size=-1)
    with pytest.raises(ValidationError):
        adapter.write_platform_config(target, "acme/tiny", max_response_length=0)
    with pytest.raises(ValidationError):
        adapter.write_platform_config(target, "acme/tiny; rm -rf /")
    with pytest.raises(ValidationError):
        adapter.write_platform_config(target, "acme/tiny", extra={"nope": 1})
    with pytest.raises(ValidationError):
        adapter.write_platform_config(target, "acme/tiny", extra={"model": "other"})
    with pytest.raises(ValidationError):
        adapter.write_platform_config(
            target, "acme/tiny", extra={"max_memory": {"0": {"nested": "no"}}}
        )
    with pytest.raises(ValidationError):
        adapter.write_platform_config(target, "acme/tiny", extra={"max_shard_size": 0})
    assert not target.exists()


def test_write_platform_config_jails_relative_paths(
    adapter: HereticEngineAdapter, settings: PlatformSettings, tmp_path: Path
) -> None:
    written = adapter.write_platform_config("engine/relative.toml", "acme/tiny")
    assert written == (settings.workspace / "engine" / "relative.toml").resolve()
    assert written.is_file()
    with pytest.raises(PathSecurityError):
        adapter.write_platform_config("../escape.toml", "acme/tiny")


def test_heretic_config_path_uses_environment(
    adapter: HereticEngineAdapter,
    settings: PlatformSettings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    override = tmp_path / "custom" / "config.toml"
    monkeypatch.setenv("HERETIC_CONFIG_FILE", str(override))
    assert adapter.heretic_config_path() == override
    monkeypatch.delenv("HERETIC_CONFIG_FILE")
    assert adapter.heretic_config_path() == (
        settings.workspace / "engine" / "heretic.toml"
    )


# ---------------------------------------------------------------------------
# Subprocess execution
# ---------------------------------------------------------------------------


def test_run_raises_when_engine_is_unavailable(
    adapter: HereticEngineAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = engine_status()
    monkeypatch.setattr(
        engine,
        "engine_status",
        lambda: {
            **real,
            "available": False,
            "missing": ["torch"],
            "message": "engine unavailable",
            "hint": "install torch",
        },
    )
    with pytest.raises(EngineUnavailableError) as excinfo:
        adapter.run(["--help"], timeout=5)
    assert "torch" in excinfo.value.details["missing"]


def test_run_uses_no_shell_and_allowlisted_environment(
    adapter: HereticEngineAdapter,
    monkeypatch: pytest.MonkeyPatch,
    settings: PlatformSettings,
) -> None:
    real = engine_status()
    monkeypatch.setattr(
        engine,
        "engine_status",
        lambda: {**real, "available": True, "missing": []},
    )
    monkeypatch.setenv("UNRELATED_SECRET", "hunter2")
    captured: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(engine.subprocess, "run", fake_run)

    adapter.write_platform_config("engine/heretic.toml", "acme/tiny")
    result = adapter.run(["--help"], timeout=5)

    assert result.returncode == 0
    assert captured["kwargs"]["shell"] is False
    assert captured["command"][0] == sys.executable
    environment = captured["kwargs"]["env"]
    assert environment["PYTHONUNBUFFERED"] == "1"
    assert "UNRELATED_SECRET" not in environment
    expected_config = (settings.workspace / "engine" / "heretic.toml").resolve()
    assert environment["HERETIC_CONFIG_FILE"] == str(expected_config)
    assert captured["kwargs"]["cwd"] == str(expected_config.parent)
    assert captured["kwargs"]["timeout"] == 5


def test_run_rejects_invalid_timeout(
    adapter: HereticEngineAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = engine_status()
    monkeypatch.setattr(
        engine, "engine_status", lambda: {**real, "available": True, "missing": []}
    )
    with pytest.raises(ValidationError):
        adapter.run(["--help"], timeout=0)
    with pytest.raises(ValidationError):
        adapter.run(["--help"], timeout=-3)


# ---------------------------------------------------------------------------
# Trust policy
# ---------------------------------------------------------------------------


def test_trust_decision_follows_platform_policy(tmp_path: Path) -> None:
    never = trust_decision(
        "acme/tiny", PlatformSettings(workspace=tmp_path, trust_remote_code="never")
    )
    assert never["allowed"] is False
    assert never["policy"] == "never"
    assert "disabled" in never["reason"]

    allowlist_settings = PlatformSettings(
        workspace=tmp_path,
        trust_remote_code="allowlist",
        trusted_repositories=["acme/tiny"],
    )
    allowed = trust_decision("acme/tiny", allowlist_settings)
    assert allowed["allowed"] is True
    assert allowed["allowlisted"] is True
    denied = trust_decision("other/model", allowlist_settings)
    assert denied["allowed"] is False
    assert denied["allowlisted"] is False
    assert "not on the trusted allowlist" in denied["reason"]

    always = trust_decision(
        "acme/tiny", PlatformSettings(workspace=tmp_path, trust_remote_code="always")
    )
    assert always["allowed"] is True
    assert "unsafe" in always["reason"]

    adapter = HereticEngineAdapter(allowlist_settings)
    assert adapter.trust_decision("acme/tiny")["allowed"] is True
