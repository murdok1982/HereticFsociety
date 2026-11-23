# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Platform configuration: TOML file + ``FSOCIETY_*`` environment overrides.

Precedence (lowest to highest): defaults → TOML file → environment variables.
The Heretic engine keeps its own ``config.toml``/``HERETIC_CONFIG_FILE``
contract; this settings object only governs the platform layer.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .errors import ValidationError

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]


def default_workspace() -> Path:
    env = os.environ.get("FSOCIETY_WORKSPACE")
    if env:
        return Path(env).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", str(Path.home()))
    elif os.uname().sysname == "Darwin":  # type: ignore[attr-defined]
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    return Path(base) / "fsociety"


class PlatformSettings(BaseSettings):
    """Validated platform configuration.

    All fields can be overridden with ``FSOCIETY_<UPPER_SNAKE_CASE>`` variables
    (for example ``FSOCIETY_API_PORT=9000``). Nested objects use ``__``.
    """

    model_config = SettingsConfigDict(
        env_prefix="FSOCIETY_",
        env_nested_delimiter="__",
        extra="ignore",
        validate_default=True,
    )

    # ---- paths -------------------------------------------------------------
    workspace: Path = Field(default_factory=default_workspace)
    models_dir: Path | None = None
    hf_cache_dir: Path | None = None
    config_file: Path | None = Field(default=None, exclude=True)

    # ---- engine defaults ---------------------------------------------------
    default_dtype: Literal["auto", "float32", "float16", "bfloat16"] = "auto"
    default_quantization: Literal["none", "bnb_4bit"] = "none"
    default_sequence_length: int = Field(default=2048, ge=16)
    default_batch_size: int = Field(default=1, ge=1)
    offline: bool = False

    # ---- evaluation --------------------------------------------------------
    evaluation_seed: int = Field(default=42, ge=0)
    max_new_tokens: int = Field(default=256, ge=1, le=32768)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)

    # ---- security ----------------------------------------------------------
    trust_remote_code: Literal["never", "allowlist", "always"] = "never"
    trusted_repositories: list[str] = Field(default_factory=list)
    plugin_isolation: bool = True
    plugin_timeout_seconds: int = Field(default=30, ge=1, le=3600)
    plugin_allow_network: bool = False
    plugin_max_output_bytes: int = Field(default=1_000_000, ge=1024)
    allow_absolute_model_paths: bool = True

    # ---- API ---------------------------------------------------------------
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    api_key: SecretStr | None = None
    api_require_key_for_loopback: bool = False
    api_cors_origins: list[str] = Field(default_factory=list)
    api_allowed_hosts: list[str] = Field(default_factory=list)
    api_rate_limit_per_minute: int = Field(default=120, ge=1)
    api_max_upload_bytes: int = Field(default=1_000_000, ge=1024)
    api_docs_enabled: bool = True
    serve_frontend: bool = True
    frontend_dist: Path | None = None

    # ---- logging -----------------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    audit_log_enabled: bool = True

    @field_validator("api_cors_origins")
    @classmethod
    def _validate_origins(cls, value: list[str]) -> list[str]:
        for origin in value:
            if origin == "*":
                raise ValueError(
                    "Wildcard CORS origin is not allowed; list explicit origins."
                )
            if not (origin.startswith("http://") or origin.startswith("https://")):
                raise ValueError(f"Invalid CORS origin: {origin!r}")
        return value

    @field_validator("trusted_repositories")
    @classmethod
    def _validate_repositories(cls, value: list[str]) -> list[str]:
        for repo in value:
            if repo.count("/") != 1 or repo.startswith("/") or ".." in repo:
                raise ValueError(
                    f"Trusted repository must look like 'org/name', got {repo!r}"
                )
        return value

    # ---- derived -----------------------------------------------------------
    def resolved_models_dir(self) -> Path:
        return (self.models_dir or (self.workspace / "models")).expanduser()

    def resolved_hf_cache(self) -> Path:
        if self.hf_cache_dir:
            return self.hf_cache_dir.expanduser()
        env = os.environ.get("HF_HOME")
        if env:
            return Path(env).expanduser() / "hub"
        env = os.environ.get("HUGGINGFACE_HUB_CACHE")
        if env:
            return Path(env).expanduser()
        return Path.home() / ".cache" / "huggingface" / "hub"

    def remote_code_allowed(self, repo_id: str) -> bool:
        """Explicit trust decision for ``trust_remote_code``."""
        if self.trust_remote_code == "never":
            return False
        if self.trust_remote_code == "always":
            return True
        return repo_id in self.trusted_repositories

    def api_key_value(self) -> str | None:
        return self.api_key.get_secret_value() if self.api_key else None

    def redacted(self) -> dict[str, Any]:
        data = self.model_dump(mode="json", exclude={"api_key", "config_file"})
        data["api_key_configured"] = self.api_key is not None
        return data


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ValidationError(
            f"Configuration file not found: {path}",
            hint="Create one with `fsociety config init` or omit --config.",
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise ValidationError(f"Invalid TOML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValidationError(f"Configuration root must be a table: {path}")
    # Allow both flat keys and a [fsociety] table.
    section = data.get("fsociety")
    if isinstance(section, dict):
        merged = {k: v for k, v in data.items() if k != "fsociety"}
        merged.update(section)
        return merged
    return data


def load_settings(
    config_path: str | Path | None = None,
    **overrides: Any,
) -> PlatformSettings:
    """Load settings from TOML + environment + explicit overrides."""
    explicit = config_path or os.environ.get("FSOCIETY_CONFIG")
    values: dict[str, Any] = {}
    resolved_path: Path | None = None
    if explicit:
        resolved_path = Path(explicit).expanduser()
        values.update(_read_toml(resolved_path))
    else:
        default = Path("fsociety.toml")
        if default.is_file():
            resolved_path = default.resolve()
            values.update(_read_toml(resolved_path))

    if resolved_path is not None:
        values["config_file"] = resolved_path
    values.update({k: v for k, v in overrides.items() if v is not None})
    return PlatformSettings(**values)


def write_default_config(path: Path, settings: PlatformSettings | None = None) -> Path:
    """Write a commented, secret-free starter configuration."""
    settings = settings or PlatformSettings()
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = f"""# FSOCIETY AI configuration
# Precedence: defaults < this file < FSOCIETY_* environment variables.

[fsociety]
workspace = "{settings.workspace}"
default_dtype = "auto"
default_quantization = "none"
default_sequence_length = {settings.default_sequence_length}
offline = false

# Evaluation defaults (recorded in every report for reproducibility)
evaluation_seed = {settings.evaluation_seed}
max_new_tokens = {settings.max_new_tokens}
temperature = {settings.temperature}

# Security: never enable trust_remote_code globally.
# Use trust_remote_code = "allowlist" + an explicit list of org/repo ids.
trust_remote_code = "never"
trusted_repositories = []
plugin_isolation = true
plugin_timeout_seconds = {settings.plugin_timeout_seconds}
plugin_allow_network = false

# API binds to loopback by default. Set FSOCIETY_API_KEY before exposing it.
api_host = "127.0.0.1"
api_port = {settings.api_port}
api_cors_origins = []
api_rate_limit_per_minute = {settings.api_rate_limit_per_minute}
serve_frontend = true
"""
    path.write_text(content, encoding="utf-8")
    return path
