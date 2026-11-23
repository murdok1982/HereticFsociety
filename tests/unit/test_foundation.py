# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Foundation tests: paths, config, redaction, store, hardware, estimator."""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from fsociety.config import PlatformSettings, load_settings
from fsociety.core.estimator import (
    EstimationRequest,
    estimate_from_parameter_count,
    estimate_memory,
)
from fsociety.core.hardware import (
    CommandResult,
    DetectionOptions,
    accelerator_budget_bytes,
    detect_hardware,
    hardware_fingerprint,
)
from fsociety.core.models import (
    CpuInfo,
    EventRecord,
    GpuInfo,
    HardwareProfile,
    ModelInfo,
    TorchInfo,
)
from fsociety.errors import PathSecurityError, ValidationError
from fsociety.experiments.store import ExperimentStore
from fsociety.logging import AuditLogger
from fsociety.paths import safe_join
from fsociety.security.redaction import redact, redact_obj, redact_tree

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def test_safe_join_allows_nested_path(tmp_path: Path) -> None:
    assert safe_join(tmp_path, "models", "a") == (tmp_path / "models" / "a").resolve()


def test_safe_join_rejects_traversal(tmp_path: Path) -> None:
    with pytest.raises(PathSecurityError):
        safe_join(tmp_path, "../etc/passwd")
    with pytest.raises(PathSecurityError):
        safe_join(tmp_path, "..", "..")


def test_safe_join_rejects_absolute(tmp_path: Path) -> None:
    with pytest.raises(PathSecurityError):
        safe_join(tmp_path, "/etc/passwd")


def test_safe_join_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    try:
        (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks are not supported on this filesystem.")
    with pytest.raises(PathSecurityError):
        safe_join(tmp_path, "link", "secret.txt")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_config_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FSOCIETY_API_PORT", "9123")
    monkeypatch.setenv("FSOCIETY_WORKSPACE", str(tmp_path / "ws"))
    settings = load_settings()
    assert settings.api_port == 9123
    assert settings.workspace == tmp_path / "ws"


def test_config_toml(tmp_path: Path) -> None:
    config = tmp_path / "fsociety.toml"
    config.write_text(
        '[fsociety]\napi_port = 9001\ntrust_remote_code = "allowlist"\n'
        'trusted_repositories = ["org/model"]\n',
        encoding="utf-8",
    )
    settings = load_settings(config)
    assert settings.api_port == 9001
    assert settings.remote_code_allowed("org/model")
    assert not settings.remote_code_allowed("other/model")


def test_config_rejects_wildcard_cors() -> None:
    with pytest.raises(PydanticValidationError):
        PlatformSettings(api_cors_origins=["*"])


def test_config_rejects_bad_trust_repo() -> None:
    with pytest.raises(PydanticValidationError):
        PlatformSettings(trusted_repositories=["../../etc"])


def test_config_redacts_api_key() -> None:
    settings = PlatformSettings(api_key="hf_" + "S" * 34)
    payload = settings.redacted()
    assert "api_key" not in payload
    assert payload["api_key_configured"] is True
    assert "hf_" + "S" * 34 not in json.dumps(payload)


def test_config_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        load_settings(tmp_path / "missing.toml")


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "secret",
    [
        "hf_" + "a" * 34,
        "sk-abcdefghijklmnopqrstuvwx",
        "AKIAIOSFODNN7EXAMPLE",
        "ghp_" + "b" * 36,
    ],
)
def test_redaction_removes_tokens(secret: str) -> None:
    text = f"token={secret} trailing"
    assert secret not in redact(text)
    assert "REDACTED" in redact(text)


def test_redaction_nested() -> None:
    payload = {"a": ["hf_" + "c" * 34], "n": 1}
    cleaned = redact_obj(payload)
    assert "hf_" + "c" * 34 not in json.dumps(cleaned)
    assert cleaned["n"] == 1


def test_redact_tree_finds_files(tmp_path: Path) -> None:
    (tmp_path / "leak.txt").write_text("key = hf_" + "d" * 34, encoding="utf-8")
    (tmp_path / "clean.txt").write_text("hello", encoding="utf-8")
    findings = redact_tree(tmp_path)
    assert any("leak.txt" in path for path in findings)
    assert not any("clean.txt" in path for path in findings)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def test_store_roundtrip(tmp_path: Path) -> None:
    store = ExperimentStore(tmp_path / "db.sqlite3")
    from fsociety.core.models import ExperimentRecord, JobRecord, TrialRecord

    experiment = ExperimentRecord(
        experiment_id="exp_1", name="test", kind="evaluation", model_id="m"
    )
    store.create_experiment(experiment)
    store.add_trial(
        TrialRecord(
            trial_id="t1",
            experiment_id="exp_1",
            number=0,
            state="complete",
            params={"x": 1},
            values=[0.5],
            objective_names=["score"],
        )
    )
    assert store.get_experiment("exp_1").name == "test"
    assert store.list_trials("exp_1")[0].values == [0.5]
    job = store.create_job(JobRecord(job_id="job_1", kind="test"))
    store.update_job("job_1", status="running", progress=0.5)
    assert store.get_job("job_1").progress == 0.5
    assert job.status == "queued"
    exported = store.export_json()
    assert exported["experiments"][0]["experiment_id"] == "exp_1"
    store.close()


def test_store_concurrent_events(tmp_path: Path) -> None:
    store = ExperimentStore(tmp_path / "db.sqlite3")

    def writer(index: int) -> None:
        for n in range(25):
            store.add_event(EventRecord(message=f"w{index}-{n}", category="test"))

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    events = store.list_events(limit=500)
    assert len(events) == 200
    store.close()


def test_store_rejects_unknown_field(tmp_path: Path) -> None:
    store = ExperimentStore(tmp_path / "db.sqlite3")
    from fsociety.core.models import ExperimentRecord

    store.create_experiment(
        ExperimentRecord(experiment_id="exp_2", name="x", kind="evaluation")
    )
    with pytest.raises(ValueError, match="Unknown experiment fields"):
        store.update_experiment("exp_2", made_up=1)
    store.close()


def test_audit_logger_redacts(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    logger = AuditLogger(path, level="DEBUG")
    logger.warning("token hf_" + "e" * 34 + " leaked")
    content = path.read_text(encoding="utf-8")
    assert "hf_" + "e" * 34 not in content
    assert "REDACTED" in content
    assert logger.read_recent(1)[0]["level"] == "warning"


# ---------------------------------------------------------------------------
# Hardware
# ---------------------------------------------------------------------------


def _profile(**overrides) -> HardwareProfile:
    data = dict(
        hostname="test",
        os="Linux",
        os_release="6",
        architecture="x86_64",
        python_version="3.12",
        cpu=CpuInfo(model="Test CPU", physical_cores=8, logical_cores=16),
        ram_total_bytes=32 * 1024**3,
        ram_available_bytes=24 * 1024**3,
    )
    data.update(overrides)
    return HardwareProfile(**data)


def test_hardware_detection_cpu_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fsociety.core.hardware.shutil.which", lambda name: None)
    profile = detect_hardware(
        DetectionOptions(probe_torch=False),
        runner=lambda command, timeout: CommandResult(ok=False),
    )
    assert profile.cpu.logical_cores
    assert profile.gpus == []
    assert profile.backends["cpu"] is True
    assert profile.fingerprint
    assert any("No accelerator" in warning for warning in profile.warnings)


def test_hardware_detection_nvidia(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "fsociety.core.hardware.shutil.which",
        lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None,
    )

    def runner(command, timeout):
        if "nvidia-smi" in command[0]:
            return CommandResult(
                ok=True,
                stdout="0, NVIDIA RTX 4090, 24564, 20000, 8.9, 555.42\n",
            )
        return CommandResult(ok=False)

    profile = detect_hardware(DetectionOptions(probe_torch=False), runner=runner)
    assert len(profile.gpus) == 1
    gpu = profile.gpus[0]
    assert gpu.name == "NVIDIA RTX 4090"
    assert gpu.total_memory_bytes == 24564 * 1024 * 1024
    assert gpu.compute_capability == "8.9"
    assert gpu.source == "measured"
    assert profile.backends["cuda_driver"] is True
    assert accelerator_budget_bytes(profile) == gpu.total_memory_bytes


def test_hardware_detection_mps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fsociety.core.hardware.shutil.which", lambda name: None)
    monkeypatch.setattr("fsociety.core.hardware.platform.system", lambda: "Darwin")
    profile = detect_hardware(
        DetectionOptions(probe_torch=True),
        runner=lambda command, timeout: CommandResult(ok=False),
        torch_probe=lambda: TorchInfo(
            installed=True, version="2.9", mps_available=True, dtype_support=["float16"]
        ),
    )
    assert profile.gpus and profile.gpus[0].backend == "mps"
    assert profile.backends["mps_torch"] is True


def test_hardware_fingerprint_stable_and_volatile_free() -> None:
    first = _profile(ram_available_bytes=1)
    second = _profile(ram_available_bytes=999)
    assert hardware_fingerprint(first) == hardware_fingerprint(second)
    third = _profile(gpus=[GpuInfo(name="Other", backend="cuda", total_memory_bytes=1)])
    assert hardware_fingerprint(first) != hardware_fingerprint(third)


# ---------------------------------------------------------------------------
# Estimator
# ---------------------------------------------------------------------------


def _model(params: int = 7_000_000_000) -> ModelInfo:
    return ModelInfo(
        model_id="org/model",
        parameter_count=params,
        hidden_size=4096,
        num_hidden_layers=32,
        num_attention_heads=32,
        num_key_value_heads=8,
        intermediate_size=11008,
    )


def test_estimate_requires_parameter_count() -> None:
    with pytest.raises(ValidationError):
        estimate_memory(ModelInfo(model_id="x"))


def test_estimate_scales_with_batch_and_sequence() -> None:
    hw = _profile(ram_total_bytes=512 * 1024**3, ram_available_bytes=512 * 1024**3)
    small = estimate_memory(_model(1_000_000_000), EstimationRequest(), hw)
    large = estimate_memory(
        _model(1_000_000_000),
        EstimationRequest(batch_size=4, sequence_length=8192),
        hw,
    )
    assert large.breakdown.kv_cache_bytes > small.breakdown.kv_cache_bytes
    assert large.breakdown.activations_bytes > small.breakdown.activations_bytes
    assert large.breakdown.weights_bytes == small.breakdown.weights_bytes


def test_estimate_quantization_reduces_weights() -> None:
    fp16 = estimate_memory(_model(), EstimationRequest(dtype="float16"))
    q4 = estimate_memory(_model(), EstimationRequest(quantization="bnb_4bit"))
    assert q4.breakdown.weights_bytes < fp16.breakdown.weights_bytes / 3


def test_estimate_does_not_fit_with_alternatives() -> None:
    hw = _profile(ram_total_bytes=8 * 1024**3, ram_available_bytes=6 * 1024**3)
    estimate = estimate_memory(_model(7_000_000_000), EstimationRequest(), hw)
    assert estimate.fits is False
    assert estimate.alternatives
    assert estimate.recommended_quantization


def test_estimate_fits_small_model() -> None:
    hw = _profile()
    estimate = estimate_memory(
        _model(124_000_000),
        EstimationRequest(sequence_length=256),
        hw,
    )
    assert estimate.fits is True
    assert estimate.recommended_batch_size
    assert estimate.breakdown.total_bytes == sum(
        [
            estimate.breakdown.weights_bytes,
            estimate.breakdown.kv_cache_bytes,
            estimate.breakdown.activations_bytes,
            estimate.breakdown.overhead_bytes,
        ]
    )


def test_estimate_without_hardware_is_unknown() -> None:
    estimate = estimate_from_parameter_count(
        "org/x", 1_000_000, hidden_size=1024, num_hidden_layers=12
    )
    assert estimate.fits is None
    assert estimate.confidence == "estimated"
    assert any("No hardware" in item for item in estimate.assumptions)


def test_estimate_invalid_dtype() -> None:
    with pytest.raises(ValidationError):
        estimate_memory(_model(), EstimationRequest(dtype="float8"))


def test_core_import_does_not_load_torch() -> None:
    import subprocess

    root = Path(__file__).resolve().parents[2]
    code = (
        "import sys; import fsociety.core.estimator, fsociety.core.hardware; "
        "print('torch' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=root,
        env={"PYTHONPATH": str(root / "src"), "PATH": "/usr/bin:/bin"},
        check=True,
    )
    assert result.stdout.strip() == "False"
