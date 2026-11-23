# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Audit logging.

The audit log is an append-only JSONL file. Every record passes through
:func:`fsociety.security.redaction.redact` before it is written, so secrets
cannot reach disk even if a caller passes them by mistake.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path
from typing import Any, Literal, cast

from .core.models import EventRecord, utcnow_iso
from .security.redaction import redact, redact_obj


class AuditLogger:
    def __init__(
        self,
        path: Path | None = None,
        *,
        level: str = "INFO",
        echo: bool = False,
        max_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        self.path = Path(path) if path else None
        self.echo = echo
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        self._logger = logging.getLogger("fsociety")
        if not self._logger.handlers:
            handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
            self._logger.addHandler(handler)
        self._logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def _rotate_if_needed(self) -> None:
        if not self.path or not self.path.exists():
            return
        if self.path.stat().st_size < self.max_bytes:
            return
        rotated = self.path.with_suffix(self.path.suffix + ".1")
        try:
            if rotated.exists():
                rotated.unlink()
            self.path.rename(rotated)
        except OSError:
            # Rotation is best-effort; never block the operation on it.
            pass

    def emit(
        self,
        message: str,
        *,
        level: str = "info",
        category: str = "system",
        data: dict[str, Any] | None = None,
    ) -> EventRecord:
        event = EventRecord(
            created_at=utcnow_iso(),
            level=cast('Literal["debug", "info", "warning", "error", "audit"]', level),
            category=category,
            message=redact(message),
            data=redact_obj(data or {}),
        )
        if self.echo or level in {"warning", "error", "audit"}:
            self._logger.log(
                {
                    "debug": logging.DEBUG,
                    "info": logging.INFO,
                    "warning": logging.WARNING,
                    "error": logging.ERROR,
                    "audit": logging.INFO,
                }.get(level, logging.INFO),
                "[%s] %s %s",
                category,
                event.message,
                json.dumps(event.data, ensure_ascii=False) if event.data else "",
            )
        if self.path:
            line = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
            with self._lock:
                self._rotate_if_needed()
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        return event

    def info(self, message: str, **kwargs: Any) -> EventRecord:
        return self.emit(message, level="info", **kwargs)

    def warning(self, message: str, **kwargs: Any) -> EventRecord:
        return self.emit(message, level="warning", **kwargs)

    def error(self, message: str, **kwargs: Any) -> EventRecord:
        return self.emit(message, level="error", **kwargs)

    def audit(self, message: str, **kwargs: Any) -> EventRecord:
        return self.emit(message, level="audit", **kwargs)

    def read_recent(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.path or not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        result: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return result
