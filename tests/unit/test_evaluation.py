# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Unit tests for suites, backends, the runner, reporting and the lm-eval adapter."""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from fsociety.core.hardware import hardware_fingerprint
from fsociety.core.models import HardwareProfile
from fsociety.errors import (
    EngineUnavailableError,
    EvaluationError,
    NotFoundError,
    ValidationError,
)
from fsociety.evaluation import (
    CallableBackend,
    EvaluationConfig,
    EvaluationRunner,
    SuiteRegistry,
    TransformersBackend,
    config_hash_for,
    lm_eval_adapter,
    load_backend,
    load_report,
    rebuild_manifest,
    write_report,
)
from fsociety.evaluation.suites import DEFAULT_SUITE_DIR, MANIFEST_FILENAME
from fsociety.experiments.store import ExperimentStore

EXPECTED_SUITE_IDS = [
    "defensive_cybersecurity",
    "hallucination_resistance",
    "instruction_following",
    "knowledge_core",
    "math_word_problems",
    "python_coding",
    "spanish_language",
]


def make_hardware() -> HardwareProfile:
    profile = HardwareProfile(
        hostname="test-host",
        os="Linux",
        os_release="6.0.0-test",
        architecture="x86_64",
        python_version="3.12.0",
        ram_total_bytes=16 * 1024**3,
        detection_mode="partial",
    )
    profile.fingerprint = hardware_fingerprint(profile)
    return profile


def answers_for(registry: SuiteRegistry, suite_id: str) -> dict[str, str]:
    answers: dict[str, str] = {}
    for benchmark_item in registry.load_items(suite_id):
        if benchmark_item.expected is None:
            continue
        answers[benchmark_item.prompt] = benchmark_item.expected
    return answers


# ---------------------------------------------------------------------------
# Suite registry
# ---------------------------------------------------------------------------


def test_all_bundled_suites_load():
    registry = SuiteRegistry()
    assert registry.suite_ids() == EXPECTED_SUITE_IDS
    assert len(registry) == len(EXPECTED_SUITE_IDS)
    assert registry.manifest_verified is True
    for info in registry.list_suites():
        assert info.item_count >= 10
        assert info.sha256 and len(info.sha256) == 64
        assert info.categories
        assert info.provenance
        assert info.limitations
        assert "NOT" in info.provenance.upper() or "not" in info.provenance
        items = registry.load_items(info.suite_id)
        assert len(items) == info.item_count


def test_suite_hashes_stable_and_item_ids_unique():
    first = SuiteRegistry()
    second = SuiteRegistry()
    for suite_id in first.suite_ids():
        assert first.suite_hash(suite_id) == second.suite_hash(suite_id)
        assert first.file_sha256(suite_id) == second.file_sha256(suite_id)
        items = first.load_items(suite_id)
        item_ids = [benchmark_item.item_id for benchmark_item in items]
        assert len(item_ids) == len(set(item_ids))


def test_manifest_covers_every_suite():
    manifest_path = DEFAULT_SUITE_DIR / MANIFEST_FILENAME
    assert manifest_path.is_file()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["manifest_version"] == 1
    assert sorted(payload["suites"]) == EXPECTED_SUITE_IDS
    for entry in payload["suites"].values():
        assert entry["item_count"] >= 10
        assert len(entry["suite_sha256"]) == 64


def test_unknown_suite_raises_not_found():
    registry = SuiteRegistry()
    with pytest.raises(NotFoundError):
        registry.get("missing_suite")
    with pytest.raises(NotFoundError):
        registry.load_items("missing_suite")


def test_manifest_detects_tampering(tmp_path: Path):
    target = tmp_path / "benchmarks"
    shutil.copytree(DEFAULT_SUITE_DIR, target)
    suite_path = target / "knowledge_core.json"
    original = suite_path.read_text(encoding="utf-8")
    suite_path.write_text(original.replace("Canberra", "Sydney"), encoding="utf-8")

    with pytest.raises(ValidationError) as excinfo:
        SuiteRegistry(target)
    problems = excinfo.value.details.get("problems", [])
    assert any("knowledge_core" in problem for problem in problems)

    rebuild_manifest(target)
    repaired = SuiteRegistry(target)
    assert repaired.manifest_verified is True
    assert repaired.suite_hash("knowledge_core") != SuiteRegistry().suite_hash(
        "knowledge_core"
    )


def test_registry_rejects_invalid_suite_documents(tmp_path: Path):
    document = {
        "suite_id": "bad_suite",
        "name": "Bad suite",
        "version": "1.0.0",
        "description": "A suite used to exercise validation errors.",
        "grader": "numeric",
        "language": "en",
        "tags": [],
        "provenance": "Synthetic document created only for structural validation tests.",
        "limitations": ["none"],
        "items": [
            {"item_id": "dup", "prompt": "one", "expected": "1"},
            {"item_id": "dup", "prompt": "two", "expected": "2"},
        ],
    }
    (tmp_path / "bad.json").write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValidationError):
        SuiteRegistry(tmp_path, verify_manifest=False)

    document["items"][1]["item_id"] = "unique"
    document["grader"] = "not_a_grader"
    (tmp_path / "bad.json").write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValidationError):
        SuiteRegistry(tmp_path, verify_manifest=False)


def test_defensive_suite_contains_only_defensive_content():
    registry = SuiteRegistry()
    items = registry.load_items("defensive_cybersecurity")
    offensive_phrases = [
        "reverse shell",
        "exploit code",
        "how to hack",
        "write malware",
        "create a virus",
        "steal credentials",
        "bypass authentication",
        "keylogger",
        "sqlmap",
        "phishing kit",
        "crack password",
        "privilege escalation",
        "ddos",
        "payload",
    ]
    for benchmark_item in items:
        assert "key_concepts" in benchmark_item.metadata
        haystack = (
            f"{benchmark_item.prompt} {json.dumps(benchmark_item.metadata)}".casefold()
        )
        for phrase in offensive_phrases:
            assert phrase not in haystack, (benchmark_item.item_id, phrase)


def test_hallucination_suite_marks_every_item_unanswerable():
    registry = SuiteRegistry()
    items = registry.load_items("hallucination_resistance")
    assert items
    for benchmark_item in items:
        assert benchmark_item.metadata.get("requires_abstention") is True
        assert benchmark_item.expected is None


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


def test_callable_backend_contract():
    backend = CallableBackend(
        lambda prompts: ["x" for _ in prompts], name="scripted", model_id="m"
    )
    assert backend.name == "scripted"
    assert backend.model_id == "m"
    assert backend.generate(["prompt"], 4) == ["x"]
    assert backend.metadata()["kind"] == "callable"


def test_callable_backend_validates_input_and_output():
    backend = CallableBackend(lambda prompts: ["x" for _ in prompts])
    with pytest.raises(ValidationError):
        backend.generate([], 4)
    with pytest.raises(ValidationError):
        backend.generate(["   "], 4)
    with pytest.raises(ValidationError):
        backend.generate(["ok"], 0)

    with pytest.raises(EvaluationError):
        CallableBackend(lambda prompts: "not a list").generate(["a"], 4)
    with pytest.raises(EvaluationError):
        CallableBackend(lambda prompts: []).generate(["a"], 4)
    with pytest.raises(EvaluationError):
        CallableBackend(lambda prompts: [None]).generate(["a"], 4)


def test_load_backend_rejects_invalid_specs():
    with pytest.raises(ValidationError):
        load_backend("not-a-dict")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        load_backend({"kind": "fake"})
    with pytest.raises(ValidationError):
        load_backend({"kind": "transformers"})
    with pytest.raises(ValidationError):
        load_backend({"kind": "transformers", "model_id": "org/model", "unknown": 1})
    with pytest.raises(ValidationError):
        load_backend({"kind": "llama_cpp", "path": "/does/not/exist.gguf"})


@pytest.mark.skipif(
    importlib.util.find_spec("torch") is not None
    and importlib.util.find_spec("transformers") is not None,
    reason="torch/transformers are installed; the unavailable path cannot be exercised",
)
def test_transformers_backend_unavailable_error_is_actionable():
    with pytest.raises(EngineUnavailableError) as excinfo:
        TransformersBackend("org/tiny-model")
    assert excinfo.value.details["missing"]
    assert excinfo.value.hint and "pip install" in excinfo.value.hint


def test_llama_backend_requires_existing_file(tmp_path: Path):
    from fsociety.evaluation import LlamaCppBackend

    with pytest.raises(ValidationError):
        LlamaCppBackend(str(tmp_path / "missing.gguf"))


@pytest.mark.skipif(
    importlib.util.find_spec("llama_cpp") is not None,
    reason="llama-cpp-python is installed; the unavailable path cannot be exercised",
)
def test_llama_backend_unavailable_error_is_actionable(tmp_path: Path):
    from fsociety.evaluation import LlamaCppBackend

    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF")
    with pytest.raises(EngineUnavailableError) as excinfo:
        LlamaCppBackend(str(model))
    assert excinfo.value.details["missing"] == ["llama_cpp"]
    assert excinfo.value.hint and "fsociety-ai[llama]" in excinfo.value.hint


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def test_runner_metrics_and_report_files(tmp_path: Path):
    registry = SuiteRegistry()
    answers = answers_for(registry, "math_word_problems")
    backend = CallableBackend(
        lambda prompts: [answers[prompt] for prompt in prompts],
        name="scripted",
        model_id="scripted-numeric",
    )
    hardware = make_hardware()
    runner = EvaluationRunner(hardware=hardware, registry=registry)
    report = runner.run(
        "math_word_problems",
        backend,
        EvaluationConfig(seed=7, max_new_tokens=16),
        save_to=tmp_path,
    )

    assert report.status == "succeeded"
    assert report.model_id == "scripted-numeric"
    assert report.backend == "scripted"
    assert report.seed == 7
    assert report.suite_hash == registry.suite_hash("math_word_problems")
    assert report.hardware_fingerprint == hardware.fingerprint
    assert report.metrics["item_count"] == 10
    assert report.metrics["planned_item_count"] == 10
    assert report.metrics["pass_rate"] == 1.0
    assert report.metrics["mean_score"] == 1.0
    assert report.metrics["error_rate"] == 0.0
    assert report.metrics["grader_version"] == "1"
    assert report.metrics["refusal_rate_heuristic"] is True
    assert set(report.category_metrics) == set(
        registry.categories("math_word_problems")
    )
    assert all(result.prompt_sha256 for result in report.results)

    json_files = list(tmp_path.glob("*.json"))
    markdown_files = list(tmp_path.glob("*.md"))
    assert len(json_files) == 1
    assert len(markdown_files) == 1
    markdown = markdown_files[0].read_text(encoding="utf-8")
    for expected in (
        report.model_id,
        report.suite_hash,
        str(report.seed),
        report.hardware_fingerprint,
        "## Conditions",
        "## Limitations",
        "## Suite provenance",
    ):
        assert expected in markdown
    reloaded = load_report(json_files[0])
    assert reloaded.report_id == report.report_id
    assert reloaded.metrics["pass_rate"] == 1.0


def test_runner_isolates_backend_errors_per_item():
    registry = SuiteRegistry()
    answers = answers_for(registry, "knowledge_core")

    def flaky(prompts: list[str]) -> list[str]:
        responses = []
        for prompt in prompts:
            if "planet" in prompt.casefold():
                raise RuntimeError("backend exploded")
            responses.append(answers[prompt])
        return responses

    backend = CallableBackend(flaky, name="scripted", model_id="flaky")
    runner = EvaluationRunner(hardware=make_hardware(), registry=registry)
    report = runner.run(
        "knowledge_core",
        backend,
        EvaluationConfig(seed=1, batch_size=4),
    )

    assert report.status == "succeeded"
    errored = [result for result in report.results if result.error]
    assert len(errored) == 1
    assert errored[0].item_id == "kc-004"
    assert errored[0].score is None
    assert report.metrics["error_count"] == 1
    assert report.metrics["graded_count"] == len(report.results) - 1
    assert report.metrics["error_rate"] == pytest.approx(
        1 / len(report.results), abs=1e-6
    )
    assert any("retried individually" in warning for warning in report.warnings)


def test_runner_cancel_stops_and_keeps_partial_results():
    registry = SuiteRegistry()
    answers = answers_for(registry, "knowledge_core")
    backend = CallableBackend(
        lambda prompts: [answers[prompt] for prompt in prompts],
        name="scripted",
        model_id="cancellable",
    )
    flag = {"cancel": False}

    def progress(_done: int, _total: int, _result: object) -> None:
        flag["cancel"] = True

    runner = EvaluationRunner(hardware=make_hardware(), registry=registry)
    report = runner.run(
        "knowledge_core",
        backend,
        EvaluationConfig(seed=1),
        progress=progress,
        cancel=lambda: flag["cancel"],
    )

    assert report.status == "cancelled"
    assert 0 < len(report.results) < registry.get("knowledge_core").item_count
    assert (
        report.metrics["planned_item_count"]
        == registry.get("knowledge_core").item_count
    )
    assert any("cancelled" in warning for warning in report.warnings)


def test_runner_persists_experiment_and_artifact(tmp_path: Path):
    registry = SuiteRegistry()
    answers = answers_for(registry, "math_word_problems")
    backend = CallableBackend(
        lambda prompts: [answers[prompt] for prompt in prompts],
        name="scripted",
        model_id="persisted-model",
    )
    hardware = make_hardware()
    store = ExperimentStore(":memory:")
    try:
        runner = EvaluationRunner(store=store, hardware=hardware, registry=registry)
        config = EvaluationConfig(seed=11, limit=3)
        report = runner.run(
            "math_word_problems",
            backend,
            config,
            save_to=tmp_path,
        )
        experiments = store.list_experiments(kind="evaluation")
        assert len(experiments) == 1
        experiment = experiments[0]
        assert experiment.status == "succeeded"
        assert experiment.model_id == "persisted-model"
        assert experiment.seed == 11
        assert experiment.suite_hash == report.suite_hash
        assert experiment.config_hash == config_hash_for(config)
        assert experiment.hardware_fingerprint == hardware.fingerprint
        assert experiment.metrics["pass_rate"] == 1.0
        assert report.experiment_id == experiment.experiment_id

        artifacts = store.list_artifacts(experiment.experiment_id)
        assert len(artifacts) == 1
        assert artifacts[0].kind == "evaluation_report"
        assert Path(artifacts[0].path).is_file()
        assert artifacts[0].size_bytes > 0
    finally:
        store.close()


def test_runner_fails_when_nothing_can_be_graded():
    registry = SuiteRegistry()

    def broken(prompts: list[str]) -> list[str]:
        raise RuntimeError("no model")

    backend = CallableBackend(broken, name="broken", model_id="broken")
    runner = EvaluationRunner(hardware=make_hardware(), registry=registry)
    report = runner.run("math_word_problems", backend, EvaluationConfig(limit=2))
    assert report.status == "failed"
    assert report.metrics["graded_count"] == 0
    assert all(result.error for result in report.results)


def test_runner_validates_config_and_backend():
    registry = SuiteRegistry()
    runner = EvaluationRunner(hardware=make_hardware(), registry=registry)
    backend = CallableBackend(lambda prompts: ["" for _ in prompts])
    with pytest.raises(ValidationError):
        runner.run("math_word_problems", backend, {"batch_size": 0})  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        runner.run("math_word_problems", object(), None)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        runner.run("", backend)
    with pytest.raises(NotFoundError):
        runner.run("missing_suite", backend)


def test_config_hash_ignores_batch_size_but_tracks_seed():
    first = EvaluationConfig(seed=42, batch_size=1)
    second = EvaluationConfig(seed=42, batch_size=8)
    third = EvaluationConfig(seed=43, batch_size=1)
    assert config_hash_for(first) == config_hash_for(second)
    assert config_hash_for(first) != config_hash_for(third)


def test_reporting_roundtrip(tmp_path: Path):
    registry = SuiteRegistry()
    answers = answers_for(registry, "math_word_problems")
    backend = CallableBackend(lambda prompts: [answers[prompt] for prompt in prompts])
    runner = EvaluationRunner(hardware=make_hardware(), registry=registry)
    report = runner.run("math_word_problems", backend, EvaluationConfig(limit=2))
    json_path, markdown_path = write_report(report, tmp_path)
    assert json_path.is_file() and markdown_path.is_file()
    loaded = load_report(json_path)
    assert loaded.model_dump(mode="json") == report.model_dump(mode="json")
    with pytest.raises(EvaluationError):
        load_report(tmp_path / "missing.json")


def test_response_truncation_is_recorded():
    registry = SuiteRegistry()
    long_response = "x" * 12_000
    backend = CallableBackend(lambda prompts: [long_response for _ in prompts])
    runner = EvaluationRunner(hardware=make_hardware(), registry=registry)
    report = runner.run("math_word_problems", backend, EvaluationConfig(limit=1))
    result = report.results[0]
    assert len(result.response or "") == 8_000
    assert result.details["response_truncated"] is True
    assert result.details["response_chars_full"] == 12_000
    assert any("truncated" in warning for warning in report.warnings)


# ---------------------------------------------------------------------------
# lm-evaluation-harness adapter
# ---------------------------------------------------------------------------


def test_lm_eval_unavailable_error_is_actionable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(lm_eval_adapter, "lm_eval_available", lambda: False)
    with pytest.raises(EngineUnavailableError) as excinfo:
        lm_eval_adapter.run_lm_eval("org/model", ["hellaswag"])
    assert excinfo.value.code == "engine_unavailable"
    assert excinfo.value.hint and "pip install" in excinfo.value.hint


def test_lm_eval_build_command_is_a_real_subprocess_argv(tmp_path: Path):
    command = lm_eval_adapter.build_command(
        "org/model",
        ["hellaswag", "arc_easy"],
        num_fewshot=2,
        batch_size=4,
        device="cpu",
        output_path=tmp_path,
        limit=10,
        seed=7,
    )
    assert command[1:3] == ["-m", "lm_eval"]
    assert "--model" in command and command[command.index("--model") + 1] == "hf"
    model_args = command[command.index("--model_args") + 1]
    assert "pretrained=org/model" in model_args
    assert "trust_remote_code" not in model_args
    tasks = command[command.index("--tasks") + 1]
    assert tasks == "hellaswag,arc_easy"
    assert command[command.index("--num_fewshot") + 1] == "2"
    assert command[command.index("--limit") + 1] == "10"
    assert command[command.index("--seed") + 1] == "7"

    trusted = lm_eval_adapter.build_command(
        "org/model", ["hellaswag"], output_path=tmp_path, trust_remote_code=True
    )
    assert "trust_remote_code=True" in trusted[trusted.index("--model_args") + 1]


def test_lm_eval_build_command_rejects_unsafe_tasks(tmp_path: Path):
    with pytest.raises(ValidationError):
        lm_eval_adapter.build_command("org/model", ["-x"], output_path=tmp_path)
    with pytest.raises(ValidationError):
        lm_eval_adapter.build_command("org/model", [], output_path=tmp_path)


def test_lm_eval_trust_policy_denies_remote_code():
    from fsociety.config import PlatformSettings
    from fsociety.errors import TrustPolicyError

    settings = PlatformSettings(trust_remote_code="never")
    with pytest.raises(TrustPolicyError):
        lm_eval_adapter.ensure_trust(settings, "org/model", requested=True)
