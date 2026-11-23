# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Model-integrity scorer: verified-hash ratio for a manifest + model root.

Payload::

    {
      "root": "/path/to/model",                 # optional; defaults to manifest parent
      "manifest": "/path/to/manifest.json"      # or an inline dict
    }

The manifest may use ``{"files": {rel: {"sha256": ...}}}``, a list under
``files``, or a flat mapping of relative path to a checksum object/string.
Relative paths are confined to ``root``; traversal entries count as mismatches.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from fsociety import PLUGIN_API_VERSION
from fsociety.utils.hashing import sha256_file

from ...errors import PathSecurityError, PluginError
from ...paths import safe_join
from ..api import Scorer


def _extract_entries(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    raw_files = manifest.get("files")
    if isinstance(raw_files, dict):
        iterable = raw_files.items()
    elif isinstance(raw_files, list):
        iterable = [
            (str(entry.get("path") or entry.get("name") or ""), entry)
            for entry in raw_files
            if isinstance(entry, dict)
        ]
    else:
        iterable = manifest.items()
    for rel, entry in iterable:
        if not rel or not isinstance(rel, str):
            continue
        if isinstance(entry, str):
            entries[rel] = {"sha256": entry}
        elif isinstance(entry, dict) and (
            "sha256" in entry or "size" in entry or "size_bytes" in entry
        ):
            entries[rel] = entry
    return entries


class ModelIntegrityScorer(Scorer):
    name = "model_integrity"
    version = "1.0.0"
    api_version = PLUGIN_API_VERSION
    kind = "scorer"
    description = (
        "Verifies a SHA-256 manifest against a model root and scores the "
        "verified-file ratio."
    )
    author = "FSOCIETY AI contributors"
    license = "AGPL-3.0-or-later"
    dependencies: ClassVar[list[str]] = []
    # The scorer only reads the manifest/root paths the caller explicitly
    # supplies (confined with safe_join); it does not enumerate the filesystem
    # on its own, so no filesystem permission is requested.
    permissions: ClassVar[list[str]] = []

    def analyze(self, item: dict[str, Any]) -> dict[str, Any]:
        manifest_ref = item.get("manifest")
        if manifest_ref is None:
            raise PluginError(
                "ModelIntegrityScorer requires a 'manifest' (path or object).",
                hint="Pass {'manifest': '/path/manifest.json', 'root': '/path/model'}.",
            )
        manifest_path: Path | None = None
        if isinstance(manifest_ref, dict):
            manifest = manifest_ref
        elif isinstance(manifest_ref, str):
            manifest_path = Path(manifest_ref).expanduser().resolve()
            if not manifest_path.is_file():
                raise PluginError(
                    f"Model manifest not found: {manifest_path.name}.",
                    details={"path": str(manifest_path)},
                )
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise PluginError(
                    "Could not read model manifest.",
                    details={"error": str(exc)},
                ) from exc
            if not isinstance(manifest, dict):
                raise PluginError("Model manifest root must be a JSON object.")
        else:
            raise PluginError("Model manifest must be a path string or a JSON object.")

        root_ref = item.get("root")
        if root_ref is not None:
            if not isinstance(root_ref, str) or not root_ref.strip():
                raise PluginError("Model 'root' must be a non-empty path string.")
            root = Path(root_ref).expanduser().resolve()
        elif manifest_path is not None:
            root = manifest_path.parent
        else:
            raise PluginError(
                "Model 'root' is required when the manifest is passed inline.",
            )
        if not root.is_dir():
            raise PluginError(
                f"Model root is not a directory: {root.name}.",
                details={"path": str(root)},
            )

        entries = _extract_entries(manifest)
        if not entries:
            return {
                "score": 0.0,
                "verified": 0,
                "total": 0,
                "mismatches": [],
                "missing": [],
                "notes": ["manifest contains no file entries"],
                "heuristic": False,
            }

        verified = 0
        mismatches: list[str] = []
        missing: list[str] = []
        for rel, entry in sorted(entries.items()):
            try:
                target = safe_join(root, rel)
            except PathSecurityError:
                mismatches.append(rel)
                continue
            if not target.is_file():
                missing.append(rel)
                continue
            expected = entry.get("sha256")
            if not isinstance(expected, str) or not expected:
                mismatches.append(rel)
                continue
            try:
                actual = sha256_file(target)
            except OSError:
                mismatches.append(rel)
                continue
            if actual.lower() != expected.lower():
                mismatches.append(rel)
            else:
                verified += 1

        total = len(entries)
        score = verified / total if total else 0.0
        return {
            "score": round(score, 4),
            "verified": verified,
            "total": total,
            "mismatches": mismatches,
            "missing": missing,
            "root": str(root),
            "notes": [f"verified {verified}/{total} manifest entries"],
            "heuristic": False,
        }

    def score(self, item: dict[str, Any]) -> float:
        return self.analyze(item)["score"]
