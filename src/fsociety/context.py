# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Composition root.

The CLI and API share one :class:`PlatformContext`. It owns the settings,
workspace, store, audit logger and a cached hardware profile. Heavy integrations
(Heretic engine, model manager, evaluation backends) are imported lazily so that
``fsociety --help`` and the HTTP API work without PyTorch.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from . import PLUGIN_API_VERSION, SOURCE_URL, __platform_name__, __version__
from .config import PlatformSettings, load_settings
from .core.hardware import detect_hardware
from .core.models import HardwareProfile, SystemInfo
from .experiments.store import ExperimentStore
from .logging import AuditLogger
from .paths import Workspace


def _package_version(name: str) -> str | None:
    try:
        import importlib.metadata

        return importlib.metadata.version(name)
    except Exception:
        return None


def software_versions() -> dict[str, str]:
    """Versions recorded with every experiment for reproducibility."""
    packages = [
        "fsociety-ai",
        "heretic-llm",
        "torch",
        "transformers",
        "accelerate",
        "peft",
        "optuna",
        "bitsandbytes",
        "datasets",
        "huggingface-hub",
        "pydantic",
        "fastapi",
        "numpy",
    ]
    versions: dict[str, str] = {"fsociety-ai": __version__}
    for package in packages:
        if package == "fsociety-ai":
            continue
        version = _package_version(package)
        if version:
            versions[package] = version
    return versions


class PlatformContext:
    def __init__(
        self,
        settings: PlatformSettings | None = None,
        *,
        workspace: str | Path | None = None,
    ) -> None:
        if settings is None:
            settings = load_settings(
                workspace=Path(workspace).expanduser() if workspace else None
            )
        elif workspace is not None:
            settings = settings.model_copy(
                update={"workspace": Path(workspace).expanduser()}
            )
        self.settings = settings
        self.workspace = Workspace(settings.workspace)
        self.started_at = time.time()
        self._store: ExperimentStore | None = None
        self._logger: AuditLogger | None = None
        self._hardware: HardwareProfile | None = None
        self._hardware_at = 0.0
        self._lock = threading.RLock()

    # ---- components --------------------------------------------------------
    @property
    def store(self) -> ExperimentStore:
        with self._lock:
            if self._store is None:
                self._store = ExperimentStore(self.workspace.db_path)
            return self._store

    @property
    def logger(self) -> AuditLogger:
        with self._lock:
            if self._logger is None:
                self._logger = AuditLogger(
                    self.workspace.logs / "audit.jsonl"
                    if self.settings.audit_log_enabled
                    else None,
                    level=self.settings.log_level,
                    echo=False,
                )
            return self._logger

    def log(
        self,
        message: str,
        *,
        level: str = "info",
        category: str = "system",
        data: dict[str, Any] | None = None,
    ) -> None:
        event = self.logger.emit(message, level=level, category=category, data=data)
        try:
            self.store.add_event(event)
        except Exception:
            # Persistence of logs must never break the operation itself.
            self.logger.warning("Could not persist event to the experiment store.")

    # ---- hardware ----------------------------------------------------------
    def hardware(
        self, *, refresh: bool = False, ttl_seconds: float = 600.0
    ) -> HardwareProfile:
        with self._lock:
            stale = (time.time() - self._hardware_at) > ttl_seconds
            if refresh or self._hardware is None or stale:
                self._hardware = detect_hardware()
                self._hardware_at = time.time()
            return self._hardware

    # ---- engine ------------------------------------------------------------
    def engine_status(self) -> dict[str, Any]:
        """Report whether the Heretic engine dependencies are importable."""
        import importlib.util

        missing: list[str] = []
        for module in ("torch", "transformers", "optuna"):
            if importlib.util.find_spec(module) is None:
                missing.append(module)
        status: dict[str, Any] = {
            "available": not missing,
            "missing": missing,
            "heretic_version": _package_version("heretic-llm"),
            "torch_version": _package_version("torch"),
            "transformers_version": _package_version("transformers"),
        }
        if missing:
            status["message"] = (
                "The modification engine is unavailable. Install the engine dependencies "
                "to run model modification: " + ", ".join(missing)
            )
            status["hint"] = (
                "pip install fsociety-ai  (or `uv sync` in a source checkout)"
            )
        else:
            status["message"] = "Heretic engine available."
        return status

    # ---- info --------------------------------------------------------------
    def system_info(self) -> SystemInfo:
        engine = self.engine_status()
        return SystemInfo(
            platform_name=__platform_name__,
            version=__version__,
            plugin_api_version=PLUGIN_API_VERSION,
            engine_available=bool(engine["available"]),
            engine_version=engine.get("heretic_version"),
            workspace=str(self.workspace.root),
            source_url=SOURCE_URL,
        )

    def uptime_seconds(self) -> float:
        return max(0.0, time.time() - self.started_at)

    def close(self) -> None:
        with self._lock:
            if self._store is not None:
                self._store.close()
                self._store = None
