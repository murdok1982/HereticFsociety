# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Adversarial trust-policy tests: exact repo matching and engine refusal."""

from __future__ import annotations

from pathlib import Path

import pytest

from fsociety.config import PlatformSettings
from fsociety.core.engine import (
    HereticEngineAdapter,
    trust_decision,
)
from fsociety.errors import TrustPolicyError, ValidationError
from fsociety.security.policy import TrustPolicy


def _policy(tmp_path: Path, **overrides) -> TrustPolicy:
    settings = PlatformSettings(workspace=tmp_path / "workspace", **overrides)
    return TrustPolicy(settings)


def test_default_policy_is_never(tmp_path: Path) -> None:
    settings = PlatformSettings(workspace=tmp_path / "workspace")
    assert settings.trust_remote_code == "never"
    assert settings.remote_code_allowed("org/model") is False
    assert settings.trusted_repositories == []


def test_remote_code_is_denied_unless_requested(tmp_path: Path) -> None:
    policy = _policy(tmp_path, trust_remote_code="always")
    assert policy.ensure_remote_code("org/model", False) is False


@pytest.mark.parametrize(
    "repo",
    [
        "org/model-extra",
        "org/Model",
        "ORG/model",
        "org",
        "org/model/extra",
        "org/../model",
        "org/mo del",
        "org/model\x00",
        "x" * 201,
    ],
)
def test_allowlist_requires_the_exact_repository_id(tmp_path: Path, repo: str) -> None:
    policy = _policy(
        tmp_path,
        trust_remote_code="allowlist",
        trusted_repositories=["org/model"],
    )
    with pytest.raises(TrustPolicyError):
        policy.ensure_remote_code(repo, True)


def test_allowlist_normalizes_surrounding_whitespace(tmp_path: Path) -> None:
    policy = _policy(
        tmp_path,
        trust_remote_code="allowlist",
        trusted_repositories=["org/model"],
    )
    # Whitespace is stripped before the exact comparison; it cannot smuggle a
    # different repository past the allowlist.
    assert policy.ensure_remote_code(" org/model ", True) is True
    with pytest.raises(TrustPolicyError):
        policy.ensure_remote_code(" org/other ", True)


def test_allowlist_accepts_only_the_exact_id(tmp_path: Path) -> None:
    policy = _policy(
        tmp_path,
        trust_remote_code="allowlist",
        trusted_repositories=["org/model"],
    )
    assert policy.ensure_remote_code("org/model", True) is True


def test_empty_allowlist_denies_everything(tmp_path: Path) -> None:
    policy = _policy(tmp_path, trust_remote_code="allowlist")
    with pytest.raises(TrustPolicyError):
        policy.ensure_remote_code("org/model", True)
    assert any("empty allowlist" in warning for warning in policy.warnings())


@pytest.mark.parametrize(
    "repository",
    ["org", "org/model/extra", "/model", "../model", "org/../model", "org\\model"],
)
def test_settings_reject_malformed_trusted_repositories(
    tmp_path: Path, repository: str
) -> None:
    with pytest.raises(ValueError):
        PlatformSettings(
            workspace=tmp_path / "workspace",
            trust_remote_code="allowlist",
            trusted_repositories=[repository],
        )


def test_trust_decision_matches_policy(tmp_path: Path) -> None:
    never = trust_decision(
        "org/model", PlatformSettings(workspace=tmp_path / "workspace")
    )
    assert never["allowed"] is False
    assert never["policy"] == "never"

    allowlist = trust_decision(
        "org/model",
        PlatformSettings(
            workspace=tmp_path / "workspace",
            trust_remote_code="allowlist",
            trusted_repositories=["org/model"],
        ),
    )
    assert allowlist["allowed"] is True
    assert allowlist["allowlisted"] is True

    denied = trust_decision(
        "org/other",
        PlatformSettings(
            workspace=tmp_path / "workspace",
            trust_remote_code="allowlist",
            trusted_repositories=["org/model"],
        ),
    )
    assert denied["allowed"] is False


@pytest.mark.parametrize(
    "args",
    [
        ["--trust-remote-code"],
        ["--trust_remote_code=1"],
        ["org/model", "--trust-remote-code"],
    ],
)
def test_engine_adapter_refuses_remote_code_flags(
    tmp_path: Path, args: list[str]
) -> None:
    adapter = HereticEngineAdapter(
        PlatformSettings(workspace=tmp_path / "workspace"),
        python_executable="/bin/true",
    )
    with pytest.raises(TrustPolicyError):
        adapter.validate_invocation(args)


@pytest.mark.parametrize(
    "args",
    [
        ["org/model; rm -rf /"],
        ["org/model`id`"],
        ["org/model", "$(id)"],
        ["org/model", "../escape"],
        ["org/model", "bad\x00arg"],
        [""],
    ],
)
def test_engine_adapter_rejects_shell_and_path_metacharacters(
    tmp_path: Path, args: list[str]
) -> None:
    adapter = HereticEngineAdapter(
        PlatformSettings(workspace=tmp_path / "workspace"),
        python_executable="/bin/true",
    )
    with pytest.raises(ValidationError):
        adapter.validate_invocation(args)


def test_engine_adapter_accepts_a_normal_repository(tmp_path: Path) -> None:
    adapter = HereticEngineAdapter(
        PlatformSettings(workspace=tmp_path / "workspace"),
        python_executable="/bin/true",
    )
    adapter.validate_invocation(["org/model", "--batch-size", "4"])


def test_engine_config_never_enables_shell_or_remote_code(tmp_path: Path) -> None:
    adapter = HereticEngineAdapter(
        PlatformSettings(workspace=tmp_path / "workspace"),
        python_executable="/bin/true",
    )
    target = adapter.write_platform_config(
        tmp_path / "workspace" / "engine" / "heretic.toml",
        "org/model",
    )
    document = target.read_text(encoding="utf-8")
    assert "enable_shell = false" in document
    assert "trust_remote_code" not in document
    assert "org/model" in document


def test_engine_config_rejects_unknown_extra_keys(tmp_path: Path) -> None:
    adapter = HereticEngineAdapter(
        PlatformSettings(workspace=tmp_path / "workspace"),
        python_executable="/bin/true",
    )
    with pytest.raises(ValidationError):
        adapter.write_platform_config(
            tmp_path / "workspace" / "engine" / "heretic.toml",
            "org/model",
            extra={"trust_remote_code": True},
        )


def test_engine_status_never_claims_availability_without_heretic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib.util

    if importlib.util.find_spec("heretic") is not None:
        pytest.skip("Heretic is importable in this profile.")
    adapter = HereticEngineAdapter(
        PlatformSettings(workspace=tmp_path / "workspace"),
        python_executable="/bin/true",
    )
    status = adapter.status()
    assert status["available"] is False
    assert status["message"]
