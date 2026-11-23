# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Hugging Face Hub integration for the model manager.

The cache reader works completely offline and without ``huggingface_hub``
installed: it only knows the on-disk layout
(``<cache>/models--org--name/snapshots/<commit>`` plus ``refs/<ref>`` files).
Network operations import ``huggingface_hub`` lazily and are never triggered
implicitly by inspection or scanning.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...errors import (
    AuthorizationError,
    EngineUnavailableError,
    FSocietyError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from ...logging import AuditLogger
from ..models import ModelInfo
from .inspection import inspect_hf_cache_snapshot

if TYPE_CHECKING:
    from ...config import PlatformSettings

__all__ = [
    "download_snapshot",
    "find_cached_snapshot",
    "hub_metadata",
    "is_repo_id",
    "list_cached_models",
    "snapshot_commit",
]

_LOGGER = logging.getLogger(__name__)

#: ``org/name`` repository identifiers (no spaces, no traversal segments).
_REPO_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")

#: Git commit hashes used by the Hub (sha1 today, sha256 tolerated).
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")

#: Ref labels we understand (anything else is still read, but this is the
#: preferred order when several labels point at the same commit).
_PREFERRED_REFS = ("main", "master")


def _warn(logger: AuditLogger | None, message: str, **data: Any) -> None:
    if logger is not None:
        try:
            logger.emit(message, level="warning", category="models", data=data)
            return
        except (OSError, ValueError) as exc:  # pragma: no cover - defensive
            _LOGGER.warning("Audit logging failed (%s); falling back to stderr.", exc)
    _LOGGER.warning(message)


def is_repo_id(value: str) -> bool:
    """Whether ``value`` looks like a Hugging Face ``org/name`` identifier."""
    return isinstance(value, str) and bool(_REPO_ID_RE.match(value))


def _repo_id_from_cache_dir(directory: Path) -> str | None:
    name = directory.name
    prefix = "models--"
    if not name.startswith(prefix):
        return None
    remainder = name[len(prefix) :]
    organization, separator, model = remainder.partition("--")
    if not separator or not organization or not model:
        return None
    return f"{organization}/{model}"


def read_refs(repo_dir: Path, *, logger: AuditLogger | None = None) -> dict[str, str]:
    """Read ``refs/<label>`` files into a ``label -> commit`` mapping."""
    refs: dict[str, str] = {}
    refs_dir = repo_dir / "refs"
    if not refs_dir.is_dir():
        return refs
    try:
        entries = sorted(refs_dir.iterdir())
    except OSError as exc:
        _warn(logger, f"Could not list refs in {refs_dir}: {exc.strerror or exc}")
        return refs
    for entry in entries:
        if not entry.is_file() or entry.name.startswith("."):
            continue
        try:
            commit = entry.read_text(encoding="utf-8", errors="replace").strip()
        except OSError as exc:
            _warn(logger, f"Could not read ref {entry.name}: {exc.strerror or exc}")
            continue
        if _COMMIT_RE.match(commit):
            refs[entry.name] = commit
        elif commit:
            _warn(
                logger,
                f"Ignored ref {entry.name!r}: value is not a commit hash.",
                value=commit[:80],
            )
    return refs


def _revision_labels(refs: dict[str, str]) -> dict[str, str]:
    """Map ``commit -> preferred ref label`` for display."""
    labels: dict[str, str] = {}
    for commit in set(refs.values()):
        candidates = sorted(label for label, value in refs.items() if value == commit)
        preferred = next(
            (label for label in _PREFERRED_REFS if label in candidates),
            candidates[0] if candidates else None,
        )
        if preferred:
            labels[commit] = preferred
    return labels


def snapshot_commit(snapshot: Path) -> str | None:
    """The commit hash encoded in a cache snapshot path, if any."""
    return snapshot.name if _COMMIT_RE.match(snapshot.name) else None


def find_cached_snapshot(
    hf_cache: Path,
    repo_id: str,
    revision: str | None = None,
    *,
    logger: AuditLogger | None = None,
) -> tuple[Path, str, str | None] | None:
    """Locate a cached snapshot.

    Returns ``(snapshot_path, commit, ref_label)`` or ``None`` when the
    repository or revision is not cached. An ambiguous revision prefix raises
    :class:`~fsociety.errors.ValidationError` instead of guessing.
    """
    if not is_repo_id(repo_id):
        raise ValidationError(
            f"Invalid repository id: {repo_id!r}",
            hint="Use the 'org/name' form, for example 'meta-llama/Llama-3.1-8B'.",
        )
    cache = Path(hf_cache).expanduser()
    repo_dir = cache / f"models--{repo_id.replace('/', '--')}"
    if not repo_dir.is_dir():
        return None
    snapshots_dir = repo_dir / "snapshots"
    if not snapshots_dir.is_dir():
        return None
    refs = read_refs(repo_dir, logger=logger)
    labels = _revision_labels(refs)
    try:
        snapshots = sorted(
            entry
            for entry in snapshots_dir.iterdir()
            if entry.is_dir() and not entry.name.startswith(".")
        )
    except OSError as exc:
        _warn(
            logger,
            f"Could not list snapshots in {snapshots_dir}: {exc.strerror or exc}",
        )
        return None
    if not snapshots:
        return None

    if revision:
        target = refs.get(revision, revision)
        if _COMMIT_RE.match(target):
            exact = snapshots_dir / target
            if exact.is_dir():
                label = revision if refs.get(revision) == target else labels.get(target)
                return exact, target, label
            if len(target) >= 7:
                matches = [
                    entry for entry in snapshots if entry.name.startswith(target)
                ]
                if len(matches) == 1:
                    commit = matches[0].name
                    return matches[0], commit, labels.get(commit)
                if len(matches) > 1:
                    raise ValidationError(
                        f"Revision {revision!r} is ambiguous for {repo_id}: it matches "
                        f"{len(matches)} cached commits.",
                        details={"candidates": [entry.name for entry in matches]},
                        hint="Pass a longer commit prefix or the full 40-character hash.",
                    )
            return None
        # Not a hash: it may be a label that the refs directory does not know.
        for entry in snapshots:
            if entry.name == target:
                return entry, entry.name, labels.get(entry.name)
        return None

    main = refs.get("main")
    if main and (snapshots_dir / main).is_dir():
        return snapshots_dir / main, main, "main"
    try:
        newest = max(snapshots, key=lambda entry: entry.stat().st_mtime)
    except OSError as exc:
        _warn(
            logger,
            f"Could not stat cached snapshots in {snapshots_dir}: {exc.strerror or exc}",
        )
        newest = snapshots[0]
    return newest, newest.name, labels.get(newest.name)


def list_cached_models(
    hf_cache: Path, *, logger: AuditLogger | None = None
) -> list[ModelInfo]:
    """Inspect every snapshot in a ``huggingface_hub`` cache directory."""
    cache = Path(hf_cache).expanduser()
    if not cache.is_dir():
        return []
    try:
        repo_dirs = sorted(
            entry
            for entry in cache.glob("models--*")
            if entry.is_dir() and not entry.is_symlink()
        )
    except OSError as exc:
        _warn(
            logger, f"Could not list Hugging Face cache {cache}: {exc.strerror or exc}"
        )
        return []

    results: list[ModelInfo] = []
    seen: set[tuple[str, str]] = set()
    for repo_dir in repo_dirs:
        repo_id = _repo_id_from_cache_dir(repo_dir)
        if repo_id is None:
            continue
        snapshots_dir = repo_dir / "snapshots"
        if not snapshots_dir.is_dir():
            continue
        try:
            snapshots = sorted(
                entry
                for entry in snapshots_dir.iterdir()
                if entry.is_dir() and not entry.name.startswith(".")
            )
        except OSError as exc:
            _warn(
                logger,
                f"Could not list snapshots for {repo_id}: {exc.strerror or exc}",
                repo_id=repo_id,
            )
            continue
        refs = read_refs(repo_dir, logger=logger)
        labels = _revision_labels(refs)
        for snapshot in snapshots:
            commit = snapshot.name
            if (repo_id, commit) in seen:
                continue
            try:
                entries = list(snapshot.iterdir())
            except OSError as exc:
                _warn(
                    logger,
                    f"Could not read snapshot {commit[:12]} of {repo_id}: "
                    f"{exc.strerror or exc}",
                    repo_id=repo_id,
                )
                continue
            if not entries:
                continue
            try:
                info = inspect_hf_cache_snapshot(
                    snapshot, repo_id, commit, labels.get(commit)
                )
            except FSocietyError as exc:
                _warn(
                    logger,
                    f"Skipped cached snapshot {repo_id}@{commit[:12]}: {exc.message}",
                    repo_id=repo_id,
                    commit=commit,
                )
                continue
            seen.add((repo_id, commit))
            results.append(info)
    results.sort(key=lambda info: (info.model_id, info.commit or ""))
    return results


def _wrap_hub_error(exc: Exception, repo_id: str) -> FSocietyError:
    """Translate a huggingface_hub exception into a typed platform error."""
    name = type(exc).__name__
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status == 404 or "NotFound" in name:
        return NotFoundError(
            f"Repository {repo_id!r} was not found on the Hugging Face Hub.",
            details={"repo_id": repo_id, "status": status},
            hint="Check the spelling, or pass a token for a private repository.",
        )
    if status in (401, 403) or "Gated" in name or "Auth" in name:
        return AuthorizationError(
            f"Access to {repo_id!r} was denied by the Hugging Face Hub.",
            details={"repo_id": repo_id, "status": status},
            hint="Accept the repository license on huggingface.co and pass a token "
            "with read access.",
        )
    if status == 429:
        return RateLimitError(
            f"The Hugging Face Hub rate-limited the request for {repo_id!r}.",
            details={"repo_id": repo_id, "status": status},
            hint="Retry later or authenticate with a token.",
        )
    return FSocietyError(
        f"Hugging Face Hub request failed for {repo_id!r}: {exc}",
        details={"repo_id": repo_id, "exception": name},
        hint="Check network connectivity and proxy settings, then retry.",
    )


def _validate_patterns(allow_patterns: list[str] | None) -> list[str] | None:
    if allow_patterns is None:
        return None
    if not isinstance(allow_patterns, list) or not allow_patterns:
        raise ValidationError(
            "allow_patterns must be a non-empty list of glob patterns.",
            hint='Example: ["*.json", "*.safetensors", "tokenizer*"].',
        )
    cleaned: list[str] = []
    for pattern in allow_patterns:
        if not isinstance(pattern, str) or not pattern.strip():
            raise ValidationError(
                "Every allow_patterns entry must be a non-empty string."
            )
        if len(pattern) > 512:
            raise ValidationError(
                "allow_patterns entries must be at most 512 characters long."
            )
        if pattern.startswith(("/", "\\")) or ".." in pattern:
            raise ValidationError(
                f"Unsafe allow_patterns entry: {pattern!r}",
                hint="Patterns are relative globs inside the repository.",
            )
        cleaned.append(pattern)
    return cleaned


def download_snapshot(
    repo_id: str,
    revision: str | None = None,
    cache_dir: Path | None = None,
    allow_patterns: list[str] | None = None,
    token: str | None = None,
    *,
    confirm_weights: bool = False,
    settings: PlatformSettings | None = None,
) -> Path:
    """Download a repository snapshot and return the local snapshot path.

    A revision is always resolved to a commit: ``None`` means ``main`` here, and
    the returned path is the immutable ``snapshots/<commit>`` directory, whose
    name is the commit hash. Downloading model weights requires either explicit
    ``allow_patterns`` or ``confirm_weights=True``.
    """
    if not is_repo_id(repo_id):
        raise ValidationError(
            f"Invalid repository id: {repo_id!r}",
            hint="Use the 'org/name' form, for example 'meta-llama/Llama-3.1-8B'.",
        )
    if settings is not None and getattr(settings, "offline", False):
        raise ValidationError(
            "Offline mode is enabled; refusing to contact the Hugging Face Hub.",
            details={"repo_id": repo_id},
            hint="Set FSOCIETY_OFFLINE=0 (or offline = false in fsociety.toml) to "
            "download models.",
        )
    if revision is not None:
        if not isinstance(revision, str) or not revision.strip() or len(revision) > 200:
            raise ValidationError(
                "revision must be a non-empty ref name or commit hash (max 200 chars)."
            )
        if not re.match(r"^[A-Za-z0-9._/-]+$", revision):
            raise ValidationError(
                f"Invalid revision: {revision!r}",
                hint="Use a branch/tag name or a commit hash.",
            )
    patterns = _validate_patterns(allow_patterns)
    if patterns is None and not confirm_weights:
        raise ValidationError(
            "Refusing to download full model weights implicitly.",
            details={"repo_id": repo_id},
            hint="Pass allow_patterns to fetch metadata only (for example "
            '["*.json", "tokenizer*"]), or confirm_weights=True to download the '
            "complete snapshot.",
        )

    try:
        from huggingface_hub import snapshot_download
    except ModuleNotFoundError as exc:
        raise EngineUnavailableError(
            "huggingface_hub is required to download models.",
            details={"missing": ["huggingface_hub"]},
            hint="Install it with `pip install huggingface-hub`.",
        ) from exc

    kwargs: dict[str, Any] = {
        "repo_id": repo_id,
        "revision": revision or "main",
        "token": token,
    }
    if patterns is not None:
        kwargs["allow_patterns"] = patterns
    if cache_dir is not None:
        kwargs["cache_dir"] = str(Path(cache_dir).expanduser())
    try:
        path = snapshot_download(**kwargs)
    except Exception as exc:
        raise _wrap_hub_error(exc, repo_id) from exc
    snapshot = Path(path)
    if not snapshot.is_dir():
        raise FSocietyError(
            f"huggingface_hub returned a non-directory snapshot path for {repo_id!r}: "
            f"{snapshot}",
            details={"repo_id": repo_id, "path": str(snapshot)},
        )
    return snapshot


def hub_metadata(
    repo_id: str,
    revision: str | None = None,
    *,
    token: str | None = None,
    settings: PlatformSettings | None = None,
) -> dict[str, Any]:
    """Fetch repository metadata from the Hub (explicit network operation)."""
    if not is_repo_id(repo_id):
        raise ValidationError(
            f"Invalid repository id: {repo_id!r}",
            hint="Use the 'org/name' form.",
        )
    if settings is not None and getattr(settings, "offline", False):
        raise ValidationError(
            "Offline mode is enabled; Hub metadata is unavailable.",
            details={"repo_id": repo_id},
            hint="Disable offline mode to query the Hub.",
        )
    try:
        from huggingface_hub import HfApi
    except ModuleNotFoundError as exc:
        raise EngineUnavailableError(
            "huggingface_hub is required to query the Hugging Face Hub.",
            details={"missing": ["huggingface_hub"]},
            hint="Install it with `pip install huggingface-hub`.",
        ) from exc
    api = HfApi()
    try:
        info = api.model_info(repo_id, revision=revision, token=token)
    except Exception as exc:
        raise _wrap_hub_error(exc, repo_id) from exc

    tags = [tag for tag in (getattr(info, "tags", None) or []) if isinstance(tag, str)]
    license_id: str | None = None
    card = getattr(info, "cardData", None)
    if isinstance(card, dict):
        raw_license = card.get("license")
        if isinstance(raw_license, str) and raw_license:
            license_id = raw_license
        elif isinstance(raw_license, list) and raw_license:
            license_id = str(raw_license[0])
    if license_id is None:
        license_id = next(
            (tag.split(":", 1)[1] for tag in tags if tag.startswith("license:")), None
        )

    last_modified = getattr(info, "lastModified", None)
    return {
        "model_id": getattr(info, "id", repo_id) or repo_id,
        "sha": getattr(info, "sha", None),
        "revision": revision,
        "license": license_id,
        "tags": tags,
        "downloads": getattr(info, "downloads", None),
        "likes": getattr(info, "likes", None),
        "pipeline_tag": getattr(info, "pipeline_tag", None),
        "private": bool(getattr(info, "private", False)),
        "gated": getattr(info, "gated", None),
        "last_modified": last_modified.isoformat()
        if hasattr(last_modified, "isoformat")
        else None,
    }
