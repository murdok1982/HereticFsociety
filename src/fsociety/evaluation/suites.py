# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Bundled benchmark suites and their integrity registry.

Suites are plain JSON documents shipped inside
``fsociety/evaluation/benchmarks``. Each document is validated with a strict
Pydantic model at load time, assigned a content hash over its canonical JSON
form (``suite_hash``), and verified against ``_manifest.json`` so silent edits
to a shipped suite are detected.

The bundled suites are deliberately small, hand-written smoke suites. They are
**not** MMLU, GSM8K, HumanEval or any other official benchmark, and scores must
never be reported as official benchmark results.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from pydantic import Field
from pydantic import ValidationError as PydanticValidationError

from ..core.models import BenchmarkItem, BenchmarkSuiteInfo, StrictModel
from ..errors import EvaluationError, NotFoundError, ValidationError
from ..utils.hashing import sha256_bytes, sha256_text
from .graders import available_graders, get_grader

DEFAULT_SUITE_DIR = Path(__file__).resolve().parent / "benchmarks"
MANIFEST_FILENAME = "_manifest.json"
MANIFEST_VERSION = 1
SUITE_HASH_LENGTH = 64


class SuiteItemSpec(StrictModel):
    """One hand-written benchmark item as stored on disk."""

    item_id: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=20_000)
    expected: str | None = Field(default=None, max_length=20_000)
    category: str = Field(default="general", min_length=1, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SuiteDocument(StrictModel):
    """Full suite document, validated at the trust boundary."""

    suite_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    name: str = Field(min_length=3, max_length=200)
    version: str = Field(min_length=1, max_length=32)
    description: str = Field(min_length=10, max_length=4_000)
    grader: str = Field(min_length=1, max_length=64)
    language: str = Field(default="en", min_length=2, max_length=8)
    tags: list[str] = Field(default_factory=list)
    provenance: str = Field(min_length=20, max_length=8_000)
    limitations: list[str] = Field(default_factory=list)
    items: list[SuiteItemSpec] = Field(min_length=1, max_length=2_000)


def canonical_suite_json(document: SuiteDocument) -> str:
    """Formatting-independent canonical JSON used for content hashing."""
    payload = document.model_dump(mode="json")
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def suite_sha256(document: SuiteDocument) -> str:
    """SHA-256 over the canonical JSON of a suite document."""
    return sha256_text(canonical_suite_json(document))


@dataclass(frozen=True)
class LoadedSuite:
    document: SuiteDocument
    info: BenchmarkSuiteInfo
    items: tuple[BenchmarkItem, ...]
    file_path: Path
    file_sha256: str


def _atomic_write_text(path: Path, text: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:  # pragma: no cover - best effort cleanup
                pass
    return path


class SuiteRegistry:
    """Loads, validates and hashes every bundled suite in a directory."""

    def __init__(
        self,
        directory: str | Path | None = None,
        *,
        verify_manifest: bool = True,
    ) -> None:
        self.directory = (
            Path(directory).expanduser() if directory else DEFAULT_SUITE_DIR
        )
        self.verify_manifest = verify_manifest
        self.warnings: list[str] = []
        self.manifest_verified = False
        self._suites: dict[str, LoadedSuite] = {}
        self._manifest: dict[str, Any] | None = None
        self._load_all()

    # ---- loading -----------------------------------------------------------
    def _load_all(self) -> None:
        if not self.directory.is_dir():
            raise EvaluationError(
                f"Benchmark suite directory not found: {self.directory}",
                hint="Reinstall the package or pass an explicit suite directory.",
            )
        files = sorted(
            path
            for path in self.directory.glob("*.json")
            if not path.name.startswith("_")
        )
        if not files:
            raise EvaluationError(
                f"No benchmark suite files found in {self.directory}",
                hint="Expected at least one '*.json' suite document.",
            )

        for path in files:
            self._load_file(path)

        manifest_path = self.directory / MANIFEST_FILENAME
        if manifest_path.is_file() and self.verify_manifest:
            self._manifest = self._read_manifest(manifest_path)
            self._verify_against_manifest(manifest_path)
        elif self.verify_manifest:
            self.warnings.append(
                f"No suite manifest at {manifest_path}; hashes were computed but not "
                "verified against the shipped integrity manifest."
            )

    def _load_file(self, path: Path) -> None:
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise EvaluationError(
                f"Could not read suite file {path.name}: {exc}",
                details={"path": str(path)},
            ) from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError(
                f"Suite file {path.name} is not valid UTF-8 JSON: {exc}",
                details={"path": str(path)},
            ) from exc
        if not isinstance(payload, dict):
            raise ValidationError(
                f"Suite file {path.name} must contain a JSON object.",
                details={"path": str(path)},
            )
        try:
            document = SuiteDocument.model_validate(payload)
        except PydanticValidationError as exc:
            raise ValidationError(
                f"Suite file {path.name} failed schema validation.",
                details={
                    "path": str(path),
                    "errors": exc.errors(include_url=False, include_context=False),
                },
                hint="Fix the suite JSON; see fsociety.evaluation.suites.SuiteDocument.",
            ) from exc

        if document.suite_id in self._suites:
            previous = self._suites[document.suite_id].file_path.name
            raise ValidationError(
                f"Duplicate suite_id {document.suite_id!r} in {path.name} "
                f"(already defined by {previous}).",
                details={"path": str(path), "suite_id": document.suite_id},
            )
        try:
            get_grader(document.grader)
        except ValidationError as exc:
            raise ValidationError(
                f"Suite {document.suite_id!r} references unknown grader "
                f"{document.grader!r}.",
                details={
                    "path": str(path),
                    "available_graders": available_graders(),
                },
                hint=exc.hint,
            ) from exc

        item_ids = [item.item_id for item in document.items]
        duplicates = sorted({item for item in item_ids if item_ids.count(item) > 1})
        if duplicates:
            raise ValidationError(
                f"Suite {document.suite_id!r} has duplicate item ids: {duplicates}",
                details={"path": str(path), "duplicate_item_ids": duplicates},
            )

        items = tuple(
            BenchmarkItem(
                item_id=item.item_id,
                prompt=item.prompt,
                expected=item.expected,
                category=item.category,
                metadata=dict(item.metadata),
            )
            for item in document.items
        )
        digest = suite_sha256(document)
        info = BenchmarkSuiteInfo(
            suite_id=document.suite_id,
            name=document.name,
            version=document.version,
            description=document.description,
            grader=document.grader,
            language=document.language,
            categories=sorted({item.category for item in items}),
            item_count=len(items),
            sha256=digest,
            tags=list(document.tags),
            source="builtin",
            provenance=document.provenance,
            limitations=list(document.limitations),
        )
        self._suites[document.suite_id] = LoadedSuite(
            document=document,
            info=info,
            items=items,
            file_path=path,
            file_sha256=sha256_bytes(raw),
        )

    def _read_manifest(self, path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EvaluationError(
                f"Suite manifest is unreadable: {path.name}: {exc}",
                details={"path": str(path)},
                hint="Regenerate it with fsociety.evaluation.suites.rebuild_manifest().",
            ) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("suites"), dict):
            raise EvaluationError(
                f"Suite manifest {path.name} has an unexpected shape.",
                hint="Regenerate it with fsociety.evaluation.suites.rebuild_manifest().",
            )
        version = payload.get("manifest_version")
        if version != MANIFEST_VERSION:
            raise EvaluationError(
                f"Unsupported suite manifest version {version!r}; expected {MANIFEST_VERSION}.",
                hint="Regenerate it with fsociety.evaluation.suites.rebuild_manifest().",
            )
        return payload

    def _verify_against_manifest(self, manifest_path: Path) -> None:
        assert self._manifest is not None  # narrow for type checkers
        entries: dict[str, Any] = self._manifest["suites"]
        problems: list[str] = []
        for suite_id, loaded in self._suites.items():
            entry = entries.get(suite_id)
            if not isinstance(entry, dict):
                problems.append(f"{suite_id}: missing from manifest")
                continue
            expected_hash = entry.get("suite_sha256")
            if expected_hash != loaded.info.sha256:
                problems.append(
                    f"{suite_id}: suite hash {loaded.info.sha256} does not match "
                    f"manifest {expected_hash}"
                )
            expected_file_hash = entry.get("file_sha256")
            if expected_file_hash and expected_file_hash != loaded.file_sha256:
                problems.append(
                    f"{suite_id}: file hash {loaded.file_sha256} does not match "
                    f"manifest {expected_file_hash}"
                )
        for suite_id in sorted(set(entries) - set(self._suites)):
            problems.append(f"{suite_id}: listed in manifest but no suite file found")
        if problems:
            raise ValidationError(
                f"Benchmark suite integrity check failed for {manifest_path.name}.",
                details={"problems": problems, "manifest": str(manifest_path)},
                hint=(
                    "A suite file was modified or removed. Restore it, or regenerate the "
                    "manifest with fsociety.evaluation.suites.rebuild_manifest() and "
                    "review the diff."
                ),
            )
        self.manifest_verified = True

    # ---- public API --------------------------------------------------------
    def list_suites(self) -> list[BenchmarkSuiteInfo]:
        return [self._suites[suite_id].info for suite_id in sorted(self._suites)]

    def loaded_suites(self) -> list[LoadedSuite]:
        """All loaded suites in stable ``suite_id`` order."""
        return [self._suites[suite_id] for suite_id in sorted(self._suites)]

    def get(self, suite_id: str) -> BenchmarkSuiteInfo:
        loaded = self._loaded(suite_id)
        return loaded.info

    def get_document(self, suite_id: str) -> SuiteDocument:
        return self._loaded(suite_id).document

    def load_items(self, suite_id: str) -> list[BenchmarkItem]:
        return list(self._loaded(suite_id).items)

    def suite_hash(self, suite_id: str) -> str:
        return self._loaded(suite_id).info.sha256

    def file_sha256(self, suite_id: str) -> str:
        return self._loaded(suite_id).file_sha256

    def file_path(self, suite_id: str) -> Path:
        return self._loaded(suite_id).file_path

    def categories(self, suite_id: str) -> list[str]:
        return list(self._loaded(suite_id).info.categories)

    def suite_ids(self) -> list[str]:
        return sorted(self._suites)

    def __len__(self) -> int:
        return len(self._suites)

    def __contains__(self, suite_id: object) -> bool:
        return isinstance(suite_id, str) and suite_id in self._suites

    def __iter__(self) -> Iterable[BenchmarkSuiteInfo]:
        return iter(self.list_suites())

    def _loaded(self, suite_id: str) -> LoadedSuite:
        if not isinstance(suite_id, str) or suite_id not in self._suites:
            raise NotFoundError(
                f"Benchmark suite not found: {suite_id!r}",
                details={"available": self.suite_ids()},
                hint="List suites with SuiteRegistry().list_suites().",
            )
        return self._suites[suite_id]


def rebuild_manifest(directory: str | Path | None = None) -> Path:
    """Regenerate ``_manifest.json`` from the suite files in ``directory``.

    Intended for suite authors. Regenerating the manifest silently is an
    explicit trust decision: review the diff before committing it.
    """
    registry = SuiteRegistry(directory, verify_manifest=False)
    manifest_path = registry.directory / MANIFEST_FILENAME
    entries = {
        loaded.info.suite_id: {
            "version": loaded.info.version,
            "item_count": loaded.info.item_count,
            "suite_sha256": loaded.info.sha256,
            "file_sha256": loaded.file_sha256,
        }
        for loaded in registry.loaded_suites()
    }
    payload = {
        "manifest_version": MANIFEST_VERSION,
        "generator": "fsociety.evaluation.suites.rebuild_manifest",
        "suites": entries,
    }
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return _atomic_write_text(manifest_path, text)


__all__ = [
    "DEFAULT_SUITE_DIR",
    "MANIFEST_FILENAME",
    "MANIFEST_VERSION",
    "LoadedSuite",
    "SuiteDocument",
    "SuiteItemSpec",
    "SuiteRegistry",
    "canonical_suite_json",
    "rebuild_manifest",
    "suite_sha256",
]
