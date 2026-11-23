# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""End-to-end evaluation persistence and report retrieval through the API."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from fsociety.config import PlatformSettings  # noqa: E402
from fsociety.context import PlatformContext  # noqa: E402
from fsociety.evaluation.backends import CallableBackend  # noqa: E402
from fsociety.evaluation.runner import EvaluationConfig, EvaluationRunner  # noqa: E402
from fsociety.evaluation.suites import SuiteRegistry  # noqa: E402
from fsociety.interfaces.api.app import create_app  # noqa: E402

API_KEY = "eval-test-key-0123456789"
AUTH = {"X-API-Key": API_KEY}


def test_report_is_persisted_and_retrievable(workspace: Path) -> None:
    settings = PlatformSettings(
        workspace=workspace,
        api_key=API_KEY,
        api_rate_limit_per_minute=10_000,
        serve_frontend=False,
    )
    ctx = PlatformContext(settings)
    app = create_app(context=ctx)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        registry = SuiteRegistry()
        items = registry.load_items("knowledge_core")

        def answer(prompts: list[str]) -> list[str]:
            return [str(item.expected or "") for item in items[: len(prompts)]]

        backend = CallableBackend(answer, name="test-callable", model_id="test/model")
        runner = EvaluationRunner(
            store=ctx.store, logger=ctx.logger, hardware=ctx.hardware()
        )
        report = runner.run(
            "knowledge_core",
            backend,
            EvaluationConfig(seed=1, limit=3, max_new_tokens=16),
            save_to=ctx.workspace.reports,
        )
        assert report.status == "succeeded"
        assert report.experiment_id

        response = client.get(
            f"/api/v1/experiments/{report.experiment_id}/report", headers=AUTH
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["report_id"] == report.report_id
        assert payload["suite_hash"] == report.suite_hash
        assert payload["seed"] == 1

        markdown = client.get(
            f"/api/v1/experiments/{report.experiment_id}/report",
            params={"format": "markdown"},
            headers=AUTH,
        )
        assert markdown.status_code == 200
        assert "FSOCIETY" in markdown.text or "Suite" in markdown.text

        artifacts = client.get(
            f"/api/v1/experiments/{report.experiment_id}/artifacts", headers=AUTH
        )
        assert artifacts.status_code == 200
        kinds = {item["kind"] for item in artifacts.json()["items"]}
        assert "evaluation_report" in kinds or "report" in kinds

        found = client.get(
            f"/api/v1/evaluation/reports/{report.report_id}", headers=AUTH
        )
        assert found.status_code == 200
        assert found.json()["report_id"] == report.report_id
    ctx.close()
