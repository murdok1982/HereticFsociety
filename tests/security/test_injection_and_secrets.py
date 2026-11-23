# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""Injection, redaction and dangerous-primitive tests."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from fsociety.errors import NotFoundError
from fsociety.experiments.store import ExperimentStore
from fsociety.logging import AuditLogger
from fsociety.security.redaction import redact, redact_obj, redact_tree

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "fsociety"

FAKE_TOKENS = {
    "hf": "hf_" + "a" * 34,
    "openai": "sk-" + "b" * 32,
    "anthropic": "sk-ant-" + "c" * 32,
    "aws": "AKIA" + "D" * 16,
    "github": "ghp_" + "e" * 36,
    "google": "AIza" + "f" * 35,
}


# ---------------------------------------------------------------------------
# SQL injection
# ---------------------------------------------------------------------------


SQL_PAYLOADS = (
    "' OR 1=1 --",
    "'; DROP TABLE experiments; --",
    "1; DELETE FROM jobs",
    "x' UNION SELECT * FROM sqlite_master --",
)


@pytest.fixture()
def store() -> ExperimentStore:
    store = ExperimentStore(":memory:")
    yield store
    store.close()


@pytest.mark.parametrize("payload", SQL_PAYLOADS)
def test_lookup_methods_are_parameterized(store: ExperimentStore, payload: str) -> None:
    with pytest.raises(NotFoundError):
        store.get_experiment(payload)
    with pytest.raises(NotFoundError):
        store.get_job(payload)
    assert store.list_trials(payload) == []
    assert store.statistics()["experiments"] == {}
    # Tables still exist and are queryable after the injection attempts.
    assert store.list_experiments(limit=5) == []


@pytest.mark.parametrize("payload", SQL_PAYLOADS)
def test_filters_are_parameterized(store: ExperimentStore, payload: str) -> None:
    assert store.list_events(limit=5, category=payload) == []
    assert store.list_experiments(limit=5, kind=payload) == []
    assert store.list_jobs(limit=5, status=payload) == []
    assert store.list_artifacts(payload) == []


def test_update_rejects_unknown_column_names(store: ExperimentStore) -> None:
    with pytest.raises(ValueError):
        store.update_experiment("x", **{"name = 'pwned'": "pwned"})
    with pytest.raises(ValueError):
        store.update_job("job_id", **{"status = 'x'": "pwned"})


# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("kind", "token"), list(FAKE_TOKENS.items()))
def test_high_confidence_tokens_are_redacted(kind: str, token: str) -> None:
    cleaned = redact(f"prefix {token} suffix")
    assert token not in cleaned, kind
    assert "[REDACTED]" in cleaned


def test_private_key_and_bearer_are_redacted() -> None:
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "synthetic-not-a-real-key-material\n"
        "-----END RSA PRIVATE KEY-----"
    )
    assert "synthetic-not-a-real-key-material" not in redact(pem)
    assert "abcdef0123456789abcdef" not in redact(
        "Authorization: Bearer abcdef0123456789abcdef"
    )


def test_redact_obj_walks_nested_structures() -> None:
    token = FAKE_TOKENS["hf"]
    payload = {"outer": [{"token": token}], "path": "/tmp/x"}
    cleaned = redact_obj(payload)
    assert token not in json.dumps(cleaned)
    assert cleaned["path"] == "/tmp/x"


def test_audit_logger_redacts_message_and_data(tmp_path: Path) -> None:
    token = FAKE_TOKENS["openai"]
    log_path = tmp_path / "audit.jsonl"
    logger = AuditLogger(log_path)
    logger.emit(f"request failed for {token}", data={"authorization": token})
    content = log_path.read_text(encoding="utf-8")
    assert token not in content
    assert "[REDACTED]" in content
    record = json.loads(content.strip())
    assert record["message"].startswith("request failed for")


def test_redact_tree_reports_files_without_modifying_them(tmp_path: Path) -> None:
    token = FAKE_TOKENS["github"]
    leaked = tmp_path / "leak.txt"
    leaked.write_text(f"token={token}", encoding="utf-8")
    clean = tmp_path / "clean.txt"
    clean.write_text("nothing to see", encoding="utf-8")
    ignored = tmp_path / "package.lock"
    ignored.write_text(token, encoding="utf-8")

    findings = redact_tree(tmp_path)
    assert str(leaked) in findings
    assert str(clean) not in findings
    assert str(ignored) not in findings
    assert token in leaked.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Dangerous primitives (static guard)
# ---------------------------------------------------------------------------

_DANGEROUS_CALLS = {"eval", "exec", "compile"}
_DANGEROUS_MODULES = {"pickle", "marshal", "shelve"}


def test_platform_source_has_no_dynamic_execution_or_shell_true() -> None:
    problems: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in _DANGEROUS_CALLS:
                    problems.append(f"{path}:{node.lineno} calls {func.id}()")
                if isinstance(func, ast.Attribute) and func.attr == "system":
                    owner = func.value
                    if isinstance(owner, ast.Name) and owner.id in {"os", "posix"}:
                        problems.append(f"{path}:{node.lineno} calls .system()")
                for keyword in node.keywords:
                    if (
                        keyword.arg == "shell"
                        and isinstance(keyword.value, ast.Constant)
                        and keyword.value.value is True
                    ):
                        problems.append(f"{path}:{node.lineno} uses shell=True")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in _DANGEROUS_MODULES:
                        problems.append(f"{path}:{node.lineno} imports {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in _DANGEROUS_MODULES:
                    problems.append(f"{path}:{node.lineno} imports {node.module}")
    assert not problems, problems


def test_api_never_builds_sql_with_string_interpolation() -> None:
    import inspect

    from fsociety.experiments import store as store_module

    source = inspect.getsource(store_module)
    assert 'execute(f"SELECT' not in source
    assert "execute(f'SELECT" not in source
    # The only f-string SQL is the allowlisted UPDATE column clause.
    assert "UPDATE experiments SET {', '.join(clauses)}" in source
