# SPDX-License-Identifier: AGPL-3.0-or-later
"""SBOM generation and dependency-audit availability tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fsociety.security import sbom


def test_cyclonedx_contains_installed_pydantic(tmp_path: Path) -> None:
    destination = tmp_path / "sbom.json"
    document = sbom.generate_cyclonedx(destination)

    assert destination.is_file()
    on_disk = json.loads(destination.read_text(encoding="utf-8"))
    assert on_disk["bomFormat"] == "CycloneDX"
    assert on_disk["specVersion"] == "1.5"
    assert on_disk["serialNumber"].startswith("urn:uuid:")
    assert on_disk["metadata"]["tools"][0]["name"] == "fsociety-sbom"

    names = {component["name"].lower() for component in document["components"]}
    assert "pydantic" in names
    for component in document["components"]:
        assert component["type"] == "library"
        assert component["purl"].startswith("pkg:pypi/")
        assert component["bom-ref"] == component["purl"]


def test_cyclonedx_component_ordering_is_deterministic(tmp_path: Path) -> None:
    first = sbom.generate_cyclonedx(tmp_path / "one.json")
    second = sbom.generate_cyclonedx(tmp_path / "two.json")
    keys = [
        (component["name"].lower(), component["version"])
        for component in first["components"]
    ]
    assert keys == sorted(keys)
    assert first["components"] == second["components"]


def test_dependency_audit_unavailable_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sbom.importlib.util, "find_spec", lambda name: None)
    result = sbom.dependency_audit()
    assert result["status"] == "unavailable"
    assert result["hint"]


def test_dependency_audit_normalizes_findings() -> None:
    payload = {
        "dependencies": [
            {
                "name": "demo-package",
                "version": "1.0.0",
                "vulns": [
                    {
                        "id": "CVE-2099-0001",
                        "aliases": ["GHSA-xxxx"],
                        "fix_versions": ["1.0.1"],
                        "description": "example finding",
                    }
                ],
            }
        ]
    }
    normalized = sbom._normalize_audit(payload)
    assert normalized["status"] == "vulnerabilities"
    assert normalized["finding_count"] == 1
    assert normalized["findings"][0]["id"] == "CVE-2099-0001"
    assert normalized["findings"][0]["fix_versions"] == ["1.0.1"]
