# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Subprocess isolation for untrusted plugins.

Guarantees implemented here
----------------------------
* No shell execution: the worker is launched with an explicit ``argv``.
* Wall-clock timeout enforced by the parent, with the whole process group
  killed (``start_new_session=True`` on POSIX).
* Bounded stdout/stderr capture; oversized protocol output is rejected instead
  of being parsed as a truncated JSON document.
* Minimal environment allowlist: no provider API keys, no cloud credentials.
* Optional network-namespace isolation via ``unshare``. Availability is
  *probed* and the result records whether isolation was actually applied. This
  module never claims an isolation level it did not apply.
* Optional CPU limit inside the worker (POSIX ``RLIMIT_CPU``).

Limitations (reported honestly, never hidden)
---------------------------------------------
* This is process isolation, not a security boundary equivalent to a VM. A
  malicious plugin runs as the same OS user and can still read files that user
  can read unless the caller additionally applies OS sandboxing.
* ``unshare`` user namespaces may be disabled by the host; in that case
  ``network_isolated`` is ``False`` and a reason is returned.
* Windows has no process-group signal semantics; ``proc.kill()`` is used.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from ..core.models import PluginInfo
from ..errors import PluginError, PluginTimeoutError
from ..security.redaction import redact

#: Environment variables copied into the worker. No secrets/tokens.
ENV_ALLOWLIST: tuple[str, ...] = (
    "PATH",
    "HOME",
    "PYTHONPATH",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "TEMP",
    "TMP",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
)

_CLASS_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
_METHOD_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_MIN_OUTPUT_CAP = 1024
_MAX_PAYLOAD_BYTES = 16 * 1024 * 1024

_UNSHARE_CACHE: dict[str, Any] | None = None

_ABS_PATH_RE = re.compile(r"(?:[A-Za-z]:)?(?:[\\/][^\s'\":]+){2,}[\\/]?")


def _sanitize(text: str) -> str:
    """Redact secrets and collapse absolute host paths in diagnostics."""
    return _ABS_PATH_RE.sub("<path>", redact(text))


class _BoundedBuffer:
    """Byte sink that keeps at most ``limit`` bytes but counts everything."""

    __slots__ = ("buf", "limit", "total")

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.buf = bytearray()
        self.total = 0

    def feed(self, chunk: bytes) -> None:
        self.total += len(chunk)
        remaining = self.limit - len(self.buf)
        if remaining > 0:
            self.buf.extend(chunk[:remaining])

    @property
    def truncated(self) -> bool:
        return self.total > len(self.buf)


def _pump(stream: Any, sink: _BoundedBuffer) -> None:
    try:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            sink.feed(chunk)
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _absolute_pythonpath(value: str) -> str:
    parts: list[str] = []
    for entry in value.split(os.pathsep):
        if not entry:
            continue
        path = Path(entry)
        if not path.is_absolute():
            path = Path.cwd() / path
        parts.append(str(path))
    return os.pathsep.join(parts)


def _ensure_pythonpath(env: dict[str, str]) -> None:
    """Normalize or synthesize ``PYTHONPATH`` in place."""
    if "PYTHONPATH" in env:
        env["PYTHONPATH"] = _absolute_pythonpath(env["PYTHONPATH"])
    else:
        fallback = [
            entry
            for entry in sys.path
            if entry and Path(entry).is_absolute() and Path(entry).is_dir()
        ]
        if fallback:
            env["PYTHONPATH"] = os.pathsep.join(fallback)


def sandbox_env() -> dict[str, str]:
    """Build the worker environment from the strict allowlist."""
    env: dict[str, str] = {}
    for key in ENV_ALLOWLIST:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    _ensure_pythonpath(env)
    # Deterministic, non-secret runtime flags only.
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def detect_network_isolation(*, force: bool = False) -> dict[str, Any]:
    """Probe whether ``unshare`` can create a network namespace.

    Returns ``{"available", "argv_prefix", "reason"}``. The probe actually runs
    ``unshare … true`` so a container that has the binary but no permission is
    reported as unavailable.
    """
    global _UNSHARE_CACHE
    if _UNSHARE_CACHE is not None and not force:
        return dict(_UNSHARE_CACHE)

    if os.name != "posix":
        result: dict[str, Any] = {
            "available": False,
            "argv_prefix": [],
            "reason": "Network namespace isolation requires POSIX/unshare.",
        }
        _UNSHARE_CACHE = result
        return dict(result)

    executable = shutil.which("unshare")
    if not executable:
        result = {
            "available": False,
            "argv_prefix": [],
            "reason": "unshare executable not found on PATH.",
        }
        _UNSHARE_CACHE = result
        return dict(result)

    failures: list[str] = []
    for flags in (["-n"], ["-rn"]):
        argv = [executable, *flags, "--", "true"]
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                timeout=5,
                check=False,
                env=sandbox_env(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            failures.append(f"{' '.join(flags)}: {exc}")
            continue
        if completed.returncode == 0:
            result = {
                "available": True,
                "argv_prefix": [executable, *flags, "--"],
                "reason": f"verified network namespace creation via unshare {' '.join(flags)}.",
            }
            _UNSHARE_CACHE = result
            return dict(result)
        stderr = completed.stderr.decode("utf-8", "replace").strip()
        failures.append(
            f"{' '.join(flags)}: rc={completed.returncode} {stderr}".strip()
        )

    result = {
        "available": False,
        "argv_prefix": [],
        "reason": "unshare exists but network namespace creation is not permitted ("
        + "; ".join(failures)
        + ").",
    }
    _UNSHARE_CACHE = result
    return dict(result)


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        process.kill()
    except (ProcessLookupError, OSError):
        pass


def _normalize_plugin_reference(plugin: str) -> str:
    """Return an absolute file path or a validated ``module[:Class]`` string."""
    text = plugin.strip()
    if not text:
        raise PluginError("Plugin reference must not be empty.")
    if "\x00" in text or any(ord(ch) < 32 for ch in text):
        raise PluginError("Plugin reference contains control characters.")
    candidate = Path(text)
    if candidate.exists():
        resolved = candidate.resolve()
        if not resolved.is_file():
            raise PluginError(f"Plugin path is not a file: {resolved.name}")
        if resolved.suffix != ".py":
            raise PluginError("Plugin file must have a .py extension.")
        return str(resolved)
    module, _, attribute = text.partition(":")
    if not _MODULE_RE.match(module):
        raise PluginError(f"Invalid plugin module reference: {text!r}")
    if attribute and not _CLASS_RE.match(attribute):
        raise PluginError(f"Invalid plugin entry-point class: {attribute!r}")
    if ".." in module.split("."):
        raise PluginError("Plugin module reference must not contain parent segments.")
    return text


def _validate_call(
    class_name: str,
    method: str,
    payload: Any,
    timeout_seconds: float,
    max_output_bytes: int,
) -> dict[str, Any]:
    if method != "discover_plugins" and not _CLASS_RE.match(class_name):
        raise PluginError(f"Invalid plugin class name: {class_name!r}")
    if not _METHOD_RE.match(method):
        raise PluginError(f"Invalid plugin method name: {method!r}")
    if not isinstance(payload, dict):
        raise PluginError("Plugin payload must be a JSON object (dict).")
    if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
        raise PluginError("Plugin timeout must be a positive number of seconds.")
    if not isinstance(max_output_bytes, int) or max_output_bytes < _MIN_OUTPUT_CAP:
        raise PluginError(
            f"Plugin output cap must be an integer >= {_MIN_OUTPUT_CAP} bytes."
        )
    return payload


def run_isolated(
    plugin: str,
    class_name: str,
    method: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = 30,
    max_output_bytes: int = 1_000_000,
    allow_network: bool = False,
    python_executable: str | None = None,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run ``plugin.method(payload)`` in a sandboxed worker process.

    Returns an envelope ``{"ok": True, "result": ..., "isolation": {...}}``.
    Raises :class:`PluginTimeoutError` on wall-clock timeout and
    :class:`PluginError` for crashes, invalid protocol output or output overflow.
    """
    body = dict(payload or {})
    _validate_call(class_name, method, body, timeout_seconds, max_output_bytes)
    reference = _normalize_plugin_reference(plugin)

    try:
        payload_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PluginError(
            "Plugin payload is not JSON serializable.",
            details={"error": redact(str(exc))},
        ) from exc
    if len(payload_bytes) > _MAX_PAYLOAD_BYTES:
        raise PluginError(
            f"Plugin payload exceeds the {_MAX_PAYLOAD_BYTES} byte limit.",
            details={"size_bytes": len(payload_bytes)},
        )

    executable = python_executable or sys.executable
    worker_argv = [
        executable,
        "-m",
        "fsociety.plugins.worker",
        "--plugin",
        reference,
        "--class",
        class_name,
        "--method",
        method,
        "--max-output-bytes",
        str(max_output_bytes),
        "--cpu-limit-seconds",
        # Give the parent wall-clock timeout a grace window so RLIMIT_CPU does
        # not race it and turn a timeout into an empty-protocol error.
        str(max(1, round(timeout_seconds)) + 2),
    ]

    network = detect_network_isolation()
    network_isolated = bool(network["available"] and not allow_network)
    argv = (
        list(network["argv_prefix"]) + worker_argv if network_isolated else worker_argv
    )

    worker_env = dict(env) if env is not None else sandbox_env()
    if env is not None:
        # Explicit env is still filtered: never let a caller smuggle a secret in.
        worker_env = {
            key: value
            for key, value in worker_env.items()
            if key in ENV_ALLOWLIST or key in {"PYTHONIOENCODING", "PYTHONUNBUFFERED"}
        }
        _ensure_pythonpath(worker_env)

    temp_context: tempfile.TemporaryDirectory[str] | None = None
    if cwd is None:
        temp_context = tempfile.TemporaryDirectory(prefix="fsociety-plugin-")
        workdir = temp_context.name
    else:
        workdir = str(Path(cwd).resolve())
        if not Path(workdir).is_dir():
            raise PluginError(f"Isolation working directory does not exist: {workdir}")

    started = time.monotonic()
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=workdir,
            env=worker_env,
            start_new_session=(os.name == "posix"),
        )
    except OSError as exc:
        if temp_context is not None:
            temp_context.cleanup()
        raise PluginError(
            "Could not start the plugin isolation worker.",
            details={"error": redact(str(exc))},
            hint="Ensure the configured Python executable exists and is runnable.",
        ) from exc

    stdout_sink = _BoundedBuffer(max_output_bytes)
    stderr_sink = _BoundedBuffer(max_output_bytes)
    threads = [
        threading.Thread(target=_pump, args=(process.stdout, stdout_sink), daemon=True),
        threading.Thread(target=_pump, args=(process.stderr, stderr_sink), daemon=True),
    ]
    for thread in threads:
        thread.start()

    timed_out = False
    try:
        try:
            if process.stdin is not None:
                process.stdin.write(payload_bytes)
                process.stdin.close()
        except (BrokenPipeError, OSError):
            pass

        deadline = started + float(timeout_seconds)
        for thread in threads:
            remaining = max(0.0, deadline - time.monotonic())
            thread.join(timeout=remaining)
        if any(thread.is_alive() for thread in threads):
            timed_out = True
            _kill_process_group(process)
            for thread in threads:
                thread.join(timeout=1.0)
        else:
            remaining = max(0.05, deadline - time.monotonic())
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_process_group(process)
    finally:
        if process.poll() is None:
            _kill_process_group(process)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        for thread in threads:
            thread.join(timeout=1.0)
        if temp_context is not None:
            temp_context.cleanup()

    elapsed = time.monotonic() - started

    if timed_out:
        raise PluginTimeoutError(
            f"Plugin {class_name}.{method} exceeded the {timeout_seconds:g}s isolation timeout.",
            details={
                "plugin": class_name,
                "method": method,
                "timeout_seconds": float(timeout_seconds),
                "duration_seconds": round(elapsed, 4),
            },
            hint="Reduce plugin work or raise plugin_timeout_seconds explicitly.",
        )

    stdout_text = bytes(stdout_sink.buf).decode("utf-8", "replace")
    stderr_text = bytes(stderr_sink.buf).decode("utf-8", "replace")
    if stdout_sink.truncated:
        raise PluginError(
            f"Plugin {class_name}.{method} exceeded the "
            f"{max_output_bytes} byte output cap.",
            details={
                "code": "plugin_output_limit",
                "stdout_bytes": stdout_sink.total,
                "duration_seconds": round(elapsed, 4),
            },
            hint="The plugin must not write to stdout directly; raise "
            "plugin_max_output_bytes only if the protocol result is legitimately large.",
        )

    payload_out: dict[str, Any] | None = None
    stripped = stdout_text.strip()
    if stripped:
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            payload_out = parsed

    if payload_out is None:
        tail = _sanitize(stderr_text[-2000:]) if stderr_text else ""
        raise PluginError(
            f"Plugin {class_name}.{method} produced an invalid isolation protocol response.",
            details={
                "returncode": process.returncode,
                "stderr": tail,
                "stdout_truncated": stdout_sink.truncated,
                "stderr_truncated": stderr_sink.truncated,
                "duration_seconds": round(elapsed, 4),
            },
            hint="A plugin must return JSON-serializable data; direct stdout writes "
            "corrupt the worker protocol.",
        )

    if not payload_out.get("ok"):
        error = payload_out.get("error") or {}
        code = error.get("code") or "plugin_error"
        message = error.get("message") or "Plugin execution failed."
        raise PluginError(
            f"Plugin {class_name}.{method} failed: {_sanitize(str(message))}",
            details={
                "code": _sanitize(str(code)),
                "plugin": class_name,
                "method": method,
                "duration_seconds": round(elapsed, 4),
            },
        )

    isolation_info = {
        "mode": "subprocess",
        "network_isolated": network_isolated,
        "network_isolation_requested": not allow_network,
        "unshare_available": bool(network["available"]),
        "unshare_reason": network["reason"],
        "cwd": "temporary-directory" if temp_context is not None else workdir,
    }
    return {
        "ok": True,
        "result": payload_out.get("result"),
        "plugin": class_name,
        "method": method,
        "duration_seconds": round(elapsed, 4),
        "isolation": isolation_info,
        "output": {
            "stdout_bytes": stdout_sink.total,
            "stderr_bytes": stderr_sink.total,
            "stdout_truncated": stdout_sink.truncated,
            "stderr_truncated": stderr_sink.truncated,
            "plugin_stdout_bytes": payload_out.get("plugin_stdout_bytes", 0),
            "plugin_stdout_truncated": payload_out.get(
                "plugin_stdout_truncated", False
            ),
            "plugin_stderr_bytes": payload_out.get("plugin_stderr_bytes", 0),
        },
    }


def discover_plugins_isolated(
    plugin: str,
    class_name: str = "",
    *,
    timeout_seconds: float = 30,
    max_output_bytes: int = 1_000_000,
    allow_network: bool = False,
    python_executable: str | None = None,
) -> list[PluginInfo]:
    """Discover and validate plugin classes without importing them in the host.

    Untrusted workspace plugins are imported inside the isolation worker, so a
    malicious module-level payload cannot execute in the API/CLI process during
    discovery. The returned metadata is validated against the plugin contract;
    execution still happens in a fresh worker.
    """
    envelope = run_isolated(
        plugin,
        class_name,
        "discover_plugins",
        {},
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
        allow_network=allow_network,
        python_executable=python_executable,
    )
    payload = envelope.get("result")
    if not isinstance(payload, list):
        raise PluginError(
            "Plugin discovery worker returned an unexpected payload.",
            details={"plugin": plugin},
        )
    discovered: list[PluginInfo] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            discovered.append(PluginInfo.model_validate(item))
        except Exception as exc:
            raise PluginError(
                "Plugin discovery worker returned invalid metadata.",
                details={"plugin": plugin, "error": str(exc)},
            ) from exc
    return discovered
