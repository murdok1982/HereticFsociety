# SPDX-License-Identifier: AGPL-3.0-or-later
"""Plugin API, discovery, isolation and builtin scorer tests."""

from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path
from typing import Any

import pytest

from fsociety import PLUGIN_API_VERSION
from fsociety.config import PlatformSettings
from fsociety.errors import NotFoundError, PluginError, PluginTimeoutError
from fsociety.plugins.api import (
    Scorer,
    validate_plugin_class,
)
from fsociety.plugins.isolation import (
    detect_network_isolation,
    run_isolated,
    sandbox_env,
)
from fsociety.plugins.registry import PluginRegistry
from fsociety.plugins.scorers import (
    BUILTIN_SCORERS,
    BehavioralRegressionScorer,
    CodeQualityScorer,
    DefensiveCybersecurityScorer,
    HallucinationScorer,
    ModelIntegrityScorer,
    ReasoningScorer,
    SpanishLanguageScorer,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "plugins"


@pytest.fixture()
def settings(tmp_path: Path) -> PlatformSettings:
    return PlatformSettings(
        workspace=tmp_path / "workspace",
        plugin_timeout_seconds=2,
        plugin_max_output_bytes=1_000_000,
        plugin_allow_network=False,
        plugin_isolation=True,
    )


@pytest.fixture()
def registry(settings: PlatformSettings) -> PluginRegistry:
    return PluginRegistry([FIXTURES], settings=settings)


def _fixture(name: str) -> Path:
    return FIXTURES / name


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_all_builtin_scorers_validate() -> None:
    for cls in BUILTIN_SCORERS:
        info = validate_plugin_class(cls)
        assert info.valid, (cls.__name__, info.errors)
        assert info.kind == "scorer"
        assert info.api_version == PLUGIN_API_VERSION
        assert info.version == "1.0.0"


def test_wrong_api_version_rejected(registry: PluginRegistry) -> None:
    info = registry.get("fixture_wrong_api")
    assert info.valid is False
    assert any("API version" in error for error in info.errors)


def test_missing_dependency_rejected(registry: PluginRegistry) -> None:
    info = registry.get("fixture_missing_dependency")
    assert info.valid is False
    assert any("not importable" in error for error in info.errors)


def test_unknown_permission_rejected(registry: PluginRegistry) -> None:
    info = registry.get("fixture_unknown_permission")
    assert info.valid is False
    assert any("root_access" in error for error in info.errors)


def test_wrong_base_class_rejected(registry: PluginRegistry) -> None:
    info = registry.get("fixture_wrong_base")
    assert info.valid is False
    assert any("PluginBase" in error for error in info.errors)


def test_abstract_class_rejected(registry: PluginRegistry) -> None:
    info = registry.get("fixture_abstract")
    assert info.valid is False
    assert any("abstract" in error for error in info.errors)


def test_validate_rejects_bad_metadata() -> None:
    class BadMetadataScorer(Scorer):
        name = ""
        version = "not-semver"
        api_version = PLUGIN_API_VERSION
        kind = "scorer"

        def score(self, item: dict[str, Any]) -> float:
            return 0.0

    info = validate_plugin_class(BadMetadataScorer)
    assert info.valid is False
    assert any("name" in error for error in info.errors)
    assert any("semver" in error for error in info.errors)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_discovery_keeps_invalid_plugins_and_healthy_ones(
    registry: PluginRegistry,
) -> None:
    names = {info.name for info in registry.list()}
    assert "fixture_valid" in names
    assert "import_failure" in names
    assert "empty_plugin" in names
    assert "code_quality" in names

    broken = registry.get("import_failure")
    assert broken.valid is False
    assert broken.errors

    empty = registry.get("empty_plugin")
    assert empty.valid is False
    assert any("No plugin class" in error for error in empty.errors)

    assert registry.get("fixture_valid").valid is True
    assert registry.get("code_quality").valid is True


def test_get_unknown_plugin_raises(registry: PluginRegistry) -> None:
    with pytest.raises(NotFoundError):
        registry.get("does_not_exist")


def test_discovery_ignores_symlink_escape(
    tmp_path: Path, settings: PlatformSettings
) -> None:
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    shutil.copyfile(_fixture("valid_scorer.py"), plugins_dir / "valid_scorer.py")
    (plugins_dir / "escape_scorer.py").symlink_to(_fixture("valid_scorer.py"))

    registry = PluginRegistry([plugins_dir], settings=settings)
    copied = registry.get("fixture_valid")
    assert copied.valid is True
    escaped = registry.get("escape_scorer")
    assert escaped.valid is False
    assert any("escapes" in error for error in escaped.errors)


def test_discovery_includes_workspace_plugins_dir(
    tmp_path: Path, settings: PlatformSettings
) -> None:
    workspace_plugins = Path(settings.workspace) / "plugins"
    workspace_plugins.mkdir(parents=True)
    shutil.copyfile(
        _fixture("valid_scorer.py"), workspace_plugins / "workspace_scorer.py"
    )
    registry = PluginRegistry(None, settings=settings)
    assert registry.get("fixture_valid").valid is True


def test_load_returns_plugin_instance(registry: PluginRegistry) -> None:
    instance = registry.load("fixture_valid")
    assert isinstance(instance, Scorer)
    assert instance.score({}) == 0.5


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


def test_isolated_valid_plugin_returns_result() -> None:
    result = run_isolated(
        str(_fixture("valid_scorer.py")),
        "ValidScorer",
        "score",
        {"text": "hello"},
        timeout_seconds=10,
        max_output_bytes=65536,
    )
    assert result["ok"] is True
    assert result["result"] == 0.5
    assert result["isolation"]["mode"] == "subprocess"


def test_isolated_timeout_kills_runaway_plugin() -> None:
    started = time.monotonic()
    with pytest.raises(PluginTimeoutError) as excinfo:
        run_isolated(
            str(_fixture("looping_scorer.py")),
            "LoopingScorer",
            "score",
            {"text": "x"},
            timeout_seconds=1,
            max_output_bytes=65536,
        )
    elapsed = time.monotonic() - started
    assert elapsed < 10
    assert excinfo.value.code == "plugin_timeout"


def test_isolated_crash_is_reported_not_raised_in_host() -> None:
    with pytest.raises(PluginError) as excinfo:
        run_isolated(
            str(_fixture("crashing_scorer.py")),
            "CrashingScorer",
            "score",
            {"text": "x"},
            timeout_seconds=10,
            max_output_bytes=65536,
        )
    assert "boom" in str(excinfo.value)
    assert excinfo.value.details["code"] == "plugin_crash"


def test_isolated_output_cap_rejects_flood() -> None:
    with pytest.raises(PluginError) as excinfo:
        run_isolated(
            str(_fixture("noisy_scorer.py")),
            "NoisyScorer",
            "score",
            {"text": "x"},
            timeout_seconds=10,
            max_output_bytes=4096,
        )
    assert excinfo.value.details["code"] == "plugin_output_limit"


def test_network_isolation_is_reported_honestly() -> None:
    detection = detect_network_isolation()
    assert isinstance(detection["available"], bool)
    assert detection["reason"]

    result = run_isolated(
        str(_fixture("valid_scorer.py")),
        "ValidScorer",
        "score",
        {"text": "hello"},
        timeout_seconds=10,
        max_output_bytes=65536,
        allow_network=False,
    )
    isolation = result["isolation"]
    assert isolation["network_isolated"] is detection["available"]
    if not detection["available"]:
        assert (
            "not permitted" in isolation["unshare_reason"]
            or "not found" in isolation["unshare_reason"]
        )


def test_sandbox_env_excludes_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-secret-value")
    monkeypatch.setenv("HF_TOKEN", "hf_faketokenvalue")
    env = sandbox_env()
    assert "OPENAI_API_KEY" not in env
    assert "HF_TOKEN" not in env
    assert "PATH" in env


def test_non_json_payload_rejected() -> None:
    with pytest.raises(PluginError):
        run_isolated(
            str(_fixture("valid_scorer.py")),
            "ValidScorer",
            "score",
            {"bad": object()},
            timeout_seconds=5,
        )


def test_registry_run_builtin_isolated(registry: PluginRegistry) -> None:
    result = registry.run(
        "code_quality",
        "score",
        {"code": "def f():\n    '''doc'''\n    return 1\n"},
    )
    assert result["ok"] is True
    assert 0.0 <= float(result["result"]) <= 1.0
    assert result["isolation"]["mode"] == "subprocess"


def test_registry_run_in_process_requires_explicit_opt_in(
    registry: PluginRegistry,
) -> None:
    with pytest.raises(PluginError, match="opt-in"):
        registry.run("fixture_valid", "score", {}, isolation=False)
    result = registry.run(
        "fixture_valid", "score", {}, isolation=False, allow_in_process=True
    )
    assert result["result"] == 0.5
    assert result["isolation"]["mode"] == "in-process"


def test_registry_rejects_wrong_method(registry: PluginRegistry) -> None:
    with pytest.raises(PluginError, match="only exposes"):
        registry.run("code_quality", "evaluate", {})


def test_registry_dispatches_no_argument_methods(registry: PluginRegistry) -> None:
    hardware = registry.run("fixture_hardware", "detect", {})
    assert hardware["result"]["backend"] == "fixture"
    integration = registry.run("fixture_integration", "probe", {})
    assert integration["result"]["ok"] is True


# ---------------------------------------------------------------------------
# Builtin scorers
# ---------------------------------------------------------------------------


def _score_in_range(value: float) -> None:
    assert isinstance(value, float)
    assert 0.0 <= value <= 1.0


def test_code_quality_orders_good_above_bad() -> None:
    scorer = CodeQualityScorer()
    good = (
        "def add(a, b):\n"
        '    """Add two integers safely."""\n'
        "    # Guard against unsupported types\n"
        "    try:\n"
        "        return a + b\n"
        "    except TypeError:\n"
        "        return 0\n"
    )
    bad = "eval(input('> '))\n"
    good_score = scorer.score({"code": good})
    bad_score = scorer.score({"code": bad})
    _score_in_range(good_score)
    _score_in_range(bad_score)
    assert good_score > bad_score
    assert scorer.analyze({"code": good})["breakdown"]


def test_reasoning_orders_consistent_above_unsupported_certainty() -> None:
    scorer = ReasoningScorer()
    good = (
        "Step 1: 2 + 2 = 4. Therefore, because the inputs are valid, "
        "the result likely holds. Finally, 3 * 3 = 9."
    )
    bad = "2 + 2 = 5. This is definitely always guaranteed and proven."
    good_score = scorer.score({"text": good})
    bad_score = scorer.score({"text": bad})
    _score_in_range(good_score)
    _score_in_range(bad_score)
    assert good_score > bad_score


def test_spanish_language_scores_spanish_above_english() -> None:
    scorer = SpanishLanguageScorer()
    spanish = (
        "El rápido zorro marrón salta sobre el perro perezoso y come una manzana "
        "mientras el sol brilla en la mañana."
    )
    english = (
        "The quick brown fox jumps over the lazy dog and eats an apple while the "
        "sun shines in the morning."
    )
    spanish_score = scorer.score({"text": spanish})
    english_score = scorer.score({"text": english})
    _score_in_range(spanish_score)
    _score_in_range(english_score)
    assert spanish_score > english_score


def test_hallucination_prefers_hedged_text() -> None:
    scorer = HallucinationScorer()
    fabricated = (
        "According to https://fake-source.io/report, 87.4% of users fail. "
        "(Smith, 2099) [1]."
    )
    hedged = (
        "I may not be certain, but this likely result is approximately right; "
        "I am not sure about the exact numbers."
    )
    fabricated_score = scorer.score({"text": fabricated})
    hedged_score = scorer.score({"text": hedged})
    _score_in_range(fabricated_score)
    _score_in_range(hedged_score)
    assert hedged_score > fabricated_score


def test_model_integrity_verifies_and_detects_tamper(tmp_path: Path) -> None:
    root = tmp_path / "model"
    root.mkdir()
    weight = root / "weights.bin"
    weight.write_bytes(b"model-weights")
    digest = hashlib.sha256(weight.read_bytes()).hexdigest()
    manifest = {
        "files": {
            "weights.bin": {"sha256": digest, "size_bytes": weight.stat().st_size}
        }
    }

    scorer = ModelIntegrityScorer()
    assert scorer.score({"manifest": manifest, "root": str(root)}) == 1.0
    weight.write_bytes(b"tampered")
    assert scorer.score({"manifest": manifest, "root": str(root)}) == 0.0


def test_model_integrity_missing_manifest_fails_clearly(tmp_path: Path) -> None:
    scorer = ModelIntegrityScorer()
    with pytest.raises(PluginError, match="manifest"):
        scorer.score({"root": str(tmp_path)})


def test_behavioral_regression_detects_regressions() -> None:
    scorer = BehavioralRegressionScorer()
    baseline = {
        "results": [
            {"item_id": "a", "passed": True},
            {"item_id": "b", "passed": True},
        ]
    }
    candidate = {
        "results": [
            {"item_id": "a", "passed": True},
            {"item_id": "b", "passed": False},
        ]
    }
    analysis = scorer.analyze({"baseline": baseline, "candidate": candidate})
    assert analysis["score"] < 0.5
    assert analysis["pass_rate_delta"] == pytest.approx(-0.5)
    assert analysis["regressed_items"] == ["b"]


def test_defensive_scorer_prefers_defensive_content() -> None:
    scorer = DefensiveCybersecurityScorer()
    defensive = (
        "Detect the vulnerability, apply the patch, monitor alerts, use input "
        "validation and least privilege, and follow the incident response playbook."
    )
    offensive = "Here is a reverse shell payload and a keylogger for a botnet."
    defensive_score = scorer.score({"text": defensive})
    offensive_score = scorer.score({"text": offensive})
    _score_in_range(defensive_score)
    _score_in_range(offensive_score)
    assert defensive_score > offensive_score
