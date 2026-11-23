# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Public model-management API.

:class:`ModelManager` is the single entry point used by the CLI and the HTTP
API. It resolves user-supplied references (local paths, Hugging Face cache
entries, Hub ids) through the path jail, inspects them without torch, verifies
SHA-256 manifests and downloads snapshots with an explicit revision.

Every operation emits an audit event with category ``models`` when an
:class:`~fsociety.logging.AuditLogger` is supplied.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from ...config import PlatformSettings
from ...errors import (
    FSocietyError,
    NotFoundError,
    PathSecurityError,
    ValidationError,
)
from ...logging import AuditLogger
from ...paths import safe_join
from ...utils.hashing import file_manifest, verify_manifest
from ..models import ModelInfo, ModelValidation
from . import hf
from .hf import download_snapshot, hub_metadata, is_repo_id, snapshot_commit
from .inspection import (
    SKIP_DIR_NAMES,
    compatibility_notes,
    count_safetensors_parameters,
    inspect_hf_cache_snapshot,
    inspect_local_dir,
    looks_like_model_dir,
    read_safetensors_header,
)

__all__ = [
    "ModelManager",
    "compatibility_notes",
    "count_safetensors_parameters",
    "download_snapshot",
    "hub_metadata",
    "inspect_hf_cache_snapshot",
    "inspect_local_dir",
    "list_cached_models",
    "read_safetensors_header",
]

_LOGGER = logging.getLogger(__name__)

#: Control characters are never valid in a model reference.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

#: Absolute ceiling on reference length (paths and repo ids are far shorter).
_MAX_REFERENCE_LENGTH = 4096

#: Manifest files above this size are absurd and rejected before parsing.
_MAX_MANIFEST_BYTES = 64 * 1024 * 1024

#: Defensive ceiling on manifest entries.
_MAX_MANIFEST_ENTRIES = 200_000

#: Directory depth explored when looking for models below a scan root.
_MAX_SCAN_DEPTH = 2

#: Directory ceiling per scan root (protects against pointing a scan at ``/``).
_MAX_SCAN_DIRS = 2000

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REVISION_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


def _validate_reference(reference: str) -> str:
    if not isinstance(reference, str):
        raise ValidationError("Model reference must be a string.")
    value = reference.strip()
    if not value:
        raise ValidationError(
            "Model reference is empty.",
            hint="Pass a local directory, a cached repository id, or an 'org/name' Hub id.",
        )
    if len(value) > _MAX_REFERENCE_LENGTH:
        raise ValidationError(
            f"Model reference is longer than {_MAX_REFERENCE_LENGTH} characters."
        )
    if _CONTROL_CHARS_RE.search(value):
        raise ValidationError("Model reference contains control characters.")
    if value in {".", "..", "/", "\\"}:
        raise ValidationError(f"Invalid model reference: {value!r}")
    return value


def _validate_revision(revision: str | None) -> str | None:
    if revision is None:
        return None
    if not isinstance(revision, str) or not revision.strip():
        raise ValidationError("revision must be a non-empty string when provided.")
    value = revision.strip()
    if len(value) > 200 or not _REVISION_RE.match(value) or ".." in value:
        raise ValidationError(
            f"Invalid revision: {revision!r}",
            hint="Use a branch/tag name or a commit hash.",
        )
    return value


def _validate_manifest(data: Any) -> dict[str, dict[str, Any]]:
    """Strictly validate a SHA-256 manifest read from an untrusted file."""
    if not isinstance(data, dict):
        raise ValidationError(
            "Manifest must be a JSON object mapping relative paths to hashes."
        )
    entries: Any = data
    files_value = data.get("files")
    if isinstance(files_value, dict) and files_value:
        metadata_keys = {
            "files",
            "schema_version",
            "model_id",
            "path",
            "created_at",
            "generated_at",
            "note",
        }
        if set(data) <= metadata_keys:
            entries = files_value
    if not isinstance(entries, dict) or not entries:
        raise ValidationError("Manifest contains no file entries.")
    if len(entries) > _MAX_MANIFEST_ENTRIES:
        raise ValidationError(
            f"Manifest has more than {_MAX_MANIFEST_ENTRIES} entries; refusing to verify."
        )
    validated: dict[str, dict[str, Any]] = {}
    for relative, entry in entries.items():
        if not isinstance(relative, str) or not relative.strip():
            raise ValidationError("Manifest keys must be non-empty relative paths.")
        if (
            relative.startswith(("/", "\\"))
            or "\\" in relative
            or ".." in relative.split("/")
        ):
            raise PathSecurityError(
                f"Manifest entry escapes the model directory: {relative!r}",
                hint="Manifests must only contain relative paths inside the model.",
            )
        if not isinstance(entry, dict):
            raise ValidationError(f"Manifest entry for {relative!r} must be an object.")
        digest = entry.get("sha256")
        if not isinstance(digest, str) or not _SHA256_RE.match(digest.lower()):
            raise ValidationError(
                f"Manifest entry for {relative!r} has no valid SHA-256 digest."
            )
        size = entry.get("size")
        if size is not None and (
            not isinstance(size, int) or isinstance(size, bool) or size < 0
        ):
            raise ValidationError(
                f"Manifest entry for {relative!r} has an invalid size."
            )
        validated[relative] = {"sha256": digest.lower()}
        if size is not None:
            validated[relative]["size"] = size
    return validated


class ModelManager:
    """Torch-free discovery, inspection and verification of models."""

    def __init__(
        self, settings: PlatformSettings, logger: AuditLogger | None = None
    ) -> None:
        if settings is None:
            raise ValidationError("ModelManager requires platform settings.")
        self.settings = settings
        self.logger = logger

    # ---- logging -----------------------------------------------------------
    def _log(
        self, message: str, *, level: str = "info", data: dict[str, Any] | None = None
    ) -> None:
        if self.logger is not None:
            try:
                self.logger.emit(
                    message, level=level, category="models", data=data or {}
                )
                return
            except (OSError, ValueError) as exc:  # pragma: no cover - defensive
                _LOGGER.warning(
                    "Audit logging failed (%s); falling back to the standard logger.",
                    exc,
                )
        _LOGGER.debug("%s %s", message, data or {})

    # ---- path resolution ---------------------------------------------------
    def _bases(self) -> list[Path]:
        candidates = [self.settings.workspace, self.settings.resolved_models_dir()]
        unique: list[Path] = []
        for candidate in candidates:
            if candidate is not None and candidate not in unique:
                unique.append(candidate)
        return unique

    def resolve(self, reference: str) -> Path:
        """Resolve a local reference through the path jail."""
        value = _validate_reference(reference)
        raw = Path(value).expanduser()
        if raw.is_absolute():
            if not self.settings.allow_absolute_model_paths:
                raise PathSecurityError(
                    "Absolute model paths are disabled by policy.",
                    details={"path": str(raw)},
                    hint="Set FSOCIETY_ALLOW_ABSOLUTE_MODEL_PATHS=true to allow them, "
                    "or place the model inside the workspace.",
                )
            resolved = raw.resolve()
            if not resolved.exists():
                raise NotFoundError(
                    f"Model path does not exist: {resolved}",
                    details={"path": str(resolved)},
                )
            if not resolved.is_dir():
                raise ValidationError(
                    f"Model path is not a directory: {resolved}",
                    details={"path": str(resolved)},
                )
            return resolved
        tried: list[str] = []
        for base in self._bases():
            candidate = safe_join(base, value)
            tried.append(str(candidate))
            if candidate.is_dir():
                return candidate
        raise NotFoundError(
            f"No model directory matches {value!r}.",
            details={"reference": value, "searched": tried},
            hint="Place the model under the workspace, or pass an absolute path "
            "if absolute paths are allowed.",
        )

    def _resolve_local(self, value: str) -> Path | None:
        """Best-effort local resolution; ``None`` means "not a local directory"."""
        raw = Path(value).expanduser()
        if raw.is_absolute():
            if not self.settings.allow_absolute_model_paths:
                raise PathSecurityError(
                    "Absolute model paths are disabled by policy.",
                    details={"path": str(raw)},
                    hint="Set FSOCIETY_ALLOW_ABSOLUTE_MODEL_PATHS=true to allow them.",
                )
            resolved = raw.resolve()
            if resolved.exists() and not resolved.is_dir():
                raise ValidationError(
                    f"Model path is not a directory: {resolved}",
                    details={"path": str(resolved)},
                )
            return resolved if resolved.is_dir() else None
        for base in self._bases():
            candidate = safe_join(base, value)
            if candidate.is_dir():
                return candidate
        return None

    # ---- inspection --------------------------------------------------------
    def inspect(
        self,
        reference: str,
        *,
        revision: str | None = None,
        hash_files: bool = False,
    ) -> ModelInfo:
        """Inspect a local directory, a cached repository, or a Hub repository."""
        value = _validate_reference(reference)
        revision = _validate_revision(revision)
        local = self._resolve_local(value)
        if local is not None:
            info = inspect_local_dir(local, hash_files=hash_files)
            if revision is not None:
                info.revision = revision
            self._log(
                "Inspected local model.",
                data={
                    "model_id": info.model_id,
                    "path": info.path,
                    "parameter_count": info.parameter_count,
                    "metadata_source": info.metadata_source,
                },
            )
            return info
        if is_repo_id(value):
            return self._inspect_repo(value, revision, hash_files=hash_files)
        raise NotFoundError(
            f"Model {value!r} was not found locally and is not a valid Hub id.",
            details={
                "reference": value,
                "searched": [str(path) for path in self._bases()],
            },
            hint="Pass a directory, a cached 'org/name' repository, or a Hub id.",
        )

    def _inspect_repo(
        self, repo_id: str, revision: str | None, *, hash_files: bool = False
    ) -> ModelInfo:
        cache = self.settings.resolved_hf_cache()
        found = hf.find_cached_snapshot(cache, repo_id, revision, logger=self.logger)
        if found is not None:
            snapshot, commit, label = found
            info = inspect_local_dir(snapshot, hash_files=hash_files)
            info.source = "huggingface_cache"
            info.model_id = repo_id
            info.commit = commit
            info.revision = revision or label
            self._log(
                "Inspected cached Hugging Face snapshot.",
                data={
                    "model_id": repo_id,
                    "commit": commit,
                    "revision": info.revision,
                    "path": info.path,
                },
            )
            return info
        if self.settings.offline:
            raise NotFoundError(
                f"{repo_id!r} is not present in the local Hugging Face cache.",
                details={"repo_id": repo_id, "cache": str(cache), "revision": revision},
                hint="Offline mode is enabled. Disable it to reach the Hub, or "
                f"download the model first: fsociety models download {repo_id}",
            )
        try:
            metadata = hub_metadata(repo_id, revision, settings=self.settings)
        except FSocietyError as exc:
            raise NotFoundError(
                f"{repo_id!r} is not cached locally and the Hugging Face Hub could "
                "not be reached.",
                details={
                    "repo_id": repo_id,
                    "revision": revision,
                    "cache": str(cache),
                    "cause": exc.code,
                },
                hint="Check network connectivity, or download the snapshot explicitly "
                "with `fsociety models download`.",
            ) from exc
        tags = [tag for tag in metadata.get("tags") or [] if isinstance(tag, str)]
        info = ModelInfo(
            model_id=str(metadata.get("model_id") or repo_id),
            source="huggingface_hub",
            revision=revision or "main",
            commit=metadata.get("sha"),
            license=metadata.get("license"),
            tags=tags,
            metadata_source="declared",
            warnings=[
                (
                    "Weights are not available locally; architecture and parameter "
                    "count stay unknown until the snapshot is downloaded."
                )
            ],
        )
        info.compatibility = compatibility_notes(info)
        self._log(
            "Inspected remote Hub metadata (weights not downloaded).",
            data={"model_id": repo_id, "revision": revision, "commit": info.commit},
        )
        return info

    def _discover_model_dirs(self, root: Path) -> list[Path]:
        found: list[Path] = []
        queue: list[tuple[Path, int]] = [(root, 0)]
        visited = 0
        while queue:
            current, depth = queue.pop(0)
            if depth >= _MAX_SCAN_DEPTH:
                continue
            try:
                entries = sorted(
                    entry
                    for entry in current.iterdir()
                    if entry.is_dir()
                    and not entry.is_symlink()
                    and not entry.name.startswith(".")
                    and entry.name not in SKIP_DIR_NAMES
                    and not entry.name.startswith("models--")
                )
            except OSError as exc:
                self._log(
                    f"Could not read directory {current}: {exc.strerror or exc}",
                    level="warning",
                    data={"directory": str(current)},
                )
                continue
            for entry in entries:
                visited += 1
                if visited > _MAX_SCAN_DIRS:
                    self._log(
                        f"Scan of {root} stopped after {_MAX_SCAN_DIRS} directories.",
                        level="warning",
                        data={"root": str(root)},
                    )
                    return found
                if looks_like_model_dir(entry):
                    found.append(entry)
                else:
                    queue.append((entry, depth + 1))
        return found

    def scan(
        self,
        roots: list[Path] | None = None,
        *,
        include_hf_cache: bool = True,
        limit: int = 500,
    ) -> list[ModelInfo]:
        """Discover models under ``roots`` and (optionally) in the HF cache."""
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValidationError("limit must be a positive integer.")
        if limit > 100_000:
            raise ValidationError("limit must be at most 100000.")
        base = self.settings.workspace or self.settings.resolved_models_dir()
        requested = (
            list(roots) if roots is not None else [self.settings.resolved_models_dir()]
        )
        resolved_roots: list[Path] = []
        for root in requested:
            path = Path(root).expanduser()
            if not path.is_absolute():
                path = safe_join(base, str(root))
            if not path.exists():
                self._log(
                    f"Scan root does not exist: {path}",
                    level="warning",
                    data={"root": str(path)},
                )
                continue
            if not path.is_dir():
                self._log(
                    f"Scan root is not a directory: {path}",
                    level="warning",
                    data={"root": str(path)},
                )
                continue
            resolved_roots.append(path)

        results: list[ModelInfo] = []
        seen_paths: set[str] = set()
        for root in resolved_roots:
            if len(results) >= limit:
                break
            candidates = (
                [root]
                if looks_like_model_dir(root)
                else self._discover_model_dirs(root)
            )
            for candidate in candidates:
                if len(results) >= limit:
                    break
                key = str(candidate)
                if key in seen_paths:
                    continue
                seen_paths.add(key)
                try:
                    info = inspect_local_dir(candidate)
                except FSocietyError as exc:
                    self._log(
                        f"Skipped unreadable model directory {candidate}: {exc.message}",
                        level="warning",
                        data={"path": str(candidate), "code": exc.code},
                    )
                    continue
                except OSError as exc:
                    self._log(
                        f"Skipped model directory {candidate}: {exc.strerror or exc}",
                        level="warning",
                        data={"path": str(candidate)},
                    )
                    continue
                results.append(info)

        if include_hf_cache and len(results) < limit:
            seen_cached: set[tuple[str, str]] = set()
            try:
                cached = self.list_cached()
            except (FSocietyError, OSError) as exc:
                self._log(
                    f"Could not scan the Hugging Face cache: {exc}",
                    level="warning",
                    data={"cache": str(self.settings.resolved_hf_cache())},
                )
                cached = []
            for info in cached:
                if len(results) >= limit:
                    break
                key = (info.model_id, info.commit or "")
                if key in seen_cached:
                    continue
                seen_cached.add(key)
                results.append(info)

        self._log(
            f"Model scan found {len(results)} model(s).",
            data={
                "roots": [str(root) for root in resolved_roots],
                "include_hf_cache": include_hf_cache,
                "limit": limit,
                "found": len(results),
            },
        )
        return results

    def list_cached(self) -> list[ModelInfo]:
        """All snapshots in the local Hugging Face cache."""
        cache = self.settings.resolved_hf_cache()
        infos = hf.list_cached_models(cache, logger=self.logger)
        self._log(
            f"Found {len(infos)} cached Hugging Face snapshot(s).",
            data={"cache": str(cache), "snapshots": len(infos)},
        )
        return infos

    # ---- integrity ---------------------------------------------------------
    def manifest(self, reference: str) -> dict[str, dict[str, Any]]:
        """Compute a SHA-256 manifest for a locally available model."""
        info = self.inspect(reference)
        if not info.path:
            raise NotFoundError(
                f"Model {info.model_id!r} has no local files.",
                hint="Download the snapshot before computing a manifest.",
            )
        entries = file_manifest(Path(info.path))
        self._log(
            "Computed SHA-256 manifest.",
            data={"model_id": info.model_id, "files": len(entries)},
        )
        return entries

    def verify(
        self,
        reference: str,
        *,
        manifest: dict[str, Any] | None = None,
        manifest_path: Path | None = None,
    ) -> ModelValidation:
        """Verify a model against a manifest, or build a baseline manifest.

        Without a reference manifest nothing can be verified: the result is
        ``ok=False`` with ``details["mode"] == "manifest_created"`` and the
        freshly computed manifest embedded, so callers can persist it and re-run.
        """
        if manifest is not None and manifest_path is not None:
            raise ValidationError(
                "Pass either manifest or manifest_path, not both.",
            )
        info = self.inspect(reference)
        if not info.path:
            raise NotFoundError(
                f"Model {info.model_id!r} has no local files to verify.",
                hint="Download the snapshot first, then re-run verification.",
            )
        root = Path(info.path)
        validation = ModelValidation(model_id=info.model_id, path=str(root))

        source: Any = manifest
        if manifest_path is not None:
            path = Path(manifest_path).expanduser()
            try:
                size = path.stat().st_size
            except OSError as exc:
                raise NotFoundError(
                    f"Manifest file could not be read: {path}",
                    details={"path": str(path)},
                    hint="Check the --manifest path.",
                ) from exc
            if size > _MAX_MANIFEST_BYTES:
                raise ValidationError(
                    f"Manifest file is larger than {_MAX_MANIFEST_BYTES} bytes."
                )
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise NotFoundError(
                    f"Manifest file could not be read: {path}",
                    details={"path": str(path)},
                ) from exc
            try:
                source = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValidationError(
                    f"Manifest is not valid JSON: {exc}",
                    details={"path": str(path)},
                ) from exc
            validation.manifest_path = str(path)

        if source is not None:
            entries = _validate_manifest(source)
            mismatches, missing = verify_manifest(root, entries)
            validation.files_checked = len(entries)
            validation.mismatches = mismatches
            validation.missing = missing
            validation.hashes_verified = len(entries) - len(missing) - len(mismatches)
            validation.ok = not mismatches and not missing
            validation.details = {
                "mode": "verified",
                "manifest_entries": len(entries),
                "manifest_sha256": hashlib.sha256(
                    json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
            }
        else:
            entries = file_manifest(root)
            validation.files_checked = len(entries)
            validation.hashes_verified = 0
            validation.ok = False
            validation.details = {
                "mode": "manifest_created",
                "manifest_entries": len(entries),
                "manifest": entries,
                "note": "No reference manifest was supplied, so nothing was verified. "
                "Save this manifest and re-run verification with it to detect "
                "tampering.",
            }

        self._log(
            "Model verification finished.",
            level="info" if validation.ok else "warning",
            data={
                "model_id": info.model_id,
                "ok": validation.ok,
                "mode": validation.details.get("mode"),
                "mismatches": len(validation.mismatches),
                "missing": len(validation.missing),
            },
        )
        return validation

    # ---- compatibility -----------------------------------------------------
    def compatibility(self, info: ModelInfo) -> list[str]:
        """Architecture hints and caveats for one model."""
        return compatibility_notes(info)

    # ---- downloads ---------------------------------------------------------
    def download(
        self,
        repo_id: str,
        *,
        revision: str | None = None,
        cache_dir: Path | None = None,
        allow_patterns: list[str] | None = None,
        token: str | None = None,
        confirm_weights: bool = False,
    ) -> dict[str, Any]:
        """Download a Hub snapshot and report its resolved commit."""
        revision = _validate_revision(revision)
        target_cache = (
            Path(cache_dir).expanduser()
            if cache_dir is not None
            else self.settings.resolved_hf_cache()
        )
        snapshot = download_snapshot(
            repo_id,
            revision,
            target_cache,
            allow_patterns,
            token,
            confirm_weights=confirm_weights,
            settings=self.settings,
        )
        commit = snapshot_commit(snapshot)
        if commit is None:
            try:
                commit = hub_metadata(
                    repo_id, revision, token=token, settings=self.settings
                ).get("sha")
            except FSocietyError as exc:
                self._log(
                    "Could not resolve the downloaded commit via Hub metadata.",
                    level="warning",
                    data={"repo_id": repo_id, "error": exc.message},
                )
        result: dict[str, Any] = {
            "model_id": repo_id,
            "revision": revision or "main",
            "commit": commit,
            "path": str(snapshot),
        }
        self._log(
            "Downloaded model snapshot.",
            data={
                "repo_id": repo_id,
                "revision": result["revision"],
                "commit": commit,
                "path": str(snapshot),
            },
        )
        return result


def list_cached_models(
    hf_cache: Path, *, logger: AuditLogger | None = None
) -> list[ModelInfo]:
    """Module-level convenience wrapper around :func:`hf.list_cached_models`."""
    return hf.list_cached_models(hf_cache, logger=logger)
