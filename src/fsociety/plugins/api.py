# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Versioned plugin contract for FSOCIETY AI.

Design notes
------------
* Validation is **static**: :func:`validate_plugin_class` only imports the
  module that provides the class (via :func:`importlib.util.find_spec` for
  dependencies) and inspects class attributes. It never instantiates the plugin
  and never calls plugin code.
* Plugins are untrusted. Nothing in this module executes plugin logic; runtime
  execution belongs to :mod:`fsociety.plugins.isolation` (subprocess sandbox).
* The Heretic engine keeps its own modifier plugin system. A "modifier" adapter
  for FSOCIETY is documentation-only: upstream ``heretic.modifier.Modifier``
  implementations are used unchanged and are **not** loaded through this API.
"""

from __future__ import annotations

import importlib.util
import inspect
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar, cast

from fsociety import PLUGIN_API_VERSION

from ..core.models import PluginInfo, PluginKind

#: Runtime permissions a plugin may request. Anything else is invalid.
ALLOWED_PLUGIN_PERMISSIONS: frozenset[str] = frozenset(
    {"network", "filesystem_read", "filesystem_write", "subprocess", "gpu"}
)

#: Names that would shadow platform modules or imply elevated trust.
FORBIDDEN_PLUGIN_NAMES: frozenset[str] = frozenset(
    {
        "__main__",
        "builtins",
        "fsociety",
        "heretic",
        "importlib",
        "os",
        "subprocess",
        "sys",
    }
)

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\- ]{0,63}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?$")
_REQUIREMENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]*")


def _infer_kind(cls: Any) -> str:
    if not isinstance(cls, type):
        return ""
    for kind, base in KIND_TO_ABC.items():
        if issubclass(cls, base):
            return kind
    return ""


def _dependency_import_names(requirement: str) -> list[str]:
    """Best-effort import names for a PEP 508 requirement string.

    Distribution names are not always import names (``pydantic-settings`` →
    ``pydantic_settings``), so both spellings are probed. This is a heuristic
    and is documented as such: a dependency that is installed under an
    unrelated import name is reported as missing.
    """
    raw = requirement.strip()
    if not raw:
        return []
    if raw.startswith("["):  # extras-only pseudo requirement
        return []
    match = _REQUIREMENT_NAME_RE.match(raw)
    if not match:
        return []
    base = match.group(0)
    names = [base]
    underscored = base.replace("-", "_")
    if underscored not in names:
        names.append(underscored)
    return names


def _dependency_importable(requirement: str) -> bool:
    for name in _dependency_import_names(requirement):
        try:
            if importlib.util.find_spec(name) is not None:
                return True
        except (ImportError, ModuleNotFoundError, ValueError):
            continue
    return False


class PluginBase(ABC):
    """Common metadata contract for every FSOCIETY plugin kind.

    Subclasses declare the class attributes below. ``kind`` is expected to come
    from the concrete ABC (``Scorer.kind = "scorer"``); a plugin may override it
    only with a value that matches the ABC it derives from.
    """

    name: ClassVar[str] = ""
    version: ClassVar[str] = "0.0.0"
    api_version: ClassVar[int] = PLUGIN_API_VERSION
    kind: ClassVar[str] = ""
    description: ClassVar[str] = ""
    author: ClassVar[str | None] = None
    license: ClassVar[str | None] = None
    dependencies: ClassVar[list[str]] = []
    permissions: ClassVar[list[str]] = []

    @classmethod
    def manifest(cls) -> PluginInfo:
        """Return the declared metadata as a validated-by-shape descriptor."""
        kind = cls.kind if cls.kind in KIND_TO_ABC else (_infer_kind(cls) or "scorer")
        return PluginInfo(
            name=cls.name,
            version=cls.version,
            api_version=int(cls.api_version),
            kind=cast(PluginKind, kind),
            class_name=cls.__name__,
            module=cls.__module__ or "",
            description=cls.description or "",
            author=cls.author,
            license=cls.license,
            dependencies=list(cls.dependencies),
            permissions=list(cls.permissions),
        )


class Scorer(PluginBase):
    """Scores a payload deterministically; higher is better unless documented."""

    kind = "scorer"

    @abstractmethod
    def score(self, item: dict[str, Any]) -> float:
        """Return a score in ``[0.0, 1.0]`` for ``item``."""


class Evaluator(PluginBase):
    kind = "evaluator"

    @abstractmethod
    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Evaluate ``payload`` and return a structured result."""


class Exporter(PluginBase):
    kind = "exporter"

    @abstractmethod
    def export(self, payload: dict[str, Any], destination: Path) -> dict[str, Any]:
        """Write an export to ``destination`` and return a structured result."""


class HardwareProvider(PluginBase):
    kind = "hardware_provider"

    @abstractmethod
    def detect(self) -> dict[str, Any]:
        """Return detected hardware facts with provenance."""


class Integration(PluginBase):
    kind = "integration"

    @abstractmethod
    def probe(self) -> dict[str, Any]:
        """Probe an external integration without side effects."""


#: Mapping from kind to its required ABC. Kept public for registry tooling.
KIND_TO_ABC: dict[str, type[PluginBase]] = {
    "scorer": Scorer,
    "evaluator": Evaluator,
    "exporter": Exporter,
    "hardware_provider": HardwareProvider,
    "integration": Integration,
}

_ALLOWED_METHODS: dict[str, str] = {
    "scorer": "score",
    "evaluator": "evaluate",
    "exporter": "export",
    "hardware_provider": "detect",
    "integration": "probe",
}


def allowed_method(kind: str) -> str | None:
    """Return the single callable method exposed for a plugin kind."""
    return _ALLOWED_METHODS.get(kind)


def _safe_kind(value: str) -> PluginKind:
    if value in KIND_TO_ABC:
        return cast(PluginKind, value)
    return "scorer"


def validate_plugin_class(
    cls: Any,
    expected_kind: str | None = None,
    *,
    path: str | None = None,
    builtin: bool = False,
) -> PluginInfo:
    """Validate a plugin class without executing it.

    Returns a :class:`PluginInfo` whose ``valid``/``errors`` fields describe the
    outcome. Validation never raises for malformed plugin metadata; malformed
    *caller arguments* (an unknown ``expected_kind``) raise ``ValueError``.
    """
    if expected_kind is not None and expected_kind not in KIND_TO_ABC:
        raise ValueError(
            f"Unknown plugin kind {expected_kind!r}; expected one of {sorted(KIND_TO_ABC)}."
        )

    errors: list[str] = []
    warnings: list[str] = []

    class_name = getattr(cls, "__name__", type(cls).__name__)
    module = getattr(cls, "__module__", "") or ""
    declared_kind = getattr(cls, "kind", "")
    if not isinstance(declared_kind, str):
        errors.append("Plugin attribute 'kind' must be a string.")
        declared_kind = ""

    is_plugin = isinstance(cls, type) and issubclass(cls, PluginBase)
    if not is_plugin:
        errors.append(
            f"{class_name!r} does not subclass fsociety.plugins.api.PluginBase."
        )

    resolved_kind = declared_kind if declared_kind in KIND_TO_ABC else _infer_kind(cls)
    if not resolved_kind:
        errors.append(
            "Plugin does not declare a supported kind; expected one of "
            f"{sorted(KIND_TO_ABC)}."
        )

    # --- base-class / abstractness -----------------------------------------
    if is_plugin:
        abstract_methods = set(getattr(cls, "__abstractmethods__", frozenset()))
        if inspect.isabstract(cls) or abstract_methods:
            rendered = ", ".join(sorted(abstract_methods)) or "<unknown>"
            errors.append(
                f"Plugin class is abstract; implement abstract method(s): {rendered}."
            )
        if expected_kind is not None:
            expected_base = KIND_TO_ABC[expected_kind]
            if not issubclass(cls, expected_base):
                errors.append(
                    f"Plugin class must subclass {expected_base.__name__} for kind "
                    f"{expected_kind!r}."
                )
            if declared_kind and declared_kind != expected_kind:
                errors.append(
                    f"Plugin declares kind {declared_kind!r} but discovery expected "
                    f"{expected_kind!r}."
                )
        elif declared_kind in KIND_TO_ABC:
            expected_base = KIND_TO_ABC[declared_kind]
            if not issubclass(cls, expected_base):
                errors.append(
                    f"Plugin declares kind {declared_kind!r} but must subclass "
                    f"{expected_base.__name__}."
                )

    # --- identity -----------------------------------------------------------
    name = getattr(cls, "name", "")
    if not isinstance(name, str) or not name.strip():
        errors.append("Plugin attribute 'name' must be a non-empty string.")
    else:
        if not _NAME_RE.match(name):
            errors.append(
                "Plugin name may only contain letters, digits, dots, dashes, "
                "underscores and spaces (max 64 chars)."
            )
        if name.lower() in FORBIDDEN_PLUGIN_NAMES:
            errors.append(f"Plugin name {name!r} is reserved.")

    version = getattr(cls, "version", "")
    if not isinstance(version, str) or not version.strip():
        errors.append("Plugin attribute 'version' must be a non-empty string.")
    elif not _VERSION_RE.match(version):
        errors.append(
            f"Plugin version {version!r} is not semver-like (expected MAJOR.MINOR.PATCH)."
        )

    api_version = getattr(cls, "api_version", None)
    if not isinstance(api_version, int) or isinstance(api_version, bool):
        errors.append("Plugin attribute 'api_version' must be an integer.")
    elif api_version != PLUGIN_API_VERSION:
        errors.append(
            f"Plugin targets API version {api_version}, but this platform requires "
            f"{PLUGIN_API_VERSION}."
        )

    # --- metadata -----------------------------------------------------------
    description = getattr(cls, "description", "")
    if not isinstance(description, str):
        errors.append("Plugin attribute 'description' must be a string.")
        description = ""
    for field in ("author", "license"):
        value = getattr(cls, field, None)
        if value is not None and not isinstance(value, str):
            errors.append(f"Plugin attribute {field!r} must be a string or None.")

    dependencies = getattr(cls, "dependencies", [])
    if not isinstance(dependencies, (list, tuple)) or not all(
        isinstance(dep, str) for dep in dependencies
    ):
        errors.append("Plugin attribute 'dependencies' must be a list of strings.")
        dependencies = []
    else:
        dependencies = list(dependencies)
        for dep in dependencies:
            if not dep.strip():
                errors.append("Plugin dependency entries must not be empty.")
            elif not _dependency_importable(dep):
                errors.append(f"Plugin dependency {dep!r} is not importable.")

    permissions = getattr(cls, "permissions", [])
    if not isinstance(permissions, (list, tuple)) or not all(
        isinstance(perm, str) for perm in permissions
    ):
        errors.append("Plugin attribute 'permissions' must be a list of strings.")
        permissions = []
    else:
        permissions = list(permissions)
        seen: set[str] = set()
        for perm in permissions:
            if perm in seen:
                warnings.append(f"Duplicate permission {perm!r}.")
            seen.add(perm)
            if perm not in ALLOWED_PLUGIN_PERMISSIONS:
                errors.append(
                    f"Unknown plugin permission {perm!r}; allowed permissions are "
                    f"{sorted(ALLOWED_PLUGIN_PERMISSIONS)}."
                )

    resolved_kind_safe = _safe_kind(resolved_kind or "scorer")
    return PluginInfo(
        name=name if isinstance(name, str) else "",
        version=version if isinstance(version, str) else "",
        api_version=api_version if isinstance(api_version, int) else 0,
        kind=resolved_kind_safe,
        class_name=str(class_name),
        module=str(module),
        path=path,
        description=description,
        author=getattr(cls, "author", None)
        if isinstance(getattr(cls, "author", None), str)
        else None,
        license=getattr(cls, "license", None)
        if isinstance(getattr(cls, "license", None), str)
        else None,
        dependencies=dependencies,
        permissions=permissions,
        builtin=builtin,
        valid=not errors,
        errors=errors,
        warnings=warnings,
    )


# ``Modifier`` is intentionally *not* defined: Heretic modifiers are upstream
# artifacts and must keep using ``heretic.modifier.Modifier`` unchanged.
