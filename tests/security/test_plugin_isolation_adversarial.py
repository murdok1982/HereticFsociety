# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Adversarial plugin-isolation tests.

Proves the controls that exist (secret-free environment, temporary cwd, network
namespace, timeout, output cap, crash containment) and records the one that does
not: discovery imports a workspace plugin in the host process before any sandbox
is created (VULN-001).
"""

from __future__ import annotations

import json
import shutil
import socket
import time
from pathlib import Path

import pytest

from fsociety.config import PlatformSettings
from fsociety.errors import PluginError, PluginTimeoutError
from fsociety.plugins.isolation import detect_network_isolation, run_isolated
from fsociety.plugins.registry import PluginRegistry

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "plugins"


def _fixture(name: str) -> Path:
    return FIXTURES / name


def test_isolated_worker_does_not_inherit_host_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-synthetic-not-a-real-key-000")
    monkeypatch.setenv("FSOCIETY_API_KEY", "synthetic-platform-key-000")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "synthetic-aws-secret-000")

    result = run_isolated(
        str(_fixture("environment_probe.py")),
        "EnvironmentScorer",
        "score",
        {
            "names": [
                "OPENAI_API_KEY",
                "FSOCIETY_API_KEY",
                "AWS_SECRET_ACCESS_KEY",
                "HF_TOKEN",
            ]
        },
        timeout_seconds=15,
        max_output_bytes=65536,
    )
    payload = result["result"]
    for name in (
        "OPENAI_API_KEY",
        "FSOCIETY_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "HF_TOKEN",
    ):
        assert name not in payload["keys"]
        assert payload["selected"][name] is None
    assert "PATH" in payload["keys"]


def test_explicit_worker_env_is_still_filtered() -> None:
    result = run_isolated(
        str(_fixture("environment_probe.py")),
        "EnvironmentScorer",
        "score",
        {"names": ["OPENAI_API_KEY", "HF_TOKEN"]},
        timeout_seconds=15,
        max_output_bytes=65536,
        env={
            "PATH": "/usr/bin:/bin",
            "OPENAI_API_KEY": "sk-synthetic-not-a-real-key-000",
            "HF_TOKEN": "hf_syntheticnotarealtoken000000000000",
        },
    )
    assert "OPENAI_API_KEY" not in result["result"]["keys"]
    assert result["result"]["selected"]["OPENAI_API_KEY"] is None


def test_isolated_worker_uses_a_temporary_working_directory(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = run_isolated(
        str(_fixture("environment_probe.py")),
        "EnvironmentScorer",
        "score",
        {"names": []},
        timeout_seconds=15,
        max_output_bytes=65536,
        cwd=None,
    )
    child_cwd = Path(result["result"]["cwd"])
    assert result["isolation"]["cwd"] == "temporary-directory"
    assert workspace.resolve() not in child_cwd.parents
    assert child_cwd != Path.cwd()
    assert not child_cwd.exists(), "temporary isolation directory must be removed"


def _local_listener() -> tuple[socket.socket, int]:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    return server, int(server.getsockname()[1])


def test_network_namespace_blocks_a_host_listener() -> None:
    server, port = _local_listener()
    try:
        isolated = run_isolated(
            str(_fixture("network_probe.py")),
            "NetworkProbeScorer",
            "score",
            {"port": port},
            timeout_seconds=15,
            max_output_bytes=65536,
            allow_network=False,
        )
        if isolated["isolation"]["network_isolated"]:
            assert isolated["result"] is not True, isolated["result"]
            assert "Network is unreachable" in str(isolated["result"])
        else:
            # Documented limitation: no network namespace on this host.
            assert isolated["result"] is True

        allowed = run_isolated(
            str(_fixture("network_probe.py")),
            "NetworkProbeScorer",
            "score",
            {"port": port},
            timeout_seconds=15,
            max_output_bytes=65536,
            allow_network=True,
        )
        assert allowed["isolation"]["network_isolated"] is False
        assert allowed["result"] is True
    finally:
        server.close()


def test_crash_and_timeout_do_not_take_down_the_host(tmp_path: Path) -> None:
    settings = PlatformSettings(
        workspace=tmp_path / "workspace",
        plugin_timeout_seconds=30,
        plugin_max_output_bytes=65536,
    )
    registry = PluginRegistry([FIXTURES], settings=settings)

    with pytest.raises(PluginError) as crash:
        registry.run("fixture_crashing", "score", {})
    assert crash.value.details["code"] == "plugin_crash"

    started = time.monotonic()
    with pytest.raises(PluginTimeoutError):
        run_isolated(
            str(_fixture("looping_scorer.py")),
            "LoopingScorer",
            "score",
            {},
            timeout_seconds=1,
            max_output_bytes=65536,
        )
    assert time.monotonic() - started < 15

    healthy = registry.run("fixture_valid", "score", {})
    assert healthy["ok"] is True
    assert healthy["result"] == 0.5


def test_output_flood_is_rejected_not_truncated_into_a_result(tmp_path: Path) -> None:
    settings = PlatformSettings(
        workspace=tmp_path / "workspace",
        plugin_timeout_seconds=15,
        plugin_max_output_bytes=4096,
    )
    registry = PluginRegistry([FIXTURES], settings=settings)
    with pytest.raises(PluginError) as flood:
        registry.run("fixture_noisy", "score", {})
    assert flood.value.details["code"] == "plugin_output_limit"


def test_discovery_does_not_execute_plugin_code_in_the_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """VULN-001 regression: discovery imports plugins in the isolation worker.

    The hostile fixture writes two sentinels when its module is imported:

    * ``worker_marker`` is a literal absolute path, so the *worker* can write it.
    * ``host_marker`` is only written when ``FSOCIETY_TEST_IMPORT_MARKER`` is in
      the environment; the worker environment is allowlisted and never receives
      it, while the host test process does.

    After ``PluginRegistry(...)``: metadata must be discovered (worker imported
    the module) but the host marker must be absent (host did not import it).
    """
    host_marker = tmp_path / "host-imported.txt"
    worker_marker = tmp_path / "worker-imported.txt"
    monkeypatch.setenv("FSOCIETY_TEST_IMPORT_MARKER", str(host_marker))
    monkeypatch.setenv("FSOCIETY_API_KEY", "synthetic-host-only-key-000")

    plugins_dir = tmp_path / "workspace" / "plugins"
    plugins_dir.mkdir(parents=True)
    plugin = plugins_dir / "evil.py"
    plugin.write_text(
        "import os\n"
        "from pathlib import Path\n"
        f"Path({str(worker_marker)!r}).write_text('worker', encoding='utf-8')\n"
        "_host = os.environ.get('FSOCIETY_TEST_IMPORT_MARKER')\n"
        "if _host:\n"
        "    Path(_host).write_text('host', encoding='utf-8')\n"
        "from fsociety import PLUGIN_API_VERSION\n"
        "from fsociety.plugins.api import Scorer\n"
        "class EvilDiscoveryScorer(Scorer):\n"
        "    name = 'fixture_evil_discovery'\n"
        "    version = '1.0.0'\n"
        "    api_version = PLUGIN_API_VERSION\n"
        "    kind = 'scorer'\n"
        "    def score(self, item):\n"
        "        return 0.0\n",
        encoding="utf-8",
    )

    registry = PluginRegistry(
        [plugins_dir], settings=PlatformSettings(workspace=tmp_path / "workspace")
    )

    info = registry.get("fixture_evil_discovery")
    assert info.valid, info.errors
    assert info.path == str(plugin.resolve())
    assert worker_marker.exists(), "the isolation worker did not import the plugin"
    assert not host_marker.exists(), "discovery imported the plugin in the host"


def test_explicit_host_load_executes_plugin_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Residual risk: ``load()``/in-process mode imports in the host.

    Default API/CLI execution is isolated; only an explicit
    ``isolation=False, allow_in_process=True`` call reaches this path. The test
    documents the trust decision rather than a hidden discovery side effect.
    """
    marker = tmp_path / "host-loaded.txt"
    synthetic_key = "synthetic-host-key-for-load-000"
    monkeypatch.setenv("FSOCIETY_TEST_IMPORT_MARKER", str(marker))
    monkeypatch.setenv("FSOCIETY_API_KEY", synthetic_key)

    plugins_dir = tmp_path / "workspace" / "plugins"
    plugins_dir.mkdir(parents=True)
    shutil.copyfile(_fixture("import_side_effect.py"), plugins_dir / "local.py")

    registry = PluginRegistry(
        [plugins_dir], settings=PlatformSettings(workspace=tmp_path / "workspace")
    )
    assert not marker.exists(), "discovery must not import in the host"

    instance = registry.load("fixture_import_side_effect")
    assert instance.score({}) == 0.25
    assert marker.read_text(encoding="utf-8") == synthetic_key


def test_in_process_execution_requires_explicit_opt_in(tmp_path: Path) -> None:
    settings = PlatformSettings(workspace=tmp_path / "workspace")
    registry = PluginRegistry([FIXTURES], settings=settings)
    with pytest.raises(PluginError, match="opt-in"):
        registry.run("fixture_valid", "score", {}, isolation=False)
    in_process = registry.run(
        "fixture_valid", "score", {}, isolation=False, allow_in_process=True
    )
    assert in_process["isolation"]["mode"] == "in-process"


def test_network_isolation_probe_reports_its_reason() -> None:
    detection = detect_network_isolation()
    assert isinstance(detection["available"], bool)
    assert detection["reason"]
    assert json.dumps(detection)  # JSON serializable for API/CLI reporting
