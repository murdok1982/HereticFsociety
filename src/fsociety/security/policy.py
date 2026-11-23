# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Explicit trust and API-access policies.

Defaults are deny-by-default:

* ``trust_remote_code`` is refused unless the exact repository id is allowlisted
  (or the operator explicitly selected the warning-laden ``always`` mode).
* Remote API access requires a configured key; loopback binds are locally
  trusted unless ``api_require_key_for_loopback`` is enabled.
* Plugin permissions are checked against a fixed allowlist; network access is a
  separate, explicit grant.
"""

from __future__ import annotations

import hmac
import ipaddress
import re
from typing import Any
from urllib.parse import unquote

from ..config import PlatformSettings
from ..errors import PathSecurityError, TrustPolicyError, ValidationError
from ..plugins.api import ALLOWED_PLUGIN_PERMISSIONS

_REPO_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]*/[A-Za-z0-9][A-Za-z0-9._\-]*$")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]+:")
_DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _validate_repo_id(repo_id: str) -> str:
    if not isinstance(repo_id, str) or not repo_id.strip():
        raise TrustPolicyError("Repository id must be a non-empty string.")
    value = repo_id.strip()
    if len(value) > 200:
        raise TrustPolicyError("Repository id is too long.")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise TrustPolicyError("Repository id contains control characters.")
    if ".." in value or not _REPO_ID_RE.match(value):
        raise TrustPolicyError(
            f"Repository id {repo_id!r} is not a valid 'org/name' identifier.",
            hint="Remote-code trust is keyed by exact repository id.",
        )
    return value


class TrustPolicy:
    """Remote-code and plugin-permission decisions for a settings object."""

    def __init__(self, settings: PlatformSettings) -> None:
        self.settings = settings

    def warnings(self) -> list[str]:
        warnings: list[str] = []
        if self.settings.trust_remote_code == "always":
            warnings.append(
                "trust_remote_code='always' executes arbitrary repository code; "
                "this is not recommended for untrusted models."
            )
        if (
            self.settings.trust_remote_code == "allowlist"
            and not self.settings.trusted_repositories
        ):
            warnings.append(
                "trust_remote_code='allowlist' has an empty allowlist, so every "
                "repository is denied."
            )
        if self.settings.plugin_allow_network:
            warnings.append(
                "plugin_allow_network=true grants plugins outbound network access."
            )
        return warnings

    def ensure_remote_code(self, repo_id: str, requested: bool) -> bool:
        """Return ``True`` when remote code is allowed, else raise.

        ``requested=False`` always returns ``False`` and never grants anything.
        """
        if not requested:
            return False
        value = _validate_repo_id(repo_id)
        mode = self.settings.trust_remote_code
        if mode == "never":
            raise TrustPolicyError(
                f"Remote code for {value!r} is denied by policy.",
                details={"repo_id": value, "mode": mode},
                hint="Set trust_remote_code='allowlist' and add the exact repo id "
                "to trusted_repositories.",
            )
        if mode == "always":
            return True
        if value in self.settings.trusted_repositories:
            return True
        raise TrustPolicyError(
            f"Repository {value!r} is not in the remote-code allowlist.",
            details={"repo_id": value, "mode": mode},
            hint="Add the exact 'org/name' id to trusted_repositories.",
        )

    def check_plugin_permissions(
        self, permissions: list[str], allow_network: bool
    ) -> dict[str, Any]:
        """Validate requested plugin permissions without executing the plugin."""
        if not isinstance(permissions, list) or not all(
            isinstance(permission, str) for permission in permissions
        ):
            raise ValidationError("Plugin permissions must be a list of strings.")
        errors: list[str] = []
        warnings: list[str] = []
        for permission in sorted(set(permissions)):
            if permission not in ALLOWED_PLUGIN_PERMISSIONS:
                errors.append(f"Unknown plugin permission {permission!r}.")
            elif permission == "network" and not allow_network:
                errors.append(
                    "Permission 'network' requires plugin_allow_network=true."
                )
            elif permission == "subprocess":
                warnings.append(
                    "Permission 'subprocess' lets the plugin spawn processes; "
                    "isolation should remain enabled."
                )
            elif permission == "filesystem_write":
                warnings.append(
                    "Permission 'filesystem_write' lets the plugin modify files."
                )
        return {
            "allowed": not errors,
            "permissions": sorted(set(permissions)),
            "errors": errors,
            "warnings": warnings,
            "network_allowed": bool(allow_network),
        }

    def validate_model_reference(self, ref: str) -> str:
        """Reject schemes, traversal, control characters and overlong refs."""
        if not isinstance(ref, str) or not ref:
            raise ValidationError("Model reference must be a non-empty string.")
        if len(ref) > 255:
            raise ValidationError(
                "Model reference is too long (maximum 255 characters)."
            )
        if any(ord(char) < 32 or ord(char) == 127 for char in ref):
            raise ValidationError("Model reference contains control characters.")
        if ref != ref.strip():
            raise ValidationError(
                "Model reference must not have leading/trailing spaces."
            )
        lowered = ref.lower()
        if "%2e" in lowered or "%2f" in lowered or "%5c" in lowered:
            raise PathSecurityError(
                "Model reference contains encoded path traversal characters."
            )
        if "://" in ref:
            raise ValidationError(
                "Model reference must not be a URL.",
                hint="Pass a repository id ('org/name') or a local path.",
            )
        is_absolute = bool(ref.startswith(("/", "\\")) or _DRIVE_PATH_RE.match(ref))
        if is_absolute:
            if not self.settings.allow_absolute_model_paths:
                raise ValidationError(
                    "Absolute model paths are disabled by policy.",
                    hint="Set allow_absolute_model_paths=true to permit them.",
                )
        elif _SCHEME_RE.match(ref):
            raise ValidationError(
                "Model reference must not use a URI scheme.",
                hint="Pass a repository id ('org/name') or a local path.",
            )
        decoded = unquote(ref)
        parts = re.split(r"[\\/]", decoded)
        if ".." in parts:
            raise PathSecurityError(
                "Model reference contains a parent-directory segment.",
                details={"reference": ref},
            )
        return ref

    def summary(self) -> dict[str, Any]:
        loopback = ApiAccessPolicy.is_loopback(self.settings.api_host)
        return {
            "mode": self.settings.trust_remote_code,
            "trust_remote_code": self.settings.trust_remote_code,
            "default_policy": "deny",
            "trusted_repositories": sorted(self.settings.trusted_repositories),
            "remote_code_allowed": self.settings.trust_remote_code != "never",
            "plugin_isolation": self.settings.plugin_isolation,
            "plugin_allow_network": self.settings.plugin_allow_network,
            "plugin_timeout_seconds": self.settings.plugin_timeout_seconds,
            "api_host": self.settings.api_host,
            "api_port": self.settings.api_port,
            "api_key_configured": self.settings.api_key is not None,
            "api_auth_required": (not loopback)
            or self.settings.api_require_key_for_loopback,
            "cors_origins": list(self.settings.api_cors_origins),
            "warnings": self.warnings(),
        }


class ApiAccessPolicy:
    """Loopback/API-key rules. Stateless; callers pass current settings."""

    @staticmethod
    def is_loopback(host: str | None) -> bool:
        if not host or not isinstance(host, str):
            return False
        value = host.strip().lower()
        if value.startswith("["):
            closing = value.find("]")
            value = value[1:closing] if closing >= 0 else value[1:]
        elif value.count(":") == 1 and "." in value:
            value = value.split(":", 1)[0]
        if value in {"localhost", "localhost.localdomain"}:
            return True
        try:
            return ipaddress.ip_address(value.split("%")[0]).is_loopback
        except ValueError:
            return False

    @staticmethod
    def remote_access_allowed(settings: PlatformSettings) -> bool:
        if ApiAccessPolicy.is_loopback(settings.api_host):
            return True
        return settings.api_key_value() is not None

    @staticmethod
    def require_key(host: str, settings: PlatformSettings) -> bool:
        if ApiAccessPolicy.is_loopback(host):
            return bool(settings.api_require_key_for_loopback)
        return True

    @staticmethod
    def verify_key(provided: str | None, settings: PlatformSettings) -> bool:
        configured = settings.api_key_value()
        if not configured or provided is None:
            return False
        getter = getattr(provided, "get_secret_value", None)
        if callable(getter):
            provided = getter()
        if not isinstance(provided, str):
            return False
        return hmac.compare_digest(provided.encode("utf-8"), configured.encode("utf-8"))

    @staticmethod
    def redacted_policy(settings: PlatformSettings) -> dict[str, Any]:
        loopback = ApiAccessPolicy.is_loopback(settings.api_host)
        return {
            "api_host": settings.api_host,
            "api_port": settings.api_port,
            "loopback_only": loopback,
            "remote_access_allowed": ApiAccessPolicy.remote_access_allowed(settings),
            "key_required": (not loopback) or settings.api_require_key_for_loopback,
            "api_key_configured": settings.api_key is not None,
            "cors_origins": list(settings.api_cors_origins),
            "rate_limit_per_minute": settings.api_rate_limit_per_minute,
            "docs_enabled": settings.api_docs_enabled,
        }


def redacted_policy(settings: PlatformSettings) -> dict[str, Any]:
    """Module-level helper used by API/CLI without instantiating a policy."""
    return ApiAccessPolicy.redacted_policy(settings)
