# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Adversarial path-jail tests: traversal, absolute paths and symlink escapes."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from fsociety.config import PlatformSettings
from fsociety.core.model_manager import ModelManager
from fsociety.errors import PathSecurityError, ValidationError
from fsociety.interfaces.api.deps import resolve_workspace_path
from fsociety.paths import Workspace, safe_join


@pytest.fixture()
def jail(tmp_path: Path) -> Path:
    root = tmp_path / "jail"
    (root / "nested").mkdir(parents=True)
    (root / "nested" / "file.txt").write_text("inside", encoding="utf-8")
    return root


def test_safe_join_accepts_relative_paths_inside(jail: Path) -> None:
    assert (
        safe_join(jail, "nested", "file.txt")
        == (jail / "nested" / "file.txt").resolve()
    )
    assert safe_join(jail, "nested/../nested/file.txt").is_file()


@pytest.mark.parametrize(
    "parts",
    [
        ("..",),
        ("../outside",),
        ("nested/../../outside",),
        ("/etc/passwd",),
        ("\\windows\\system32",),
    ],
)
def test_safe_join_rejects_escapes(jail: Path, parts: tuple[str, ...]) -> None:
    with pytest.raises(PathSecurityError):
        safe_join(jail, *parts)


def test_windows_drive_strings_are_not_absolute_on_posix(jail: Path) -> None:
    """On POSIX ``C:\\x`` is a relative file name, not a jail escape."""
    resolved = safe_join(jail, "C:\\Windows\\win.ini")
    assert resolved.parent == jail.resolve()


def test_safe_join_rejects_symlink_directory_escape(jail: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("do-not-read", encoding="utf-8")
    (jail / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PathSecurityError):
        safe_join(jail, "escape", "secret.txt")


def test_safe_join_rejects_symlink_file_escape(jail: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("do-not-read", encoding="utf-8")
    (jail / "leak.txt").symlink_to(outside)
    with pytest.raises(PathSecurityError):
        safe_join(jail, "leak.txt")


def test_safe_join_allows_symlink_that_stays_inside(jail: Path) -> None:
    (jail / "alias.txt").symlink_to(jail / "nested" / "file.txt")
    assert safe_join(jail, "alias.txt").is_file()


def test_safe_join_raises_value_error_for_null_bytes(jail: Path) -> None:
    """A null byte is rejected by the OS layer, not converted into a traversal."""
    with pytest.raises(ValueError):
        safe_join(jail, "nested\x00/file.txt")


def test_workspace_resolve_uses_the_jail(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    resolved = workspace.resolve("reports/run.json")
    assert str(resolved).startswith(str(workspace.root))
    with pytest.raises(PathSecurityError):
        workspace.resolve("../escape")


def _manager(tmp_path: Path, **overrides) -> ModelManager:
    settings = PlatformSettings(workspace=tmp_path / "workspace", **overrides)
    return ModelManager(settings)


def test_model_manager_rejects_traversal(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    with pytest.raises(PathSecurityError):
        manager.resolve("../etc")
    with pytest.raises(PathSecurityError):
        manager.inspect("../../etc/passwd")


def test_model_manager_absolute_paths_follow_policy(tmp_path: Path, jail: Path) -> None:
    strict = _manager(tmp_path, allow_absolute_model_paths=False)
    with pytest.raises(PathSecurityError):
        strict.resolve(str(jail))

    permissive = _manager(tmp_path, allow_absolute_model_paths=True)
    assert permissive.resolve(str(jail)) == jail.resolve()


def test_model_manager_rejects_control_characters(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    with pytest.raises(ValidationError):
        manager.inspect("model\x00name")


def test_manifest_entries_cannot_escape_the_model(tmp_path: Path, tiny_model) -> None:
    model_dir, _ = tiny_model
    manager = _manager(tmp_path, allow_absolute_model_paths=True)
    for relative in ("../escape.txt", "/etc/passwd", "shards/../../escape"):
        with pytest.raises(PathSecurityError):
            manager.verify(str(model_dir), manifest={relative: {"sha256": "a" * 64}})


def test_resolve_workspace_path_rejects_outside_absolute(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from fsociety.context import PlatformContext

    # ``resolve_workspace_path`` only needs ``ctx.settings`` and ``ctx.workspace``.
    settings = PlatformSettings(
        workspace=tmp_path / "workspace",
        models_dir=tmp_path / "workspace" / "models",
        allow_absolute_model_paths=True,
    )
    ctx = PlatformContext(settings)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ctx=ctx)))
    with pytest.raises(PathSecurityError):
        resolve_workspace_path(request, "/etc/passwd")
    inside = resolve_workspace_path(request, str(ctx.workspace.root / "data"))
    assert str(inside).startswith(str(ctx.workspace.root))
    ctx.close()


def test_cli_export_destination_cannot_escape_exports(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    with pytest.raises(PathSecurityError):
        safe_join(workspace.exports, "../outside")
    with pytest.raises(PathSecurityError):
        safe_join(workspace.exports, os.path.join("..", "..", "etc"))


def test_spa_static_handler_does_not_escape_dist(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("do-not-read", encoding="utf-8")

    candidate = (dist / "../secret.txt").resolve()
    assert dist not in candidate.parents
    assert not (dist in candidate.parents and candidate.is_file())
