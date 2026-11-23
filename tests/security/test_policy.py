# SPDX-License-Identifier: AGPL-3.0-or-later
"""Trust policy, API access policy and security-header tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from fsociety.config import PlatformSettings
from fsociety.errors import PathSecurityError, TrustPolicyError, ValidationError
from fsociety.security.headers import SECURITY_HEADERS, apply_security_headers
from fsociety.security.policy import (
    ApiAccessPolicy,
    TrustPolicy,
    redacted_policy,
)


def _settings(tmp_path: Path, **overrides: object) -> PlatformSettings:
    values: dict[str, object] = {"workspace": tmp_path / "workspace"}
    values.update(overrides)
    return PlatformSettings(**values)


# ---------------------------------------------------------------------------
# Remote code trust
# ---------------------------------------------------------------------------


def test_trust_never_denies_remote_code(tmp_path: Path) -> None:
    policy = TrustPolicy(_settings(tmp_path, trust_remote_code="never"))
    assert policy.ensure_remote_code("org/model", False) is False
    with pytest.raises(TrustPolicyError):
        policy.ensure_remote_code("org/model", True)


def test_trust_allowlist_requires_exact_repo(tmp_path: Path) -> None:
    policy = TrustPolicy(
        _settings(
            tmp_path,
            trust_remote_code="allowlist",
            trusted_repositories=["org/model"],
        )
    )
    assert policy.ensure_remote_code("org/model", True) is True
    with pytest.raises(TrustPolicyError, match="allowlist"):
        policy.ensure_remote_code("org/other", True)


def test_trust_always_allows_and_warns(tmp_path: Path) -> None:
    policy = TrustPolicy(_settings(tmp_path, trust_remote_code="always"))
    assert policy.ensure_remote_code("org/anything", True) is True
    summary = policy.summary()
    assert summary["mode"] == "always"
    assert summary["warnings"]


def test_trust_rejects_invalid_repository_id(tmp_path: Path) -> None:
    policy = TrustPolicy(_settings(tmp_path, trust_remote_code="always"))
    with pytest.raises(TrustPolicyError):
        policy.ensure_remote_code("../etc/passwd", True)


# ---------------------------------------------------------------------------
# Model references
# ---------------------------------------------------------------------------


def test_model_reference_accepts_repo_id(tmp_path: Path) -> None:
    policy = TrustPolicy(_settings(tmp_path))
    assert policy.validate_model_reference("org/model-7b") == "org/model-7b"


@pytest.mark.parametrize(
    "reference",
    [
        "https://evil.example/model",
        "file:///etc/passwd",
        "../../etc/passwd",
        "org/../../etc/passwd",
        "bad\x00name",
        "x" * 300,
    ],
)
def test_model_reference_rejects_unsafe_values(tmp_path: Path, reference: str) -> None:
    policy = TrustPolicy(_settings(tmp_path))
    with pytest.raises((ValidationError, PathSecurityError)):
        policy.validate_model_reference(reference)


def test_model_reference_absolute_path_policy(tmp_path: Path) -> None:
    strict = TrustPolicy(_settings(tmp_path, allow_absolute_model_paths=False))
    with pytest.raises(ValidationError):
        strict.validate_model_reference("/tmp/model")

    permissive = TrustPolicy(_settings(tmp_path, allow_absolute_model_paths=True))
    assert permissive.validate_model_reference("/tmp/model") == "/tmp/model"


# ---------------------------------------------------------------------------
# Plugin permissions
# ---------------------------------------------------------------------------


def test_plugin_permissions_network_requires_explicit_grant(tmp_path: Path) -> None:
    policy = TrustPolicy(_settings(tmp_path))
    denied = policy.check_plugin_permissions(["network"], allow_network=False)
    assert denied["allowed"] is False
    assert denied["errors"]

    granted = policy.check_plugin_permissions(["network"], allow_network=True)
    assert granted["allowed"] is True
    assert granted["network_allowed"] is True


def test_plugin_permissions_unknown_and_warnings(tmp_path: Path) -> None:
    policy = TrustPolicy(_settings(tmp_path))
    unknown = policy.check_plugin_permissions(["root"], allow_network=False)
    assert unknown["allowed"] is False

    accepted = policy.check_plugin_permissions(
        ["filesystem_read", "subprocess"], allow_network=False
    )
    assert accepted["allowed"] is True
    assert accepted["warnings"]


# ---------------------------------------------------------------------------
# API access
# ---------------------------------------------------------------------------


def test_loopback_detection() -> None:
    assert ApiAccessPolicy.is_loopback("127.0.0.1") is True
    assert ApiAccessPolicy.is_loopback("127.0.0.1:8000") is True
    assert ApiAccessPolicy.is_loopback("localhost") is True
    assert ApiAccessPolicy.is_loopback("::1") is True
    assert ApiAccessPolicy.is_loopback("[::1]:8000") is True
    assert ApiAccessPolicy.is_loopback("10.0.0.5") is False
    assert ApiAccessPolicy.is_loopback("") is False


def test_remote_access_requires_key(tmp_path: Path) -> None:
    closed = _settings(tmp_path, api_host="0.0.0.0", api_key=None)
    assert ApiAccessPolicy.remote_access_allowed(closed) is False

    opened = _settings(tmp_path, api_host="0.0.0.0", api_key="a-very-secret-key")
    assert ApiAccessPolicy.remote_access_allowed(opened) is True


def test_loopback_is_exempt_from_key_requirement(tmp_path: Path) -> None:
    settings = _settings(tmp_path, api_host="127.0.0.1")
    assert ApiAccessPolicy.require_key("127.0.0.1", settings) is False
    assert ApiAccessPolicy.remote_access_allowed(settings) is True


def test_api_key_compare_uses_constant_time(tmp_path: Path) -> None:
    settings = _settings(tmp_path, api_key="correct-key-value")
    assert ApiAccessPolicy.verify_key("correct-key-value", settings) is True
    assert ApiAccessPolicy.verify_key("wrong-key-value", settings) is False
    assert ApiAccessPolicy.verify_key(None, settings) is False


def test_redacted_policy_hides_secret(tmp_path: Path) -> None:
    settings = _settings(tmp_path, api_host="0.0.0.0", api_key="super-secret-value")
    policy = redacted_policy(settings)
    assert "super-secret-value" not in str(policy)
    assert policy["api_key_configured"] is True
    assert policy["key_required"] is True


# ---------------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------------


def test_security_headers_include_csp_and_frame_deny() -> None:
    assert "Content-Security-Policy" in SECURITY_HEADERS
    assert "default-src 'self'" in SECURITY_HEADERS["Content-Security-Policy"]
    assert SECURITY_HEADERS["X-Frame-Options"] == "DENY"
    headers: dict[str, str] = {"X-Custom": "kept"}
    applied = apply_security_headers(headers)
    assert applied["X-Custom"] == "kept"
    assert applied["X-Content-Type-Options"] == "nosniff"
