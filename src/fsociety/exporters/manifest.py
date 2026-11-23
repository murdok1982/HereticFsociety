# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Portable export bundles with a signed-by-hash manifest.

Layout::

    <destination>/
        manifest.json
        README.md
        files/<safe-name>

Creation stages everything in a sibling temporary directory and atomically
renames it, so a failed export never leaves a half-written bundle behind.
Verification detects modified, missing and unexpected files.

Symlinks are not followed outside their own directory; a symlink whose target
escapes that directory is rejected.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import __platform_name__, __version__
from ..core.models import ArtifactRecord, new_id, utcnow_iso
from ..errors import ExportError, PathSecurityError
from ..paths import safe_join
from ..utils.hashing import sha256_dir, sha256_file

SCHEMA_VERSION = 1
_ALLOWED_BUNDLE_FILES = frozenset({"manifest.json", "README.md"})


def _safe_basename(name: str) -> str:
    if not name or name in {".", ".."}:
        raise ExportError(f"Invalid export file name: {name!r}.")
    if "/" in name or "\\" in name or "\x00" in name:
        raise ExportError(
            f"Export file name must not contain path separators: {name!r}."
        )
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ExportError(f"Export file name contains control characters: {name!r}.")
    if len(name) > 255:
        raise ExportError("Export file name exceeds 255 characters.")
    return name


def _safe_relative_path(relative: str) -> Path:
    if not relative or "\x00" in relative or any(ord(ch) < 32 for ch in relative):
        raise ExportError(f"Invalid manifest path: {relative!r}.")
    normalized = relative.replace("\\", "/")
    if normalized.startswith("/") or ":" in normalized.split("/")[0]:
        raise ExportError(f"Absolute manifest path is not allowed: {relative!r}.")
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    if not parts or ".." in parts:
        raise ExportError(f"Manifest path traversal is not allowed: {relative!r}.")
    return Path(*parts)


def _jsonable_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        raise ExportError("Export metadata must be a JSON object.")
    try:
        json.dumps(metadata, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ExportError(
            "Export metadata is not JSON serializable.",
            details={"error": str(exc)},
        ) from exc
    return dict(metadata)


def _validate_source(file: Path) -> Path:
    path = Path(file).expanduser()
    if not path.exists():
        raise ExportError(f"Export source file does not exist: {path}.")
    if path.is_symlink():
        target = path.resolve()
        if target.parent != path.parent.resolve():
            raise ExportError(
                "Refusing to follow a symlink outside its source directory.",
                details={"file": str(path), "target": str(target)},
            )
        path = target
    if not path.is_file():
        raise ExportError(f"Export source is not a regular file: {path}.")
    return path


def create_export_bundle(
    destination: Path,
    *,
    files: list[Path],
    metadata: dict[str, Any],
    license_text: str | None = None,
    max_file_bytes: int | None = None,
) -> dict[str, Any]:
    """Create an export bundle and return an ``ArtifactRecord``-style dict.

    Raises :class:`ExportError` on unsafe names, symlink escapes, oversized
    files, duplicate basenames or a pre-existing destination.
    """
    target = Path(destination).expanduser()
    if target.exists():
        raise ExportError(
            f"Export destination already exists: {target.name}.",
            hint="Choose a new destination directory; exports are never overwritten.",
        )
    if not files:
        raise ExportError("An export bundle requires at least one file.")
    if license_text is not None and not isinstance(license_text, str):
        raise ExportError("License text must be a string when provided.")
    meta = _jsonable_metadata(metadata)
    if max_file_bytes is not None and (
        not isinstance(max_file_bytes, int) or max_file_bytes <= 0
    ):
        raise ExportError("max_file_bytes must be a positive integer when provided.")

    sources: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for file in files:
        source = _validate_source(Path(file))
        name = _safe_basename(source.name)
        if name in seen:
            raise ExportError(
                f"Duplicate export file name after sanitisation: {name!r}.",
                hint="Rename one of the sources; bundles use flat safe names.",
            )
        seen.add(name)
        size = source.stat().st_size
        if max_file_bytes is not None and size > max_file_bytes:
            raise ExportError(
                f"Export file {name!r} exceeds the {max_file_bytes} byte cap.",
                details={"size_bytes": size},
            )
        sources.append((source, name))

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.staging-", dir=str(target.parent))
    )
    try:
        files_dir = staging / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        entries: list[dict[str, Any]] = []
        for source, name in sources:
            copied = files_dir / name
            shutil.copyfile(source, copied)
            entries.append(
                {
                    "path": f"files/{name}",
                    "source_name": source.name,
                    "sha256": sha256_file(copied),
                    "size_bytes": copied.stat().st_size,
                }
            )

        bundle_id = new_id("bundle")
        created_at = utcnow_iso()
        manifest: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "bundle_id": bundle_id,
            "kind": "export_bundle",
            "created_at": created_at,
            "platform": {"name": __platform_name__, "version": __version__},
            "files": entries,
            "file_count": len(entries),
            "metadata": meta,
            "license": license_text,
            "attribution": meta.get("attribution") or meta.get("author"),
        }
        manifest_path = staging / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        readme_lines = [
            f"# {target.name}",
            "",
            f"Export bundle created {created_at} by {__platform_name__} {__version__}.",
            f"Bundle id: `{bundle_id}`.",
            "",
            "## Contents",
            "",
            "| File | Size (bytes) | SHA-256 |",
            "| --- | ---: | --- |",
        ]
        for entry in entries:
            readme_lines.append(
                f"| `{entry['path']}` | {entry['size_bytes']} | `{entry['sha256']}` |"
            )
        readme_lines += [
            "",
            "## Metadata",
            "",
            "```json",
            json.dumps(meta, indent=2, sort_keys=True, ensure_ascii=False),
            "```",
            "",
        ]
        if license_text:
            readme_lines += ["## License / attribution", "", license_text, ""]
        readme_lines += [
            "## Verification",
            "",
            "Verify integrity against `manifest.json` with the platform verifier:",
            "",
            "```bash",
            "python - <<'PY'",
            "from pathlib import Path",
            "from fsociety.exporters.manifest import verify_export",
            f"print(verify_export(Path({str(target)!r})))",
            "PY",
            "```",
            "",
            "Or compare each file in `manifest.json` against `sha256sum`.",
            "",
        ]
        (staging / "README.md").write_text("\n".join(readme_lines), encoding="utf-8")

        os.replace(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    total_size = sum(
        path.stat().st_size for path in target.rglob("*") if path.is_file()
    )
    record = ArtifactRecord(
        artifact_id=new_id("artifact"),
        kind="export_bundle",
        path=str(target),
        sha256=sha256_dir(target),
        size_bytes=total_size,
        metadata={
            "bundle_id": bundle_id,
            "file_count": len(sources),
            "manifest_sha256": sha256_file(target / "manifest.json"),
            "user_metadata": meta,
        },
    )
    result = record.model_dump(mode="json")
    result["manifest_path"] = str(target / "manifest.json")
    result["files"] = [entry["path"] for entry in entries]
    return result


def verify_export(directory: Path) -> dict[str, Any]:
    """Verify a bundle against its manifest.

    Returns a ``ModelValidation``-like dict with ``ok=False`` on any modified,
    missing or extra file, and ``details.error`` when the manifest is unusable.
    """
    root = Path(directory).expanduser().resolve()
    result: dict[str, Any] = {
        "model_id": root.name,
        "path": str(root),
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files_checked": 0,
        "hashes_verified": 0,
        "mismatches": [],
        "missing": [],
        "extra": [],
        "ok": False,
        "manifest_path": None,
        "details": {},
    }
    manifest_path = root / "manifest.json"
    if not root.is_dir():
        result["details"]["error"] = "export directory does not exist"
        return result
    if not manifest_path.is_file():
        result["details"]["error"] = "manifest.json is missing"
        return result
    result["manifest_path"] = str(manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result["details"]["error"] = f"manifest is unreadable: {exc}"
        return result
    if not isinstance(manifest, dict):
        result["details"]["error"] = "manifest root must be a JSON object"
        return result
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        result["details"]["error"] = "manifest contains no files list"
        return result

    result["details"]["bundle_id"] = manifest.get("bundle_id")
    result["details"]["schema_version"] = manifest.get("schema_version")
    expected: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            result["mismatches"].append("<invalid manifest entry>")
            continue
        relative = entry.get("path")
        if not isinstance(relative, str):
            result["mismatches"].append("<entry without path>")
            continue
        try:
            safe_relative = _safe_relative_path(relative)
            target = safe_join(root, *safe_relative.parts)
        except (ExportError, PathSecurityError) as exc:
            result["mismatches"].append(f"{relative}: unsafe path ({exc})")
            continue
        expected.add(safe_relative.as_posix())
        result["files_checked"] += 1
        if not target.is_file():
            result["missing"].append(relative)
            continue
        expected_hash = entry.get("sha256")
        if not isinstance(expected_hash, str) or not expected_hash:
            result["mismatches"].append(f"{relative}: missing sha256")
            continue
        if sha256_file(target).lower() == expected_hash.lower():
            result["hashes_verified"] += 1
        else:
            result["mismatches"].append(relative)

    actual: set[str] = set()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative in _ALLOWED_BUNDLE_FILES:
            continue
        actual.add(relative)
    result["extra"] = sorted(actual - expected)

    result["ok"] = not (result["mismatches"] or result["missing"] or result["extra"])
    return result
