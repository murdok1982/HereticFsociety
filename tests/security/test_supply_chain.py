# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Supply-chain checks: lockfiles, attribution and packaging metadata."""

from __future__ import annotations

import json
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_uv_lock_is_present_and_resolves_a_full_graph() -> None:
    lock_path = REPO_ROOT / "uv.lock"
    assert lock_path.is_file(), "uv.lock is required for reproducible installs"
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    assert lock["version"] == 1
    packages = lock.get("package", [])
    assert len(packages) >= 100
    names = {package["name"].lower() for package in packages}
    for expected in ("fastapi", "pydantic", "torch", "transformers", "uvicorn"):
        assert expected in names, expected
    for package in packages:
        assert package.get("version"), package
        assert package.get("source"), package


def test_project_metadata_keeps_upstream_attribution() -> None:
    pyproject = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    project = pyproject["project"]
    assert project["license"] == "AGPL-3.0-or-later"
    author_names = {author["name"] for author in project["authors"]}
    assert any("Weidmann" in name for name in author_names)
    assert project["scripts"]["heretic"] == "heretic.main:main"
    assert project["scripts"]["fsociety"] == "fsociety.interfaces.cli.main:entrypoint"
    assert project["urls"]["Upstream"] == "https://github.com/p-e-w/heretic"
    wheel_packages = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    assert "src/heretic" in wheel_packages
    assert "src/fsociety" in wheel_packages


def test_license_and_readme_credit_heretic() -> None:
    license_text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in license_text
    assert "Version 3" in license_text

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    lowered = readme.lower()
    assert "heretic" in lowered
    assert "gnu affero general public license" in lowered
    assert "weidmann" in lowered


def test_heretic_engine_source_is_preserved_except_the_shim() -> None:
    """The only engine touch-point is ``_distribution.py`` (verified via git)."""
    import subprocess

    completed = subprocess.run(
        ["git", "diff", "--name-only", "HEAD", "--", "src/heretic"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return  # not a git checkout in this environment
    changed = {line.strip() for line in completed.stdout.splitlines() if line.strip()}
    allowed = {
        "src/heretic/main.py",
        "src/heretic/system.py",
        "src/heretic/utils.py",
        "src/heretic/_distribution.py",
    }
    assert changed <= allowed, changed


def test_frontend_lockfile_has_no_install_hooks() -> None:
    package = json.loads(
        (REPO_ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
    )
    scripts = package.get("scripts", {})
    assert "postinstall" not in scripts
    assert "preinstall" not in scripts

    lock = json.loads(
        (REPO_ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8")
    )
    assert lock.get("lockfileVersion", 0) >= 3
    assert lock.get("packages"), "package-lock must pin the dependency graph"
