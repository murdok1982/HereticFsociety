# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Software bill of materials and dependency auditing.

The CycloneDX document is built from the *installed* distributions reported by
:mod:`importlib.metadata`. No component is invented. Dependency auditing shells
out to the real ``pip-audit`` executable (no shell); when it is not installed the
result explicitly says ``unavailable``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import uuid
from importlib import metadata
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .. import __platform_name__, __version__
from ..core.models import utcnow_iso
from ..security.redaction import redact

SCHEMA_VERSION = 1
_SPEC_VERSION = "1.5"
AUDIT_TIMEOUT_SECONDS = 180


def _purl(name: str, version: str) -> str:
    normalized = name.lower().replace("_", "-")
    return f"pkg:pypi/{quote(normalized, safe='')}@{quote(version, safe='')}"


def _components() -> list[dict[str, Any]]:
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for distribution in metadata.distributions():
        try:
            raw_name = distribution.metadata["Name"]
            version = distribution.version
        except (KeyError, AttributeError):
            continue
        if not raw_name or not version:
            continue
        name = str(raw_name).strip()
        version = str(version).strip()
        key = (name.lower(), version)
        if key in seen:
            continue
        purl = _purl(name, version)
        seen[key] = {
            "type": "library",
            "name": name,
            "version": version,
            "purl": purl,
            "bom-ref": purl,
        }
    return [seen[key] for key in sorted(seen)]


def generate_cyclonedx(path: Path | str | None = None) -> dict[str, Any]:
    """Generate a deterministic CycloneDX 1.5 BOM for installed distributions."""
    components = _components()
    bom: dict[str, Any] = {
        "bomFormat": "CycloneDX",
        "specVersion": _SPEC_VERSION,
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": utcnow_iso(),
            "tools": [
                {
                    "vendor": __platform_name__,
                    "name": "fsociety-sbom",
                    "version": __version__,
                }
            ],
            "component": {
                "type": "application",
                "name": "fsociety-ai",
                "version": __version__,
                "purl": _purl("fsociety-ai", __version__),
            },
        },
        "components": components,
    }
    if path is not None:
        destination = Path(path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(bom, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        bom["written_to"] = str(destination)
    return bom


def _normalize_audit(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"status": "error", "error": "pip-audit output is not a JSON object"}
    findings: list[dict[str, Any]] = []
    dependencies = payload.get("dependencies")
    if isinstance(dependencies, list):
        for dependency in dependencies:
            if not isinstance(dependency, dict):
                continue
            name = dependency.get("name")
            version = dependency.get("version")
            vulnerabilities = dependency.get("vulns") or []
            if not isinstance(vulnerabilities, list):
                continue
            for vulnerability in vulnerabilities:
                if not isinstance(vulnerability, dict):
                    continue
                findings.append(
                    {
                        "name": name,
                        "package": name,
                        "version": version,
                        "id": vulnerability.get("id"),
                        "aliases": vulnerability.get("aliases") or [],
                        "fix_versions": vulnerability.get("fix_versions") or [],
                        "description": redact(
                            str(vulnerability.get("description") or "")
                        ),
                    }
                )
    deduped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for finding in findings:
        key = (
            str(finding.get("name") or ""),
            str(finding.get("version") or ""),
            str(finding.get("id") or ""),
        )
        deduped.setdefault(key, finding)
    findings = sorted(
        deduped.values(),
        key=lambda finding: (
            str(finding.get("name") or "").lower(),
            str(finding.get("id") or ""),
        ),
    )
    return {
        "status": "vulnerabilities" if findings else "ok",
        "finding_count": len(findings),
        "findings": findings,
    }


def dependency_audit(timeout_seconds: int = AUDIT_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Run pip-audit as a module in the current interpreter; never fabricate findings.

    The tool is resolved as ``sys.executable -m pip_audit`` instead of trusting a
    PATH lookup, so a hostile ``PATH`` cannot substitute an arbitrary binary.
    """
    if importlib.util.find_spec("pip_audit") is None:
        return {
            "status": "unavailable",
            "hint": "Install pip-audit (for example `pip install pip-audit`) and "
            "run the audit again. No vulnerability data is fabricated.",
        }
    if not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
        return {
            "status": "error",
            "error": "timeout_seconds must be a positive integer.",
        }
    safe_env = {
        key: value
        for key, value in os.environ.items()
        if key
        in {
            "PATH",
            "HOME",
            "LANG",
            "LC_ALL",
            "TMPDIR",
            "TEMP",
            "TMP",
            "SYSTEMROOT",
            "SYSTEMDRIVE",
        }
    }
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pip_audit", "-f", "json"],
            shell=False,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            env=safe_env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "status": "error",
            "error": redact(f"pip-audit could not run: {exc}"),
            "hint": "Check that pip-audit is functional in this environment.",
        }

    stdout = completed.stdout.decode("utf-8", "replace").strip()
    stderr = completed.stderr.decode("utf-8", "replace").strip()
    try:
        payload = json.loads(stdout) if stdout else None
    except json.JSONDecodeError:
        payload = None
    if payload is None:
        return {
            "status": "error",
            "exit_code": completed.returncode,
            "error": redact(stderr[-2000:] or "pip-audit returned no JSON output."),
            "hint": "Run `pip-audit -f json` manually to diagnose the tool.",
        }
    normalized = _normalize_audit(payload)
    normalized["exit_code"] = completed.returncode
    normalized["tool"] = "python -m pip_audit"
    if completed.returncode not in {0, 1} and normalized["status"] == "ok":
        normalized["status"] = "error"
        normalized["error"] = redact(
            stderr[-2000:] or f"pip-audit exited {completed.returncode}."
        )
    return normalized
