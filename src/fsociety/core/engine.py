# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Adapter for the preserved Heretic modification engine.

The engine is AGPL software shipped in this distribution. This module never
imports it: it checks availability with :mod:`importlib.util`, writes a valid
Heretic 2.x TOML configuration, and launches ``heretic.main`` in a subprocess
without a shell, with an explicit environment allowlist.

Security invariants:

* no shell is ever used, so metacharacters cannot be interpreted;
* ``trust_remote_code`` is never enabled by the platform;
* the subprocess only sees allowlisted environment variables.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import math
import os
import platform
import re
import subprocess
import sys
from functools import lru_cache
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

from ..config import PlatformSettings
from ..errors import (
    EngineUnavailableError,
    FSocietyError,
    TrustPolicyError,
    ValidationError,
)
from ..logging import AuditLogger
from ..paths import safe_join

__all__ = [
    "ENGINE_DTYPES",
    "ENGINE_QUANTIZATIONS",
    "HereticEngineAdapter",
    "build_engine_command",
    "engine_status",
    "heretic_settings_keys",
    "trust_decision",
]

#: Modules the engine cannot run without, with their distribution name.
REQUIRED_ENGINE_MODULES: tuple[tuple[str, str], ...] = (
    ("torch", "torch"),
    ("transformers", "transformers"),
)

#: Modules that unlock optional engine features but are not required to start.
OPTIONAL_ENGINE_MODULES: tuple[tuple[str, str], ...] = (
    ("accelerate", "accelerate"),
    ("peft", "peft"),
    ("bitsandbytes", "bitsandbytes"),
    ("datasets", "datasets"),
    ("optuna", "optuna"),
    ("lm_eval", "lm-eval"),
    ("safetensors", "safetensors"),
    ("huggingface_hub", "huggingface-hub"),
)

#: dtypes accepted by Heretic's ``dtypes`` setting.
ENGINE_DTYPES = ("auto", "float16", "bfloat16", "float32")

#: Fallback dtype order tried by the engine after the requested dtype.
ENGINE_DTYPE_FALLBACKS = ("auto", "float16", "bfloat16", "float32")

#: Quantization methods supported by Heretic (``QuantizationMethod``).
ENGINE_QUANTIZATIONS = ("none", "bnb_4bit")

#: Flags that would relax the platform's trust policy. Never forwarded.
FORBIDDEN_ENGINE_FLAGS = frozenset({"--trust-remote-code", "--trust_remote_code"})

#: Bootstrap used when ``heretic.main`` has no ``__main__`` guard, because
#: ``python -m heretic.main`` would then import the module without running it.
ENGINE_BOOTSTRAP = (
    "import sys; from heretic.main import main; sys.argv[0] = 'heretic'; main()"
)

#: Environment variables the engine may inherit. Everything else is dropped so
#: unrelated secrets (API keys, cloud credentials) can never reach the child.
ENGINE_ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TMPDIR",
        "TMP",
        "TEMP",
        "SYSTEMROOT",
        "USERPROFILE",
        "VIRTUAL_ENV",
        "CONDA_PREFIX",
        "PYTHONPATH",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "CUDA_VISIBLE_DEVICES",
        "ROCM_VISIBLE_DEVICES",
        "HIP_VISIBLE_DEVICES",
        "LD_LIBRARY_PATH",
        "HF_HOME",
        "HF_HUB_CACHE",
        "HUGGINGFACE_HUB_CACHE",
        "HF_TOKEN",
        "HUGGING_FACE_HUB_TOKEN",
        "HF_HUB_OFFLINE",
        "TRANSFORMERS_OFFLINE",
        "TOKENIZERS_PARALLELISM",
    }
)

#: Fields of Heretic 2.x ``Settings`` used when the engine package is absent.
_FALLBACK_HERETIC_SETTINGS_KEYS = frozenset(
    {
        "model",
        "model_commit",
        "evaluate_model",
        "collect_reproducibles",
        "reproduce",
        "dtypes",
        "quantization",
        "device_map",
        "max_memory",
        "offload_outputs_to_cpu",
        "batch_size",
        "max_batch_size",
        "batch_size_test_prompts",
        "max_response_length",
        "response_prefix",
        "response_prefix_test_prompts",
        "chain_of_thought_blocks",
        "print_debug_information",
        "scorers",
        "modifiers",
        "n_trials",
        "n_startup_trials",
        "seed",
        "study_checkpoint_dir",
        "modified_model_type",
        "benchmarks",
        "max_shard_size",
        "export_strategy",
        "checkpoint_action",
        "trial_index",
        "n_additional_trials",
        "model_action",
        "save_directory",
        "upload_repo_id",
        "upload_repo_private",
        "upload_reproducibility_information",
        "upload_tags",
        "ignore_mismatches",
        "system_prompt",
        "enable_shell",
    }
)

_SHELL_METACHARACTER_RE = re.compile(r"[;&|`$<>(){}\[\]*?!\n\r\x00]")
_CONTROL_CHARACTER_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TOML_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_MODEL_REFERENCE_LENGTH = 4096
_MAX_SEQUENCE_LENGTH = 32768
_MAX_BATCH_SIZE = 4096
_MAX_TIMEOUT_SECONDS = 7 * 24 * 3600


def _distribution_version(distribution: str) -> str | None:
    try:
        return importlib_metadata.version(distribution)
    except (importlib_metadata.PackageNotFoundError, ValueError, OSError):
        return None
    except Exception:  # pragma: no cover - defensive: broken metadata
        return None


def _module_installed(import_name: str) -> bool:
    try:
        return importlib.util.find_spec(import_name) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


def engine_status() -> dict[str, Any]:
    """Report engine availability without importing torch or transformers."""
    modules: dict[str, dict[str, Any]] = {}
    versions: dict[str, str] = {}
    missing: list[str] = []
    optional_missing: list[str] = []
    for import_name, distribution in REQUIRED_ENGINE_MODULES:
        installed = _module_installed(import_name)
        version = _distribution_version(distribution)
        modules[import_name] = {"installed": installed, "version": version}
        if version:
            versions[distribution] = version
        if not installed:
            missing.append(import_name)
    for import_name, distribution in OPTIONAL_ENGINE_MODULES:
        installed = _module_installed(import_name)
        version = _distribution_version(distribution)
        modules[import_name] = {"installed": installed, "version": version}
        if version:
            versions[distribution] = version
        if not installed:
            optional_missing.append(import_name)

    heretic_installed = _module_installed("heretic")
    heretic_origin: str | None = None
    if heretic_installed:
        try:
            spec = importlib.util.find_spec("heretic")
        except (ImportError, ValueError, ModuleNotFoundError):
            spec = None
        if spec is not None and spec.origin:
            heretic_origin = spec.origin
    heretic_version = _distribution_version("heretic-llm") or _distribution_version(
        "fsociety-ai"
    )
    if heretic_version:
        versions["heretic"] = heretic_version

    available = not missing and heretic_installed
    if available:
        message = "Heretic engine available."
        hint = None
    elif missing:
        message = (
            "The modification engine is unavailable; missing dependencies: "
            + ", ".join(missing)
            + "."
        )
        hint = (
            "Install the engine profile, e.g. `pip install fsociety-ai` "
            "(which includes torch/transformers)."
        )
    else:
        message = "The 'heretic' package is not importable from this interpreter."
        hint = "Install fsociety-ai, or run from a source checkout with PYTHONPATH=src."

    status: dict[str, Any] = {
        "available": available,
        "missing": missing,
        "optional_missing": optional_missing,
        "modules": modules,
        "versions": versions,
        "heretic": {
            "installed": heretic_installed,
            "version": heretic_version,
            "module_file": heretic_origin,
        },
        "python": {
            "executable": sys.executable,
            "version": platform.python_version(),
        },
        "message": message,
    }
    if hint:
        status["hint"] = hint
    return status


@lru_cache(maxsize=1)
def _engine_has_main_guard() -> bool:
    """Whether ``python -m heretic.main`` would actually call ``main()``."""
    try:
        spec = importlib.util.find_spec("heretic.main")
    except (ImportError, ValueError, ModuleNotFoundError):
        return False
    origin = getattr(spec, "origin", None)
    if not origin:
        return False
    try:
        source = Path(origin).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "__main__" in source and "if __name__" in source


def build_engine_command(
    args: list[str], python_executable: str | None = None
) -> list[str]:
    """Argument vector used to launch the engine. Never involves a shell."""
    executable = python_executable or sys.executable
    if _engine_has_main_guard():
        return [executable, "-m", "heretic.main", *args]
    return [executable, "-c", ENGINE_BOOTSTRAP, *args]


@lru_cache(maxsize=1)
def heretic_settings_keys() -> frozenset[str]:
    """Field names of the installed Heretic ``Settings`` model."""
    try:
        from heretic.config import Settings
    except Exception:  # pragma: no cover - engine package absent
        return _FALLBACK_HERETIC_SETTINGS_KEYS
    fields = getattr(Settings, "model_fields", None)
    if isinstance(fields, dict) and fields:
        return frozenset(fields)
    return _FALLBACK_HERETIC_SETTINGS_KEYS


def _validate_model_reference(value: str, *, field: str = "model") -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string.")
    text = value.strip()
    if not text:
        raise ValidationError(f"{field} must not be empty.")
    if len(text) > _MAX_MODEL_REFERENCE_LENGTH:
        raise ValidationError(
            f"{field} is longer than {_MAX_MODEL_REFERENCE_LENGTH} characters."
        )
    if _CONTROL_CHARACTER_RE.search(text):
        raise ValidationError(f"{field} contains control characters.")
    match = _SHELL_METACHARACTER_RE.search(text)
    if match:
        raise ValidationError(
            f"{field} contains the shell metacharacter {match.group(0)!r}.",
            details={"field": field, "value": text},
            hint="Arguments are passed to the engine without a shell; remove "
            "characters such as ; & | $ ` > <.",
        )
    if ".." in re.split(r"[\\/]", text):
        raise ValidationError(
            f"{field} contains a '..' path segment.",
            details={"field": field, "value": text},
            hint="Use a path inside the workspace, or an absolute path without "
            "traversal segments.",
        )
    return text


def _toml_key(key: str) -> str:
    if _TOML_BARE_KEY_RE.match(key):
        return key
    return json.dumps(key, ensure_ascii=False)


def _check_toml_value(value: Any, *, field: str) -> None:
    if isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValidationError(f"{field} must be a finite number.")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, dict):
                raise ValidationError(
                    f"{field}[{index}] is an inline table; arrays of tables are "
                    "not supported in generated engine configs."
                )
            _check_toml_value(item, field=f"{field}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise ValidationError(f"{field} contains a non-string key.")
            if isinstance(item, dict):
                raise ValidationError(
                    f"{field}.{key} is a nested table; nested tables are not "
                    "supported in generated engine configs."
                )
            _check_toml_value(item, field=f"{field}.{key}")
        return
    raise ValidationError(
        f"Cannot represent {type(value).__name__} as TOML for {field}."
    )


def _toml_value(value: Any, *, field: str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValidationError(f"{field} must be a finite number.")
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        rendered = ", ".join(
            _toml_value(item, field=f"{field}[{index}]")
            for index, item in enumerate(value)
        )
        return f"[{rendered}]"
    raise ValidationError(
        f"Cannot represent {type(value).__name__} as TOML for {field}."
    )


def _validate_with_engine(document: str) -> None:
    """Best-effort validation against the installed Heretic settings model."""
    try:
        from heretic.config import InitOnlySettings
    except Exception:  # pragma: no cover - engine package absent
        return
    try:
        import tomllib as toml_reader
    except ModuleNotFoundError:  # pragma: no cover - Python 3.10
        import tomli as toml_reader  # type: ignore[no-redef]
    try:
        payload = toml_reader.loads(document)
    except Exception as exc:
        raise FSocietyError(
            f"Generated engine configuration is not valid TOML: {exc}"
        ) from exc
    try:
        InitOnlySettings.model_validate(payload)
    except Exception as exc:
        message = str(exc)
        if len(message) > 500:
            message = message[:500] + "..."
        raise FSocietyError(
            "The installed Heretic version rejected the generated engine "
            f"configuration: {message}",
            details={"error": type(exc).__name__},
            hint="This indicates a platform/engine version mismatch; report it "
            "with the generated configuration file.",
        ) from exc


def _toml_document(config: dict[str, Any]) -> str:
    scalars: list[str] = []
    tables: list[tuple[str, dict[str, Any]]] = []
    for key, value in config.items():
        if isinstance(value, dict):
            tables.append((key, value))
        else:
            scalars.append(f"{_toml_key(key)} = {_toml_value(value, field=key)}")
    lines = [
        "# Generated by FSOCIETY AI for the Heretic engine.",
        "# The platform may overwrite this file; edit a copy if you need to customise it.",
        "",
        *scalars,
    ]
    for table_name, table in tables:
        lines.append("")
        lines.append(f"[{_toml_key(table_name)}]")
        for key, value in table.items():
            lines.append(
                f"{_toml_key(key)} = {_toml_value(value, field=f'{table_name}.{key}')}"
            )
    return "\n".join(lines).rstrip("\n") + "\n"


def trust_decision(repo_id: str, settings: PlatformSettings) -> dict[str, Any]:
    """Whether remote code may run for ``repo_id``, and why."""
    if not isinstance(repo_id, str) or not repo_id.strip():
        raise ValidationError("repo_id is required for a trust decision.")
    policy = settings.trust_remote_code
    allowed = settings.remote_code_allowed(repo_id)
    allowlisted = repo_id in settings.trusted_repositories
    if policy == "never":
        reason = (
            "Remote code execution is disabled by policy (trust_remote_code='never')."
        )
    elif policy == "always":
        reason = (
            "Policy 'always' allows custom model code for every repository; this "
            "is unsafe for untrusted models."
        )
    elif allowlisted:
        reason = f"{repo_id!r} is explicitly allowlisted."
    else:
        reason = f"{repo_id!r} is not on the trusted allowlist."
    return {
        "repo_id": repo_id,
        "allowed": allowed,
        "policy": policy,
        "allowlisted": allowlisted,
        "reason": reason,
    }


class HereticEngineAdapter:
    """Write engine configuration and run the preserved Heretic CLI."""

    def __init__(
        self,
        settings: PlatformSettings | None = None,
        *,
        logger: AuditLogger | None = None,
        python_executable: str | None = None,
    ) -> None:
        self.settings = settings if settings is not None else PlatformSettings()
        self.logger = logger
        self.python_executable = python_executable or sys.executable

    # ---- logging -----------------------------------------------------------
    def _log(
        self, message: str, *, level: str = "info", data: dict[str, Any] | None = None
    ) -> None:
        if self.logger is not None:
            try:
                self.logger.emit(
                    message, level=level, category="engine", data=data or {}
                )
                return
            except (OSError, ValueError) as exc:  # pragma: no cover - defensive
                logging.getLogger(__name__).warning(
                    "Audit logging failed (%s); falling back to the standard logger.",
                    exc,
                )

    # ---- status ------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        """Engine availability, identical to :func:`engine_status`."""
        return engine_status()

    def trust_decision(self, repo_id: str) -> dict[str, Any]:
        """Whether remote code may run for ``repo_id``, and why."""
        return trust_decision(repo_id, self.settings)

    def heretic_config_path(self) -> Path:
        """Configuration file the adapter writes and the engine reads."""
        env_value = os.environ.get("HERETIC_CONFIG_FILE")
        if env_value:
            return Path(env_value).expanduser()
        return (Path(self.settings.workspace) / "engine" / "heretic.toml").expanduser()

    # ---- argument validation ----------------------------------------------
    def validate_invocation(self, args: list[str]) -> None:
        """Reject anything the engine must never receive.

        The subprocess has no shell, so this is defence in depth: it blocks
        metacharacters, traversal segments, control characters and flags that
        would bypass the platform's trust policy.
        """
        if not isinstance(args, (list, tuple)):
            raise ValidationError("Engine arguments must be a list of strings.")
        for index, argument in enumerate(args):
            if not isinstance(argument, str):
                raise ValidationError(
                    f"Engine argument {index} is not a string.",
                    details={"index": index, "type": type(argument).__name__},
                )
            if not argument:
                raise ValidationError(
                    f"Engine argument {index} is empty.",
                    details={"index": index},
                )
            if len(argument) > _MAX_MODEL_REFERENCE_LENGTH:
                raise ValidationError(
                    f"Engine argument {index} is longer than "
                    f"{_MAX_MODEL_REFERENCE_LENGTH} characters."
                )
            if _CONTROL_CHARACTER_RE.search(argument):
                raise ValidationError(
                    f"Engine argument {index} contains control characters.",
                    details={"index": index},
                )
            match = _SHELL_METACHARACTER_RE.search(argument)
            if match:
                raise ValidationError(
                    f"Engine argument {index} contains the shell metacharacter "
                    f"{match.group(0)!r}.",
                    details={"index": index, "argument": argument},
                    hint="Arguments are passed without a shell; remove ; & | $ ` "
                    "> < and similar characters.",
                )
            flag = argument.split("=", 1)[0].lower()
            if flag in FORBIDDEN_ENGINE_FLAGS:
                raise TrustPolicyError(
                    f"Engine argument {index} requests remote code execution, which "
                    "the platform never enables.",
                    details={"index": index, "flag": flag},
                    hint="Allowlist the repository in the platform trust policy "
                    "instead of forwarding the flag.",
                )
            if ".." in re.split(r"[\\/]", argument):
                raise ValidationError(
                    f"Engine argument {index} contains a '..' path segment.",
                    details={"index": index, "argument": argument},
                    hint="Use a path inside the workspace.",
                )
        for index, argument in enumerate(args):
            if not argument.startswith("-"):
                _validate_model_reference(argument, field=f"engine argument {index}")

    # ---- configuration -----------------------------------------------------
    def write_platform_config(
        self,
        path: Path,
        model_id: str,
        *,
        dtype: str = "auto",
        quantization: str = "none",
        batch_size: int = 0,
        max_response_length: int = 100,
        extra: dict[str, Any] | None = None,
    ) -> Path:
        """Write a valid Heretic 2.x TOML configuration and return its path."""
        model = _validate_model_reference(model_id)
        if dtype not in ENGINE_DTYPES:
            raise ValidationError(
                f"Unsupported dtype for the engine: {dtype!r}",
                details={"supported": list(ENGINE_DTYPES)},
            )
        if quantization not in ENGINE_QUANTIZATIONS:
            raise ValidationError(
                f"Unsupported quantization for the engine: {quantization!r}",
                details={"supported": list(ENGINE_QUANTIZATIONS)},
            )
        if (
            not isinstance(batch_size, int)
            or isinstance(batch_size, bool)
            or not 0 <= batch_size <= _MAX_BATCH_SIZE
        ):
            raise ValidationError(
                f"batch_size must be an integer between 0 and {_MAX_BATCH_SIZE} "
                "(0 means automatic)."
            )
        if (
            not isinstance(max_response_length, int)
            or isinstance(max_response_length, bool)
            or not 1 <= max_response_length <= _MAX_SEQUENCE_LENGTH
        ):
            raise ValidationError(
                f"max_response_length must be an integer between 1 and "
                f"{_MAX_SEQUENCE_LENGTH}."
            )

        dtypes = [dtype, *(item for item in ENGINE_DTYPE_FALLBACKS if item != dtype)]
        config: dict[str, Any] = {
            "model": model,
            "dtypes": dtypes,
            "quantization": quantization,
            "device_map": "auto",
            "offload_outputs_to_cpu": True,
            "batch_size": int(batch_size),
            "max_response_length": int(max_response_length),
            "seed": int(self.settings.evaluation_seed),
            "print_debug_information": False,
            "enable_shell": False,
        }

        if extra is not None:
            if not isinstance(extra, dict):
                raise ValidationError("extra must be a mapping of engine settings.")
            allowed = heretic_settings_keys()
            reserved = {
                "model",
                "dtypes",
                "quantization",
                "batch_size",
                "max_response_length",
            }
            unknown = sorted(
                str(key)
                for key in extra
                if not isinstance(key, str) or key not in allowed
            )
            if unknown:
                raise ValidationError(
                    "extra contains keys that are not Heretic settings: "
                    + ", ".join(unknown),
                    details={"unknown_keys": unknown},
                    hint="Only fields of heretic.config.Settings are accepted.",
                )
            rejected = sorted(key for key in extra if key in reserved)
            if rejected:
                raise ValidationError(
                    "extra cannot override settings computed by the adapter: "
                    + ", ".join(rejected),
                    details={"reserved_keys": rejected},
                )
            for key, value in extra.items():
                if value is None:
                    continue
                _check_toml_value(value, field=str(key))
                config[key] = value

        allowed_keys = heretic_settings_keys()
        unexpected = sorted(set(config) - allowed_keys)
        if unexpected:  # pragma: no cover - internal invariant
            raise FSocietyError(
                "Generated engine configuration contains unknown Heretic settings: "
                + ", ".join(unexpected),
                details={"unknown_keys": unexpected},
            )

        target = self._config_target(path)
        document = _toml_document(config)
        try:
            _validate_with_engine(document)
        except FSocietyError as exc:
            if not extra:
                raise
            raise ValidationError(
                "The installed Heretic version rejected the configuration produced "
                f"from 'extra': {exc.message}",
                details=exc.details,
                hint="Adjust the extra values so they satisfy heretic.config.Settings.",
            ) from exc
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.tmp")
            temporary.write_text(document, encoding="utf-8")
            temporary.replace(target)
        except OSError as exc:
            raise FSocietyError(
                f"Could not write engine configuration to {target}: "
                f"{exc.strerror or exc}",
                details={"path": str(target)},
                hint="Check that the workspace is writable.",
            ) from exc
        self._log(
            "Wrote Heretic engine configuration.",
            data={
                "path": str(target),
                "model": model,
                "dtype": dtype,
                "quantization": quantization,
                "batch_size": batch_size,
                "max_response_length": max_response_length,
            },
        )
        return target

    def _config_target(self, path: Path) -> Path:
        target = Path(path).expanduser()
        if not target.is_absolute():
            target = safe_join(Path(self.settings.workspace), str(path))
        return target

    # ---- execution ---------------------------------------------------------
    def _environment(self) -> dict[str, str]:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key in ENGINE_ENV_ALLOWLIST
        }
        environment["PYTHONUNBUFFERED"] = "1"
        environment.setdefault("TOKENIZERS_PARALLELISM", "false")
        if self.settings.offline:
            environment["HF_HUB_OFFLINE"] = "1"
            environment["TRANSFORMERS_OFFLINE"] = "1"
        config = self.heretic_config_path()
        try:
            config_exists = config.is_file()
        except OSError as exc:
            self._log(
                f"Could not inspect the engine configuration path {config}: "
                f"{exc.strerror or exc}",
                level="warning",
                data={"path": str(config)},
            )
            config_exists = False
        if config_exists:
            environment["HERETIC_CONFIG_FILE"] = str(config)
        return environment

    def _working_directory(self) -> Path:
        config = self.heretic_config_path()
        try:
            if config.is_file() and config.parent.is_dir():
                return config.parent
        except OSError as exc:
            self._log(
                f"Could not inspect the engine configuration directory {config}: "
                f"{exc.strerror or exc}",
                level="warning",
                data={"path": str(config)},
            )
        workspace = Path(self.settings.workspace)
        if workspace.is_dir():
            return workspace
        return Path.cwd()

    def run(
        self, args: list[str], *, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run the engine with ``args`` and return the completed process."""
        if timeout is not None and (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or timeout <= 0
            or timeout > _MAX_TIMEOUT_SECONDS
        ):
            raise ValidationError(
                f"timeout must be between 0 and {_MAX_TIMEOUT_SECONDS} seconds."
            )
        status = self.status()
        if not status["available"]:
            raise EngineUnavailableError(
                str(status.get("message") or "The Heretic engine is unavailable."),
                details={
                    "missing": status.get("missing", []),
                    "versions": status.get("versions", {}),
                },
                hint=str(
                    status.get("hint") or "Install the engine dependencies, then retry."
                ),
            )
        self.validate_invocation(args)
        command = build_engine_command(args, self.python_executable)
        environment = self._environment()
        working_directory = self._working_directory()
        self._log(
            "Starting the Heretic engine.",
            data={
                "command": command[1:],
                "cwd": str(working_directory),
                "timeout": timeout,
            },
        )
        try:
            completed = subprocess.run(
                command,
                env=environment,
                cwd=str(working_directory),
                timeout=timeout,
                capture_output=True,
                text=True,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise FSocietyError(
                f"The Heretic engine did not finish within {timeout} seconds.",
                details={"timeout": timeout, "command": command[1:]},
                hint="Increase the timeout or reduce the workload.",
            ) from exc
        except OSError as exc:
            raise EngineUnavailableError(
                f"Could not start the Heretic engine: {exc.strerror or exc}",
                details={"command": command[1:]},
                hint="Check that this interpreter can import 'heretic'.",
            ) from exc
        if completed.returncode != 0:
            self._log(
                f"The Heretic engine exited with status {completed.returncode}.",
                level="warning",
                data={
                    "returncode": completed.returncode,
                    "stderr_tail": (completed.stderr or "")[-2000:],
                },
            )
        else:
            self._log(
                "The Heretic engine finished successfully.",
                data={"returncode": 0},
            )
        return completed
