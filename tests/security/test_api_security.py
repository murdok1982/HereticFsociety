# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Adversarial API tests: auth bypass, headers, limits, CSRF and traversal.

These tests exercise the real FastAPI application through ``TestClient``. They
encode both the controls that must hold and the residual weaknesses recorded in
``.factory/SECURITY_AUDIT.md`` (marked with the finding id they document).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from fsociety.config import PlatformSettings  # noqa: E402
from fsociety.context import PlatformContext  # noqa: E402
from fsociety.interfaces.api.app import create_app  # noqa: E402

API_KEY = "test-api-key-0123456789"
AUTH = {"X-API-Key": API_KEY}
PUBLIC_PATHS = {"/api/v1/health", "/api/v1/version"}


@pytest.fixture()
def client(workspace: Path):
    settings = PlatformSettings(
        workspace=workspace,
        api_key=API_KEY,
        api_rate_limit_per_minute=10_000,
        serve_frontend=False,
    )
    ctx = PlatformContext(settings)
    app = create_app(context=ctx)
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        yield test_client
    ctx.close()


# ---------------------------------------------------------------------------
# Authentication and authorization
# ---------------------------------------------------------------------------


def test_only_health_and_version_are_public(client: TestClient) -> None:
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/version").status_code == 200
    for path in (
        "/api/v1/models",
        "/api/v1/settings",
        "/api/v1/plugins",
        "/api/v1/security/findings",
        "/api/v1/exports",
        "/api/v1/experiments",
        "/api/v1/events",
    ):
        response = client.get(path)
        assert response.status_code == 401, (path, response.status_code)
        assert response.json()["error"]["code"] == "authentication_required"


def test_every_openapi_endpoint_requires_the_key(client: TestClient) -> None:
    schema = client.get("/api/openapi.json").json()
    checked = 0
    for path, operations in schema["paths"].items():
        if not path.startswith("/api/v1") or path in PUBLIC_PATHS:
            continue
        concrete = re.sub(r"\{[^}]+\}", "x", path)
        for method in operations:
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            request = getattr(client, method)
            kwargs: dict[str, Any] = {}
            if method in {"post", "put", "patch"}:
                kwargs["json"] = {}
            response = request(concrete, **kwargs)
            assert response.status_code in {401, 403}, (
                method,
                concrete,
                response.status_code,
                response.text[:200],
            )
            checked += 1
    assert checked >= 15, f"only {checked} protected operations discovered"


@pytest.mark.parametrize(
    "spoof",
    [
        {"X-Forwarded-For": "127.0.0.1"},
        {"X-Real-IP": "127.0.0.1"},
        {"Forwarded": "for=127.0.0.1"},
        {"X-Forwarded-Host": "localhost"},
    ],
)
def test_forwarding_headers_do_not_grant_loopback(
    client: TestClient, spoof: dict
) -> None:
    response = client.get("/api/v1/models", headers=spoof)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_remote_without_configured_key_fails_closed(workspace: Path) -> None:
    settings = PlatformSettings(
        workspace=workspace,
        api_key=None,
        api_host="127.0.0.1",
        serve_frontend=False,
    )
    ctx = PlatformContext(settings)
    app = create_app(context=ctx)
    with TestClient(
        app, base_url="http://127.0.0.1", client=("203.0.113.9", 5555)
    ) as c:
        response = c.get("/api/v1/models")
    ctx.close()
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "remote_access_disabled"
    assert "FSOCIETY_API_KEY" in response.json()["error"]["hint"]


def test_loopback_is_exempt_from_key_by_default(workspace: Path) -> None:
    settings = PlatformSettings(workspace=workspace, serve_frontend=False)
    ctx = PlatformContext(settings)
    app = create_app(context=ctx)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1234)) as c:
        assert c.get("/api/v1/models").status_code == 200
        assert c.get("/api/v1/settings").status_code == 200
    ctx.close()


def test_loopback_can_require_a_key(workspace: Path) -> None:
    settings = PlatformSettings(
        workspace=workspace,
        api_key=API_KEY,
        api_require_key_for_loopback=True,
        serve_frontend=False,
    )
    ctx = PlatformContext(settings)
    app = create_app(context=ctx)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1234)) as c:
        assert c.get("/api/v1/models").status_code == 401
        assert c.get("/api/v1/models", headers=AUTH).status_code == 200
    ctx.close()


def test_bearer_and_token_schemes_are_accepted(client: TestClient) -> None:
    for header in (
        {"Authorization": f"Bearer {API_KEY}"},
        {"Authorization": f"bearer {API_KEY}"},
        {"Authorization": f"token {API_KEY}"},
    ):
        assert client.get("/api/v1/models", headers=header).status_code == 200


def test_empty_or_partial_credentials_are_rejected(client: TestClient) -> None:
    for header in (
        {"Authorization": "Bearer "},
        {"Authorization": "Bearer wrong-key"},
        {"X-API-Key": ""},
        {"X-API-Key": API_KEY[:-1]},
        {"X-API-Key": API_KEY + "x"},
    ):
        response = client.get("/api/v1/models", headers=header)
        assert response.status_code in {401, 403}, header


# ---------------------------------------------------------------------------
# HTTP hardening
# ---------------------------------------------------------------------------


def _assert_hardening_headers(response) -> None:
    headers = response.headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert headers["Cross-Origin-Resource-Policy"] == "same-origin"
    assert "camera=()" in headers["Permissions-Policy"]
    csp = headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "unsafe-eval" not in csp


def test_security_headers_on_success_and_auth_failure(client: TestClient) -> None:
    _assert_hardening_headers(client.get("/api/v1/health"))
    error = client.get("/api/v1/models")
    assert error.status_code == 401
    _assert_hardening_headers(error)


def test_host_header_is_validated(client: TestClient) -> None:
    response = client.get("/api/v1/health", headers={"Host": "evil.example"})
    assert response.status_code == 400
    assert "Invalid host header" in response.text
    assert "Traceback" not in response.text


def test_declared_oversized_body_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/models/scan",
        headers={**AUTH, "Content-Length": "99999999"},
        content=b"{}",
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    _assert_hardening_headers(response)


def test_chunked_body_without_length_is_rejected(client: TestClient) -> None:
    """VULN-201 (closed): chunked bodies without Content-Length are refused."""
    big = b'{"payload":{"x":"' + b"A" * (2 * 1024 * 1024) + b'"}}'

    def generate():
        for offset in range(0, len(big), 65536):
            yield big[offset : offset + 65536]

    response = client.post("/api/v1/plugins/nope/run", headers=AUTH, content=generate())
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


def test_compressed_request_bodies_are_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/plugins/nope/run",
        headers={**AUTH, "Content-Encoding": "gzip", "Content-Length": "10"},
        content=b"x" * 10,
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_encoding"


def test_rate_limit_returns_429_with_retry_after(workspace: Path) -> None:
    settings = PlatformSettings(
        workspace=workspace,
        api_key=API_KEY,
        api_rate_limit_per_minute=3,
        serve_frontend=False,
    )
    ctx = PlatformContext(settings)
    app = create_app(context=ctx)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        for _ in range(3):
            assert c.get("/api/v1/health").status_code == 200
        limited = c.get("/api/v1/health")
    ctx.close()
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert int(limited.headers["Retry-After"]) >= 1
    _assert_hardening_headers(limited)


def test_api_docs_are_publicly_readable(workspace: Path) -> None:
    """VULN-009 (LOW): the OpenAPI schema is not behind the auth dependency."""
    settings = PlatformSettings(
        workspace=workspace, api_key=API_KEY, serve_frontend=False
    )
    ctx = PlatformContext(settings)
    app = create_app(context=ctx)
    with TestClient(
        app, base_url="http://127.0.0.1", client=("203.0.113.50", 4444)
    ) as c:
        schema = c.get("/api/openapi.json")
        docs = c.get("/api/docs")
    ctx.close()
    assert schema.status_code == 200
    assert docs.status_code == 200
    assert "/api/v1/models" in schema.text


def test_errors_are_structured_and_never_contain_tracebacks(client: TestClient) -> None:
    unknown = client.get("/api/v1/definitely-not-a-route", headers=AUTH)
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "not_found"

    invalid = client.post("/api/v1/models/scan", headers=AUTH, json={"roots": "nope"})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_error"

    for response in (unknown, invalid):
        text = response.text
        for leak in ("Traceback", 'File "', ".py:", "/site-packages/"):
            assert leak not in text, (leak, text[:300])


# ---------------------------------------------------------------------------
# Path traversal and containment
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("post", "/api/v1/models/scan", {"roots": ["../../etc"]}),
        ("post", "/api/v1/models/scan", {"roots": ["/etc"]}),
        (
            "post",
            "/api/v1/exports/bundle",
            {"files": ["../../etc/passwd"], "destination": "exports/x"},
        ),
        (
            "post",
            "/api/v1/exports/bundle",
            {"files": ["../outside"], "destination": "../evil"},
        ),
    ],
)
def test_write_and_scan_endpoints_reject_path_escapes(
    client: TestClient, method: str, path: str, payload: dict
) -> None:
    response = getattr(client, method)(path, headers=AUTH, json=payload)
    assert response.status_code in {400, 404}, response.text
    body = response.json()["error"]
    assert body["code"] in {"unsafe_path", "not_found"}
    assert "root:" not in response.text


def test_inspect_rejects_relative_traversal(client: TestClient) -> None:
    response = client.get(
        "/api/v1/models/inspect", headers=AUTH, params={"reference": "../../etc/passwd"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsafe_path"


def test_report_id_traversal_returns_not_found(client: TestClient) -> None:
    payload = quote("../../../../etc/passwd", safe="")
    response = client.get(f"/api/v1/evaluation/reports/{payload}", headers=AUTH)
    assert response.status_code in {404, 405}
    assert "root:" not in response.text


def test_export_destination_must_be_inside_exports(
    client: TestClient, workspace: Path
) -> None:
    source = workspace / "artifact.txt"
    source.write_text("data", encoding="utf-8")
    response = client.post(
        "/api/v1/exports/bundle",
        headers=AUTH,
        json={"files": ["artifact.txt"], "destination": "models/not-exports"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_absolute_model_references_outside_workspace_are_rejected(
    client: TestClient, tmp_path: Path
) -> None:
    sensitive = tmp_path / "sensitive-host-dir"
    sensitive.mkdir()
    secret_marker = "HostSecretArchitecture"
    (sensitive / "config.json").write_text(
        json.dumps({"architectures": [secret_marker]}), encoding="utf-8"
    )
    (sensitive / "README.md").write_text("not needed", encoding="utf-8")

    for reference in (str(sensitive), "/etc", "/etc/passwd"):
        response = client.get(
            "/api/v1/models/inspect", headers=AUTH, params={"reference": reference}
        )
        assert response.status_code == 400, (reference, response.text)
        assert response.json()["error"]["code"] == "unsafe_path"
        assert secret_marker not in response.text


def test_absolute_model_reference_inside_workspace_is_allowed(
    client: TestClient, workspace: Path
) -> None:
    model_dir = workspace / "models" / "tiny"
    model_dir.mkdir(parents=True)
    (model_dir / "config.json").write_text(
        json.dumps({"architectures": ["LocalArch"]}), encoding="utf-8"
    )
    response = client.get(
        "/api/v1/models/inspect", headers=AUTH, params={"reference": str(model_dir)}
    )
    assert response.status_code == 200, response.text
    assert response.json()["architecture"] == "LocalArch"


def test_absolute_model_paths_can_be_disabled(workspace: Path) -> None:
    model_dir = workspace / "models" / "tiny"
    model_dir.mkdir(parents=True)
    (model_dir / "config.json").write_text("{}", encoding="utf-8")

    settings = PlatformSettings(
        workspace=workspace,
        api_key=API_KEY,
        api_rate_limit_per_minute=10_000,
        serve_frontend=False,
        allow_absolute_model_paths=False,
    )
    ctx = PlatformContext(settings)
    app = create_app(context=ctx)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        response = c.get(
            "/api/v1/models/inspect", headers=AUTH, params={"reference": str(model_dir)}
        )
    ctx.close()
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsafe_path"


def test_job_model_references_enforce_workspace_confinement(
    client: TestClient, tmp_path: Path
) -> None:
    outside = tmp_path / "outside-model"
    outside.mkdir()
    (outside / "config.json").write_text(
        json.dumps({"architectures": ["OutsideArch"]}), encoding="utf-8"
    )

    optimization = client.post(
        "/api/v1/optimization/jobs",
        headers=AUTH,
        json={
            "model_id": str(outside),
            "space": {
                "dimensions": [
                    {"name": "batch_size", "kind": "int", "low": 1, "high": 2}
                ]
            },
            "n_trials": 1,
        },
    )
    assert optimization.status_code == 400, optimization.text
    assert optimization.json()["error"]["code"] == "unsafe_path"

    evaluation = client.post(
        "/api/v1/evaluation/jobs",
        headers=AUTH,
        json={
            "suite_id": "knowledge_core",
            "backend": {"kind": "transformers", "model_id": str(outside)},
            "limit": 1,
        },
    )
    assert evaluation.status_code == 400, evaluation.text
    assert evaluation.json()["error"]["code"] == "unsafe_path"

    llama = client.post(
        "/api/v1/evaluation/jobs",
        headers=AUTH,
        json={
            "suite_id": "knowledge_core",
            "backend": {"kind": "llama_cpp", "path": "/etc/passwd"},
            "limit": 1,
        },
    )
    assert llama.status_code == 400, llama.text
    assert llama.json()["error"]["code"] == "unsafe_path"


# ---------------------------------------------------------------------------
# Secrets and configuration
# ---------------------------------------------------------------------------


def test_settings_never_return_the_api_key(client: TestClient) -> None:
    for path in ("/api/v1/settings", "/api/v1/security/policy", "/api/v1/system"):
        response = client.get(path, headers=AUTH)
        assert response.status_code == 200
        assert API_KEY not in response.text, path


def test_error_envelopes_redact_token_shaped_references(client: TestClient) -> None:
    token = "hf_" + "z" * 34
    response = client.get(
        "/api/v1/models/inspect", headers=AUTH, params={"reference": token}
    )
    assert response.status_code == 404
    assert token not in response.text
    assert "[REDACTED]" in response.text


def test_security_findings_endpoint_shape(client: TestClient) -> None:
    response = client.get("/api/v1/security/findings", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"items", "total", "counts"}
    assert set(body["counts"]) == {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}
    for item in body["items"]:
        assert item["severity"] in body["counts"]
        assert "title" in item and "status" in item


def test_no_mutating_http_verbs_are_exposed(client: TestClient) -> None:
    schema = client.get("/api/openapi.json").json()
    allowed = {"get", "post", "put", "patch", "delete", "options", "head"}
    verbs = {
        method.lower()
        for operations in schema["paths"].values()
        for method in operations
        if method.lower() in allowed
    }
    assert verbs <= {"get", "post"}, verbs


# ---------------------------------------------------------------------------
# Plugins through the API
# ---------------------------------------------------------------------------


def test_plugin_api_rejects_network_and_isolation_overrides(client: TestClient) -> None:
    for extra in ({"allow_network": True}, {"isolation": False}):
        response = client.post(
            "/api/v1/plugins/code_quality/run",
            headers=AUTH,
            json={"method": "score", "payload": {}, **extra},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


def test_plugin_api_cannot_switch_to_in_process_execution(client: TestClient) -> None:
    response = client.post(
        "/api/v1/plugins/code_quality/run",
        headers=AUTH,
        json={
            "method": "score",
            "payload": {"code": "def f():\n    return 1\n"},
            "allow_in_process": True,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"]["isolation"]["mode"] == "subprocess"


def test_plugin_path_parameters_cannot_select_arbitrary_files(
    client: TestClient,
) -> None:
    for name in ("../../etc/passwd", "..%2f..%2fetc%2fpasswd", "code_quality\x00"):
        response = client.post(
            f"/api/v1/plugins/{quote(name, safe='')}/run",
            headers=AUTH,
            json={"method": "score", "payload": {}},
        )
        assert response.status_code in {404, 405, 422}, (name, response.status_code)


# ---------------------------------------------------------------------------
# CSRF on loopback
# ---------------------------------------------------------------------------


def test_foreign_origin_state_change_is_rejected(workspace: Path) -> None:
    """VULN-201 (closed): cross-site POSTs to loopback endpoints are refused."""
    settings = PlatformSettings(workspace=workspace, serve_frontend=False)
    ctx = PlatformContext(settings)
    app = create_app(context=ctx)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1234)) as c:
        response = c.post(
            "/api/v1/plugins/reload",
            headers={"Origin": "https://evil.example"},
        )
        same_origin = c.post(
            "/api/v1/plugins/reload",
            headers={"Origin": "http://127.0.0.1"},
        )
    ctx.close()
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_not_allowed"
    assert same_origin.status_code == 200
    assert response.headers.get("Access-Control-Allow-Origin") is None
