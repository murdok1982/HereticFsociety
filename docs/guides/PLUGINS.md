# Plugin Development Guide

FSOCIETY AI plugins are **separate** from Heretic scorer/modifier plugins. Heretic
plugins keep their original contract and run inside the engine; platform plugins score
evaluation payloads, evaluate reports, export artifacts, provide hardware data or
integrate external systems.

## Contract

Every plugin derives from `fsociety.plugins.api.PluginBase` (usually through a concrete
ABC) and declares metadata as class attributes:

```python
from fsociety.plugins.api import Scorer


class LengthScorer(Scorer):
    name = "response_length"
    version = "1.0.0"
    description = "Penalizes very short or very long answers."
    author = "Example Author"
    license = "AGPL-3.0-or-later"
    dependencies = []
    permissions = []

    def score(self, item: dict) -> float:
        text = str(item.get("text", ""))
        words = len(text.split())
        if words == 0:
            return 0.0
        if words < 5:
            return 0.4
        if words > 400:
            return 0.6
        return 1.0
```

Kinds: `scorer`, `evaluator`, `hardware_provider`, `exporter`, `integration`.

* `Scorer.score(item) -> float` in `[0.0, 1.0]`.
* `Evaluator.evaluate(payload) -> dict`.
* `Exporter.export(payload, destination: Path) -> dict`.
* `HardwareProvider.detect() -> dict`.
* `Integration.probe() -> dict`.

Plugins are instantiated with no arguments. Keep constructors side-effect free and read
configuration from the payload, not from host settings.

## Discovery

1. Built-in plugins under `src/fsociety/plugins/`.
2. Python entry points in the group `fsociety.plugins`
   (`my-package = my_package.module:MyClass`).
3. `.py` files in `<workspace>/plugins/` with a class deriving from `PluginBase`.

`fsociety plugins list` shows valid and invalid plugins with reasons. One broken plugin
never prevents discovery of the others.

## Versioning and validation

* `api_version` must equal `FSOCIETY_PLUGIN_API_VERSION` (currently `1`). A mismatch is
  rejected, not silently accepted.
* `version` must be semver-like (`1.2.3`, optionally with a pre-release suffix).
* `dependencies` are PEP 508 names; importability is checked (a best-effort mapping from
  distribution to import name).
* `permissions` must be a subset of `network`, `filesystem_read`, `filesystem_write`,
  `subprocess`, `gpu`. Unknown permissions are rejected.
* Names cannot shadow platform modules (`os`, `sys`, `heretic`, `fsociety`, …).

Discovery and validation run in the isolation worker: the host enumerates `.py` files and
entry-point metadata, then a subprocess imports the module, inspects the class and returns
metadata. The host does not import workspace plugin modules during discovery, so a
malicious module-level payload does not execute in the API/CLI process.

`PluginRegistry.load()` (and `allow_in_process=True`) still import plugin code in the host
by definition; use them only for code you fully trust. Built-in plugins are trusted imports.

## Execution and isolation

By default, `run` executes in a subprocess (`fsociety.plugins.worker`) with:

* JSON payload on stdin, JSON result on stdout;
* environment allowlist (no cloud/API credentials);
* working directory in a temporary directory;
* a timeout (`plugin_timeout_seconds`);
* stdout/stderr size limits (`plugin_max_output_bytes`);
* a new session/process group killed on timeout;
* optional network namespace (`unshare -n`) when available and
  `plugin_allow_network=false` — the applied status is reported, never assumed.

This is process isolation with cooperative limits, **not** a container or VM sandbox. A
malicious plugin shares the host kernel and user. Do not run untrusted plugins on a host
with unrelated secrets.

```bash
fsociety plugins run code_quality --method score \
  --payload '{"text": "def f(x):\n    return x + 1"}'
```

`--in-process` is an explicit opt-in that removes the subprocess boundary. The API exposes
`allow_in_process` for the same purpose; do not enable it for untrusted code.

## Built-in example plugins

| Name | What it does |
| :--- | :--- |
| `code_quality` | AST parse, complexity, docstrings, error handling, dangerous constructs |
| `reasoning` | chain markers + arithmetic consistency checks |
| `spanish_language` | Spanish lexical/accent heuristic + required terms |
| `hallucination_risk` | fabricated citations/URLs/dates heuristics (higher = lower risk) |
| `model_integrity` | verifies a model manifest; score = verified ratio |
| `behavioral_regression` | pass-rate delta and per-item regressions between reports |
| `defensive_cybersecurity` | defensive-concept rubric (detection, mitigation, response) |

They are heuristics, documented as such. They are not safety classifiers and must not be
used as the sole basis for a deployment decision.

## Packaging

```toml
[project]
name = "my-fsociety-plugin"
dependencies = ["fsociety-ai>=3.0"]

[project.entry-points."fsociety.plugins"]
my_scorer = "my_package.scorer:MyScorer"
```

Install it into the same environment as the platform. Entry points appear after
`fsociety plugins reload` or an API restart.

## Testing

```python
from fsociety.plugins.api import validate_plugin_class
from my_package.scorer import MyScorer

info = validate_plugin_class(MyScorer)
assert info.valid, info.errors
assert 0.0 <= MyScorer().score({"text": "hello world"}) <= 1.0
```

Add an isolation test for anything that reads files or performs network calls. Tests must
not depend on a GPU.

## Compatibility with Heretic plugins

Heretic scorer plugins (`heretic.scorers.*`) are configured through Heretic's own config
and are not loaded by this registry. If you need a Heretic plugin, keep using
`scorers = [...]` in your Heretic TOML.
