# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""API integration tests (FastAPI TestClient, no torch required)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from fsociety.config import PlatformSettings  # noqa: E402
from fsociety.context import PlatformContext  # noqa: E402
from fsociety.interfaces.api.app import create_app  # noqa: E402

API_KEY = "test-api-key-0123456789"
AUTH = {"X-API-Key": API_KEY}


@pytest.fixture
def client(workspace: Path):
    settings = PlatformSettings(
        workspace=workspace,
        api_key=API_KEY,
        api_rate_limit_per_minute=10_000,
        api_docs_enabled=True,
        serve_frontend=False,
    )
    ctx = PlatformContext(settings)
    app = create_app(context=ctx)
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        yield test_client
    ctx.close()


def test_health_and_version_are_public(client: TestClient) -> None:
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["status"] in {"ok", "degraded"}
    version = client.get("/api/v1/version")
    assert version.status_code == 200
    assert version.json()["version"] == "3.0.0"


def test_auth_required_for_remote_client(client: TestClient) -> None:
    missing = client.get("/api/v1/models")
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "authentication_required"
    wrong = client.get("/api/v1/models", headers={"X-API-Key": "nope"})
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "invalid_api_key"


def test_auth_accepts_bearer(client: TestClient) -> None:
    response = client.get(
        "/api/v1/hardware", headers={"Authorization": f"Bearer {API_KEY}"}
    )
    assert response.status_code == 200
    assert response.json()["cpu"]["logical_cores"]


def test_security_headers_present(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "Content-Security-Policy" in response.headers
    assert response.headers.get("X-Request-ID")


def test_openapi_schema(client: TestClient) -> None:
    schema = client.get("/api/openapi.json")
    assert schema.status_code == 200
    paths = schema.json()["paths"]
    for path in (
        "/api/v1/health",
        "/api/v1/models",
        "/api/v1/optimization/jobs",
        "/api/v1/evaluation/jobs",
        "/api/v1/experiments",
        "/api/v1/security/policy",
    ):
        assert path in paths


def test_settings_redact_api_key(client: TestClient) -> None:
    response = client.get("/api/v1/settings", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["api_key_configured"] is True
    assert "api_key" not in body
    assert API_KEY not in response.text


def test_models_scan_and_inspect(
    client: TestClient, tiny_model: tuple[Path, int], workspace: Path
) -> None:
    model_dir, parameter_count = tiny_model
    target = workspace / "models" / "tiny"
    target.parent.mkdir(parents=True, exist_ok=True)
    model_dir.rename(target)

    scan = client.post("/api/v1/models/scan", headers=AUTH, json={"roots": ["models"]})
    assert scan.status_code == 200, scan.text
    items = scan.json()["items"]
    assert any(item["parameter_count"] == parameter_count for item in items)

    inspect = client.get(
        "/api/v1/models/inspect", headers=AUTH, params={"reference": "models/tiny"}
    )
    assert inspect.status_code == 200, inspect.text
    assert inspect.json()["parameter_count"] == parameter_count

    estimate = client.get(
        "/api/v1/models/estimate",
        headers=AUTH,
        params={"reference": "models/tiny", "sequence_length": 256},
    )
    assert estimate.status_code == 200, estimate.text
    assert estimate.json()["estimate"]["kind"] == "estimate"


def test_models_scan_rejects_traversal(client: TestClient) -> None:
    response = client.post(
        "/api/v1/models/scan", headers=AUTH, json={"roots": ["../../etc"]}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] in {"unsafe_path", "not_found"}


def test_optimization_job_runs_and_persists(
    client: TestClient, tiny_model: tuple[Path, int], workspace: Path
) -> None:
    model_dir, _ = tiny_model
    target = workspace / "models" / "tiny"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        model_dir.rename(target)
    model_dir = target
    body = {
        "name": "api-plan",
        "model_id": str(model_dir),
        "space": {
            "dimensions": [
                {
                    "name": "dtype",
                    "kind": "categorical",
                    "choices": ["float32", "bfloat16"],
                },
                {"name": "batch_size", "kind": "int", "low": 1, "high": 4},
            ]
        },
        "n_trials": 4,
        "seed": 7,
        "sampler": "tpe",
    }
    response = client.post("/api/v1/optimization/jobs", headers=AUTH, json=body)
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]

    deadline = time.time() + 30
    job = {}
    while time.time() < deadline:
        polled = client.get(f"/api/v1/jobs/{job_id}", headers=AUTH)
        assert polled.status_code == 200
        job = polled.json()
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            break
        time.sleep(0.1)
    assert job.get("status") == "succeeded", job
    assert job["experiment_id"]
    trials = client.get(
        f"/api/v1/experiments/{job['experiment_id']}/trials", headers=AUTH
    )
    assert trials.status_code == 200
    assert trials.json()["total"] == 4


def test_optimization_job_validation(
    client: TestClient, tiny_model: tuple[Path, int], workspace: Path
) -> None:
    model_dir, _ = tiny_model
    target = workspace / "models" / "tiny"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        model_dir.rename(target)
    model_dir = target
    body = {
        "name": "bad-space",
        "model_id": str(model_dir),
        "space": {
            "dimensions": [
                {"name": "learning_rate", "kind": "float", "low": 0.0, "high": 1.0}
            ]
        },
        "n_trials": 2,
    }
    response = client.post("/api/v1/optimization/jobs", headers=AUTH, json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"

    unknown = client.post(
        "/api/v1/evaluation/jobs",
        headers=AUTH,
        json={
            "suite_id": "does-not-exist",
            "backend": {"kind": "transformers", "model_id": "x"},
        },
    )
    assert unknown.status_code == 404


def test_unknown_api_route_returns_structured_error(client: TestClient) -> None:
    response = client.get("/api/v1/definitely-not-a-route", headers=AUTH)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_events_endpoint_records_activity(client: TestClient) -> None:
    client.get("/api/v1/hardware", headers=AUTH)
    events = client.get("/api/v1/events", headers=AUTH)
    assert events.status_code == 200
    assert "items" in events.json()


def test_experiment_not_found(client: TestClient) -> None:
    response = client.get("/api/v1/experiments/exp_missing", headers=AUTH)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_evaluation_job_reports_missing_engine(
    client: TestClient,
) -> None:
    import importlib.util

    if importlib.util.find_spec("torch") is not None:
        pytest.skip("Engine installed: this test exercises the no-engine failure path.")
    response = client.post(
        "/api/v1/evaluation/jobs",
        headers=AUTH,
        json={
            "suite_id": "knowledge_core",
            "backend": {"kind": "transformers", "model_id": "org/does-not-matter"},
            "limit": 1,
        },
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    deadline = time.time() + 20
    job = {}
    while time.time() < deadline:
        job = client.get(f"/api/v1/jobs/{job_id}", headers=AUTH).json()
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            break
        time.sleep(0.1)
    assert job["status"] == "failed", job
    assert "engine_unavailable" in (job.get("error") or ""), job
