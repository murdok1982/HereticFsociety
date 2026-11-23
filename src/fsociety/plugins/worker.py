# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Isolated worker entry point.

The parent (:mod:`fsociety.plugins.isolation`) launches this module in a fresh
process with a minimal environment and a temporary working directory. The
protocol is line-delimited JSON on the *real* stdout; plugin stdout/stderr is
captured into bounded in-memory sinks so a chatty plugin cannot corrupt the
protocol or exhaust host memory.

This module never prints tracebacks: errors are returned as
``{"ok": false, "error": {"code": ..., "message": ...}}`` with secrets redacted
and absolute host paths collapsed to ``<path>``.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import io
import json
import os
import re
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any, cast

from fsociety import PLUGIN_API_VERSION

from ..errors import PluginError
from ..security.redaction import redact
from .api import PluginBase, allowed_method, validate_plugin_class

_MAX_STDIN_BYTES = 16 * 1024 * 1024
_MIN_OUTPUT_CAP = 1024
_CLASS_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_ABS_PATH_RE = re.compile(r"(?:[A-Za-z]:)?(?:[\\/][^\s'\":]+){2,}[\\/]?")


class _BoundedText(io.TextIOBase):
    """Text sink that keeps at most ``limit`` characters but counts all writes."""

    def __init__(self, limit: int) -> None:
        self._limit = max(0, limit)
        self._chunks: list[str] = []
        self._kept = 0
        self.total = 0

    def write(self, text: str) -> int:  # type: ignore[override]
        value = str(text)
        self.total += len(value)
        remaining = self._limit - self._kept
        if remaining > 0:
            chunk = value[:remaining]
            self._chunks.append(chunk)
            self._kept += len(chunk)
        return len(value)

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:  # pragma: no cover - trivial
        return False

    def writable(self) -> bool:
        return True

    @property
    def truncated(self) -> bool:
        return self.total > self._kept

    def getvalue(self) -> str:
        return "".join(self._chunks)


def _sanitize(text: str) -> str:
    cleaned = redact(text)
    return _ABS_PATH_RE.sub("<path>", cleaned)


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return sorted(str(item) for item in value)
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return value.model_dump(mode="json")
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable.")


def _emit(payload: dict[str, Any]) -> int | None:
    encoded = json.dumps(payload, ensure_ascii=False, default=_json_default)
    stdout = sys.__stdout__
    if stdout is None:
        return 1
    stdout.write(encoded + "\n")
    stdout.flush()


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": _sanitize(message)}}


def _apply_cpu_limit(seconds: int) -> None:
    """Apply a per-process CPU limit using ``resource`` when available."""
    if seconds <= 0 or os.name != "posix":
        return
    try:
        import resource

        soft = int(seconds)
        hard = soft + 1
        resource.setrlimit(resource.RLIMIT_CPU, (soft, hard))
    except (ImportError, OSError, ValueError):
        # The parent wall-clock timeout remains the hard stop.
        return


def _load_from_file(path: Path) -> ModuleType:
    resolved = path.resolve()
    if not resolved.is_file():
        raise PluginError(f"Plugin file not found: {resolved.name}")
    if resolved.suffix != ".py":
        raise PluginError("Plugin file must have a .py extension.")
    module_name = f"fsociety_worker_plugin_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, resolved)
    if spec is None or spec.loader is None:
        raise PluginError("Could not build an import spec for the plugin file.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


def _load_module(reference: str, class_name: str) -> tuple[ModuleType, str]:
    """Load ``(module, class_name)`` from a path or ``module[:Class]`` ref."""
    text = reference.strip()
    if not text or "\x00" in text:
        raise PluginError("Plugin reference must be a non-empty string.")
    if any(ord(ch) < 32 for ch in text):
        raise PluginError("Plugin reference contains control characters.")

    attribute = ""
    if ":" in text and not (len(text) > 1 and text[1] == ":"):
        module_part, attribute = text.split(":", 1)
        if attribute and not _CLASS_RE.match(attribute):
            raise PluginError("Plugin entry point must be a simple class name.")
        text = module_part
    if attribute and class_name and attribute != class_name:
        raise PluginError(
            "Conflicting plugin class names in the entry point and --class argument."
        )
    resolved_class = class_name or attribute

    candidate = Path(text)
    if candidate.exists():
        return _load_from_file(candidate), resolved_class
    if not _MODULE_RE.match(text) or ".." in text.split("."):
        raise PluginError("Invalid plugin module reference.")
    if not resolved_class:
        # Internal discovery of every plugin class in an installed module.
        return importlib.import_module(text), ""
    return importlib.import_module(text), resolved_class


def _resolve_class(module: ModuleType, class_name: str) -> type[PluginBase]:
    if not _CLASS_RE.match(class_name) or class_name.startswith("_"):
        raise PluginError("Invalid plugin class name.")
    candidate = getattr(module, class_name, None)
    if candidate is None:
        raise PluginError(f"Plugin module does not define class {class_name!r}.")
    if not isinstance(candidate, type) or not issubclass(candidate, PluginBase):
        raise PluginError(f"Class {class_name!r} is not a PluginBase subclass.")
    return candidate


def _invoke(
    instance: PluginBase, kind: str, method: str, payload: dict[str, Any]
) -> Any:
    if kind in {"hardware_provider", "integration"}:
        # detect()/probe() take no payload by contract.
        return getattr(instance, method)()
    if kind == "exporter" and method == "export":
        destination = payload.get("destination")
        if not isinstance(destination, str) or not destination.strip():
            raise PluginError("Exporter payload requires a 'destination' string.")
        target = Path(destination).expanduser()
        if not target.is_absolute():
            raise PluginError(
                "Exporter destination must be an absolute path when running isolated.",
                hint="Relative destinations would resolve inside the deleted sandbox cwd.",
            )
        from .api import Exporter

        return cast(Exporter, instance).export(payload, target)
    return getattr(instance, method)(payload)


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.method and not re.match(r"^[a-z][a-z0-9_]{0,63}$", args.method):
        return _error("invalid_method", "Invalid plugin method name.")

    raw = sys.stdin.buffer.read(_MAX_STDIN_BYTES + 1)
    if len(raw) > _MAX_STDIN_BYTES:
        return _error(
            "payload_too_large", "Plugin payload exceeds the worker input limit."
        )
    try:
        payload = json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _error("invalid_payload", f"Plugin payload is not valid JSON: {exc}")
    if not isinstance(payload, dict):
        return _error("invalid_payload", "Plugin payload must be a JSON object.")

    module, class_name = _load_module(args.plugin, args.class_name)

    if args.method == "discover_plugins":
        from .api import PluginBase

        discovered: list[dict[str, Any]] = []
        if class_name:
            cls = _resolve_class(module, class_name)
            info = validate_plugin_class(
                cls, path=args.plugin if Path(args.plugin).exists() else None
            )
            discovered.append(info.model_dump(mode="json"))
        else:
            suffixes = (
                "Scorer",
                "Evaluator",
                "Exporter",
                "HardwareProvider",
                "Integration",
                "Modifier",
            )
            for value in vars(module).values():
                if not isinstance(value, type):
                    continue
                if getattr(value, "__module__", None) != module.__name__:
                    continue
                if value is not PluginBase and not issubclass(value, PluginBase):
                    if any(value.__name__.endswith(suffix) for suffix in suffixes):
                        from ..core.models import PluginInfo

                        discovered.append(
                            PluginInfo(
                                name=str(
                                    getattr(value, "name", "") or Path(args.plugin).stem
                                ),
                                version=str(getattr(value, "version", "0.0.0")),
                                api_version=int(getattr(value, "api_version", 0) or 0),
                                kind="scorer",
                                class_name=value.__name__,
                                module=module.__name__,
                                path=args.plugin
                                if Path(args.plugin).exists()
                                else None,
                                valid=False,
                                errors=[
                                    "Class is not a PluginBase subclass; derive from "
                                    "Scorer/Evaluator/Exporter/etc."
                                ],
                            ).model_dump(mode="json")
                        )
                    continue
                info = validate_plugin_class(
                    value, path=args.plugin if Path(args.plugin).exists() else None
                )
                discovered.append(info.model_dump(mode="json"))
        return {"ok": True, "result": discovered}

    cls = _resolve_class(module, class_name)
    info = validate_plugin_class(cls)
    if not info.valid:
        return _error("invalid_plugin", "; ".join(info.errors))
    if info.api_version != PLUGIN_API_VERSION:
        return _error(
            "invalid_api_version",
            f"Plugin API version {info.api_version} is unsupported.",
        )

    expected_method = allowed_method(info.kind)
    if expected_method is None or args.method != expected_method:
        return _error(
            "invalid_method",
            f"Plugin kind {info.kind!r} only exposes method {expected_method!r}.",
        )

    instance = cls()
    result = _invoke(instance, info.kind, args.method, payload)
    try:
        json.dumps(result, default=_json_default)
    except TypeError as exc:
        raise PluginError(f"Plugin result is not JSON serializable: {exc}") from exc
    return {"ok": True, "result": result}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FSOCIETY plugin isolation worker.")
    parser.add_argument(
        "--plugin", required=True, help="Plugin file path or module[:Class]."
    )
    parser.add_argument(
        "--class", dest="class_name", required=True, help="Plugin class name."
    )
    parser.add_argument("--method", required=True, help="Plugin method to invoke.")
    parser.add_argument("--max-output-bytes", type=int, default=1_000_000)
    parser.add_argument("--cpu-limit-seconds", type=int, default=0)
    args = parser.parse_args(argv)

    output_cap = max(_MIN_OUTPUT_CAP, int(args.max_output_bytes))
    _apply_cpu_limit(int(args.cpu_limit_seconds))

    real_stdout = sys.stdout
    real_stderr = sys.stderr
    plugin_stdout = _BoundedText(output_cap)
    plugin_stderr = _BoundedText(output_cap)
    try:
        sys.stdout = plugin_stdout  # type: ignore[assignment]
        sys.stderr = plugin_stderr  # type: ignore[assignment]
        try:
            response = _run(args)
        except PluginError as exc:
            response = _error(exc.code, exc.message)
        except BaseException as exc:
            response = _error("plugin_crash", f"{type(exc).__name__}: {exc}")
    finally:
        sys.stdout = real_stdout
        sys.stderr = real_stderr

    response["plugin_stdout_bytes"] = plugin_stdout.total
    response["plugin_stdout_truncated"] = plugin_stdout.truncated
    response["plugin_stderr_bytes"] = plugin_stderr.total
    try:
        _emit(response)
    except (TypeError, ValueError) as exc:
        _emit(
            _error("serialization_error", f"Could not serialize worker response: {exc}")
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
