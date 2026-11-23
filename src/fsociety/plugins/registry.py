# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Plugin discovery and execution registry.

Discovery never aborts on a broken plugin: every malformed candidate is kept in
the index with ``valid=False`` and a human-readable ``errors`` list, so the UI
and CLI can show exactly what is wrong without losing the healthy plugins.

Execution is isolated by default (subprocess + timeout + output cap). In-process
execution requires **both** ``isolation=False`` and ``allow_in_process=True``;
that is a deliberate, explicit opt-in for trusted builtin plugins only.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import sys
import time
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any, List, Sequence, cast

from pydantic import Field

from ..config import PlatformSettings
from ..core.models import PluginInfo, PluginKind, StrictModel
from ..errors import NotFoundError, PathSecurityError, PluginError
from ..paths import safe_join
from .api import (
    KIND_TO_ABC,
    PluginBase,
    allowed_method,
    validate_plugin_class,
)
from .isolation import discover_plugins_isolated, run_isolated
from .scorers import BUILTIN_SCORERS

#: Class-name suffixes recognised during file discovery.
SUFFIX_TO_KIND: dict[str, str] = {
    "Scorer": "scorer",
    "Evaluator": "evaluator",
    "Exporter": "exporter",
    "HardwareProvider": "hardware_provider",
    "Integration": "integration",
}

ENTRY_POINT_GROUP = "fsociety.plugins"


class PluginRunRequest(StrictModel):
    """Strict request envelope validated before any plugin lookup."""

    name: str = Field(min_length=1, max_length=64)
    method: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    payload: dict[str, Any] = Field(default_factory=dict)
    isolation: bool = True
    allow_in_process: bool = False
    allow_network: bool | None = None


def _iter_builtin_classes() -> List[type[PluginBase]]:
    return [cast(type[PluginBase], cls) for cls in BUILTIN_SCORERS]


def _invalid_info(
    *,
    name: str,
    class_name: str,
    module: str,
    path: str | None,
    kind: str,
    error: str,
    builtin: bool = False,
) -> PluginInfo:
    safe_kind = kind if kind in KIND_TO_ABC else "scorer"
    return PluginInfo(
        name=name,
        version="0.0.0",
        api_version=0,
        kind=cast(PluginKind, safe_kind),
        class_name=class_name,
        module=module,
        path=path,
        builtin=builtin,
        valid=False,
        errors=[error],
    )


class PluginRegistry:
    """Index of builtin, entry-point and workspace plugins."""

    def __init__(
        self,
        plugin_dirs: Sequence[Path] | None = None,
        *,
        logger: Any = None,
        settings: PlatformSettings | None = None,
    ) -> None:
        self.logger = logger
        self.settings = settings or PlatformSettings()
        if plugin_dirs is None:
            self.plugin_dirs = [Path(self.settings.workspace).expanduser() / "plugins"]
        else:
            self.plugin_dirs = [
                Path(directory).expanduser() for directory in plugin_dirs
            ]
        self._plugins: dict[str, PluginInfo] = {}
        self._modules: dict[str, ModuleType] = {}
        self._discover()

    # ------------------------------------------------------------------ audit
    def _audit(self, level: str, message: str, data: dict[str, Any]) -> None:
        if self.logger is None:
            return
        emitter = getattr(self.logger, "emit", None)
        if not callable(emitter):
            return
        try:
            emitter(message, level=level, category="plugins", data=data)
        except Exception:  # pragma: no cover - logging must never break execution
            return

    # -------------------------------------------------------------- discovery
    def _register(self, info: PluginInfo) -> None:
        if not info.name:
            self._audit(
                "warning",
                "Discarded plugin with empty name.",
                {"class_name": info.class_name, "path": info.path},
            )
            return
        if info.name in self._plugins:
            self._audit(
                "warning",
                "Duplicate plugin name ignored.",
                {"plugin": info.name, "class_name": info.class_name, "path": info.path},
            )
            return
        self._plugins[info.name] = info
        if not info.valid:
            self._audit(
                "warning",
                "Invalid plugin discovered.",
                {
                    "plugin": info.name,
                    "version": info.version,
                    "class_name": info.class_name,
                    "errors": info.errors,
                },
            )

    def _discover(self) -> None:
        self._discover_builtins()
        self._discover_entry_points()
        for directory in self.plugin_dirs:
            self._discover_directory(directory)

    def _discover_builtins(self) -> None:
        for cls in _iter_builtin_classes():
            # Class metadata is authoritative; the ABC it derives from is
            # checked by validate_plugin_class without an expected kind.
            info = validate_plugin_class(cls, builtin=True)
            self._register(info)

    def _discover_entry_points(self) -> None:
        try:
            entry_points = importlib.metadata.entry_points()
        except Exception as exc:  # pragma: no cover - metadata corruption
            self._audit(
                "warning",
                "Could not enumerate entry points.",
                {"error": str(exc)},
            )
            return
        if hasattr(entry_points, "select"):
            selected = list(entry_points.select(group=ENTRY_POINT_GROUP))
        else:  # pragma: no cover - Python 3.10 compatibility path
            selected = list(entry_points.get(ENTRY_POINT_GROUP, []))  # type: ignore[arg-type]

        for entry_point in selected:
            ep_name = str(getattr(entry_point, "name", "<unknown>"))
            value = str(getattr(entry_point, "value", "") or "")
            module_part, _, attribute = value.partition(":")
            attribute = attribute.split("[", 1)[0].strip()
            if attribute and "." in attribute:
                self._register(
                    _invalid_info(
                        name=ep_name,
                        class_name=attribute,
                        module=module_part,
                        path=None,
                        kind="scorer",
                        error="Entry point must reference a simple class name.",
                    )
                )
                continue
            try:
                discovered = discover_plugins_isolated(
                    module_part or value,
                    attribute,
                    timeout_seconds=self.settings.plugin_timeout_seconds,
                    max_output_bytes=self.settings.plugin_max_output_bytes,
                    allow_network=False,
                )
            except BaseException as exc:  # noqa: BLE001 - discovery must isolate failures
                self._register(
                    _invalid_info(
                        name=ep_name,
                        class_name=attribute,
                        module=module_part,
                        path=None,
                        kind="scorer",
                        error=f"Entry point discovery failed: {type(exc).__name__}: {exc}",
                    )
                )
                continue
            if not discovered:
                self._register(
                    _invalid_info(
                        name=ep_name,
                        class_name=attribute,
                        module=module_part,
                        path=None,
                        kind="scorer",
                        error="Entry point did not expose a plugin class.",
                    )
                )
                continue
            for info in discovered:
                if not info.name:
                    info = info.model_copy(update={"name": ep_name})
                self._register(info)

    def _discover_directory(self, directory: Path) -> None:
        try:
            root = directory.resolve()
        except OSError:
            return
        if not root.is_dir():
            return
        for candidate in sorted(root.glob("*.py")):
            if candidate.name.startswith("_") or candidate.name == "__init__.py":
                continue
            try:
                file = safe_join(root, candidate.name)
            except PathSecurityError as exc:
                self._register(
                    _invalid_info(
                        name=candidate.stem,
                        class_name="",
                        module="",
                        path=str(candidate),
                        kind="scorer",
                        error=f"Plugin path escapes its directory: {exc.message}",
                    )
                )
                continue
            if not file.is_file() or file.suffix != ".py":
                continue
            try:
                discovered = discover_plugins_isolated(
                    str(file),
                    timeout_seconds=self.settings.plugin_timeout_seconds,
                    max_output_bytes=self.settings.plugin_max_output_bytes,
                    allow_network=False,
                )
            except BaseException as exc:  # noqa: BLE001 - isolate discovery failures
                self._register(
                    _invalid_info(
                        name=file.stem,
                        class_name="",
                        module=file.stem,
                        path=str(file),
                        kind="scorer",
                        error=(
                            "Plugin discovery failed in isolation: "
                            f"{type(exc).__name__}: {exc}"
                        ),
                    )
                )
                continue
            if not discovered:
                self._register(
                    _invalid_info(
                        name=file.stem,
                        class_name="",
                        module=file.stem,
                        path=str(file),
                        kind="scorer",
                        error=(
                            "No plugin class found. Expected a PluginBase subclass."
                        ),
                    )
                )
                continue
            for info in discovered:
                info = info.model_copy(update={"path": str(file), "builtin": False})
                self._register(info)

    @staticmethod
    def _classes_in_module(module: ModuleType) -> List[type[Any]]:
        found: List[type[Any]] = []
        for value in vars(module).values():
            if not isinstance(value, type):
                continue
            if getattr(value, "__module__", None) != module.__name__:
                continue
            if any(value.__name__.endswith(suffix) for suffix in SUFFIX_TO_KIND):
                found.append(value)
        return found

    # ---------------------------------------------------------------- queries
    def list(self) -> List[PluginInfo]:
        return list(self._plugins.values())

    def get(self, name: str) -> PluginInfo:
        try:
            return self._plugins[name]
        except KeyError as exc:
            raise NotFoundError(
                f"Plugin {name!r} is not registered.",
                details={"available": sorted(self._plugins)},
                hint="Run `fsociety plugins list` to inspect discovered plugins.",
            ) from exc

    # ---------------------------------------------------------------- loading
    def _load_file_module(self, file: Path) -> ModuleType:
        """Import a workspace plugin in the host process.

        Only used by :meth:`load` / explicit in-process execution. Discovery and
        default execution never call this; untrusted code is imported in the
        isolation worker instead.
        """
        fingerprint = f"{file.resolve()}:{file.stat().st_mtime_ns}"
        module_name = f"fsociety_workspace_plugin_{uuid.uuid5(uuid.NAMESPACE_URL, fingerprint).hex}"
        existing = sys.modules.get(module_name)
        if existing is not None:
            return existing
        spec = importlib.util.spec_from_file_location(module_name, file)
        if spec is None or spec.loader is None:
            raise PluginError(f"Could not build an import spec for {file.name!r}.")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(module_name, None)
            raise
        return module

    def _module_for(self, info: PluginInfo) -> ModuleType:
        if info.module in self._modules:
            return self._modules[info.module]
        if info.path:
            module = self._load_file_module(Path(info.path))
            self._modules[module.__name__] = module
            return module
        if not info.module:
            raise PluginError(f"Plugin {info.name!r} has no importable module.")
        try:
            return importlib.import_module(info.module)
        except Exception as exc:
            raise PluginError(
                f"Could not import module {info.module!r} for plugin {info.name!r}.",
                details={"error": str(exc)},
            ) from exc

    def load(self, name: str) -> PluginBase:
        """Import and instantiate a plugin. This executes module import code."""
        info = self.get(name)
        started = time.monotonic()
        if not info.valid:
            self._audit(
                "error",
                "Refused to load invalid plugin.",
                {"plugin": name, "errors": info.errors},
            )
            raise PluginError(
                f"Plugin {name!r} is invalid and cannot be loaded.",
                details={"errors": info.errors},
            )
        try:
            module = self._module_for(info)
            candidate = getattr(module, info.class_name, None)
            if not isinstance(candidate, type) or not issubclass(candidate, PluginBase):
                raise PluginError("Registered class is not a PluginBase subclass.")
            instance = candidate()
        except PluginError:
            raise
        except Exception as exc:
            self._audit(
                "error",
                "Plugin load crashed.",
                {
                    "plugin": name,
                    "version": info.version,
                    "duration_seconds": round(time.monotonic() - started, 4),
                    "outcome": "crash",
                },
            )
            raise PluginError(
                f"Plugin {name!r} failed to load.",
                details={"error": str(exc)},
            ) from exc
        self._audit(
            "info",
            "Plugin loaded.",
            {
                "plugin": name,
                "version": info.version,
                "duration_seconds": round(time.monotonic() - started, 4),
                "outcome": "ok",
            },
        )
        return instance

    # -------------------------------------------------------------- execution
    def _plugin_reference(self, info: PluginInfo) -> tuple[str, str]:
        if info.path:
            return info.path, info.class_name
        if info.module:
            return f"{info.module}:{info.class_name}", info.class_name
        raise PluginError(f"Plugin {info.name!r} has no loadable reference.")

    def run(
        self,
        name: str,
        method: str,
        payload: dict[str, Any] | None = None,
        *,
        isolation: bool = True,
        allow_in_process: bool = False,
        allow_network: bool | None = None,
    ) -> dict[str, Any]:
        """Execute one plugin method.

        Isolated (subprocess) by default. Setting ``isolation=False`` without
        ``allow_in_process=True`` raises: untrusted plugins must never silently
        run in the host process.
        """
        request = PluginRunRequest(
            name=name,
            method=method,
            payload=dict(payload or {}),
            isolation=isolation,
            allow_in_process=allow_in_process,
            allow_network=allow_network,
        )
        info = self.get(request.name)
        if not info.valid:
            self._audit(
                "error",
                "Refused to run invalid plugin.",
                {"plugin": request.name, "errors": info.errors},
            )
            raise PluginError(
                f"Plugin {request.name!r} is invalid and cannot run.",
                details={"errors": info.errors},
            )

        expected = allowed_method(info.kind)
        if expected is None:
            raise PluginError(
                f"Plugin {request.name!r} has unsupported kind {info.kind!r}."
            )
        if request.method != expected:
            raise PluginError(
                f"Plugin {request.name!r} (kind {info.kind}) only exposes "
                f"method {expected!r}, not {request.method!r}.",
                details={"allowed_method": expected},
            )

        if not request.isolation:
            if not request.allow_in_process:
                raise PluginError(
                    "In-process plugin execution requires explicit opt-in.",
                    details={"plugin": request.name, "method": request.method},
                    hint="Pass allow_in_process=True only for plugins you fully trust.",
                )
            return self._run_in_process(info, request)

        reference, class_name = self._plugin_reference(info)
        network_allowed = (
            self.settings.plugin_allow_network
            if request.allow_network is None
            else request.allow_network
        )
        started = time.monotonic()
        try:
            result = run_isolated(
                reference,
                class_name,
                request.method,
                request.payload,
                timeout_seconds=self.settings.plugin_timeout_seconds,
                max_output_bytes=self.settings.plugin_max_output_bytes,
                allow_network=network_allowed,
            )
        except PluginError as exc:
            self._audit(
                "error",
                "Plugin run failed.",
                {
                    "plugin": info.name,
                    "version": info.version,
                    "method": request.method,
                    "duration_seconds": round(time.monotonic() - started, 4),
                    "outcome": "timeout" if exc.code == "plugin_timeout" else "error",
                    "error_code": exc.code,
                },
            )
            raise
        self._audit(
            "info",
            "Plugin run completed.",
            {
                "plugin": info.name,
                "version": info.version,
                "method": request.method,
                "duration_seconds": round(time.monotonic() - started, 4),
                "outcome": "ok",
                "isolation": result.get("isolation", {}),
            },
        )
        return result

    def _run_in_process(
        self, info: PluginInfo, request: PluginRunRequest
    ) -> dict[str, Any]:
        started = time.monotonic()
        instance = self.load(info.name)
        try:
            if info.kind in {"hardware_provider", "integration"}:
                result = getattr(instance, request.method)()
            elif info.kind == "exporter" and request.method == "export":
                destination = request.payload.get("destination")
                if not isinstance(destination, str) or not destination.strip():
                    raise PluginError(
                        "Exporter payload requires a 'destination' string."
                    )
                from .api import Exporter

                result = cast(Exporter, instance).export(
                    request.payload, Path(destination).expanduser().resolve()
                )
            else:
                result = getattr(instance, request.method)(request.payload)
        except Exception as exc:
            self._audit(
                "error",
                "In-process plugin run failed.",
                {
                    "plugin": info.name,
                    "version": info.version,
                    "method": request.method,
                    "duration_seconds": round(time.monotonic() - started, 4),
                    "outcome": "crash",
                },
            )
            raise PluginError(
                f"Plugin {info.name!r} raised during in-process execution.",
                details={"error": str(exc)},
            ) from exc
        self._audit(
            "warning",
            "In-process plugin run completed (no sandbox).",
            {
                "plugin": info.name,
                "version": info.version,
                "method": request.method,
                "duration_seconds": round(time.monotonic() - started, 4),
                "outcome": "ok",
            },
        )
        return {
            "ok": True,
            "result": result,
            "plugin": info.name,
            "method": request.method,
            "duration_seconds": round(time.monotonic() - started, 4),
            "isolation": {
                "mode": "in-process",
                "network_isolated": False,
                "network_isolation_requested": False,
                "unshare_available": False,
                "unshare_reason": "in-process execution was explicitly requested.",
                "cwd": "host",
            },
        }
