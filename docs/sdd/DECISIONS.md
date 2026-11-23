# Architecture Decision Records

Significant decisions, alternatives considered, and consequences. Newest first.

---

## ADR-001 — Sidecar platform instead of rewriting Heretic

**Context:** The repository is upstream Heretic 2.2.0.dev0. The mission requires an independent platform while preserving the working engine.

**Decision:** Keep `src/heretic` untouched. Add `src/fsociety` as a sidecar that lazy-imports the engine. Preserve the `heretic` import path, console script, configuration keys, scorer/modifier contracts and reproducibility manifests.

**Alternatives:** (a) rename/refactor heretic into `fsociety.core.heretic_engine` — breaks every external plugin and manifest; (b) fork the algorithms — unmaintainable duplicate logic.

**Consequences:** Two top-level packages in one distribution. Engine features fail gracefully when torch is absent. Upstream changes can be merged with minimal conflict.

---

## ADR-002 — Analytical estimation, never fabricated measurements

**Context:** No GPU/torch exists on the validation host. The platform must still plan configurations.

**Decision:** Implement a documented analytical memory model (weights/KV/activations/overhead) with explicit assumptions and `kind="estimate"`. Hardware detection records provenance and confidence. Any performance or benchmark claim requires an executed measurement; absent hardware is reported as skipped.

**Consequences:** The planner cannot validate throughput. The dashboard labels estimates as estimates. Reports state conditions and refuse cross-condition comparisons.

---

## ADR-003 — Torch-free core and optional API dependency

**Context:** Operators may want model inventory, evaluation orchestration and the dashboard without installing multi-gigabyte ML wheels.

**Decision:** `fsociety` core imports no torch/transformers at module level; engine-dependent classes lazy-import inside functions and raise `EngineUnavailableError` with install instructions. FastAPI/uvicorn are an optional `[api]` extra. Base distribution dependencies remain the original Heretic set to avoid breaking existing installs.

**Consequences:** CI runs a light profile (no torch) and an engine profile. `pip install fsociety-ai` still installs the engine stack; `--no-deps` + `[api]` yields a small deployment.

---

## ADR-004 — SQLite for experiments and jobs

**Context:** A single-node platform needs durable history, cancellable jobs and zero infrastructure.

**Decision:** SQLite in WAL mode with parameterized SQL, a schema version and a process-wide lock. Not an ORM. JSON payload columns for flexible params/metrics.

**Alternatives:** JSON files (no concurrency/atomicity), Postgres (operational burden).

**Consequences:** Multi-process job execution and shared rate limiting are documented limitations; a future queue backend can implement the same `ExperimentStore` contract.

---

## ADR-005 — Optuna remains the optimization engine

**Context:** Heretic already depends on Optuna and uses TPE. Reimplementing the sampler would be wasted effort and a divergence risk.

**Decision:** Use Optuna TPE/RandomSampler with explicit seeds, persist every trial, and expose a declarative `SearchSpace`. The API only exposes the analytical resource-planning objective; arbitrary callables remain a Python API feature.

**Consequences:** Deterministic trial sequences are testable. The API cannot be abused to execute arbitrary scoring code.

---

## ADR-006 — Separate platform plugins from Heretic plugins

**Context:** Heretic's plugin system imports Python modules with full privileges and has no version negotiation. Extending it in place would risk engine compatibility.

**Decision:** Introduce `fsociety` plugins with `PLUGIN_API_VERSION=1`, explicit kinds, manifest metadata, permission allowlists, dependency validation and subprocess isolation. Leave Heretic scorers/modifiers untouched.

**Consequences:** Two plugin ecosystems coexist and are documented as distinct. Isolation is process-level, explicitly not a container boundary.

---

## ADR-007 — Package identity change with compatibility shims

**Context:** "FSOCIETY AI" needs its own identity; `pip install heretic-llm` upgrades must not silently break.

**Decision:** Distribution renamed `fsociety-ai`, version `3.0.0`, build backend hatchling (ships `heretic` + `fsociety`), console scripts `fsociety` **and** `heretic`. The migration guide instructs uninstalling `heretic-llm` before installing `fsociety-ai` and documents unchanged imports/CLI/config/plugins.

**Consequences:** Distribution metadata and build backend change; runtime compatibility stays. AGPL attribution is preserved in `LICENSE`, `README`, `pyproject.toml` and source headers.

---

## ADR-008 — Security headers and auth fail closed

**Decision:** Non-loopback requests are refused unless an API key is configured and supplied (constant-time comparison). Wildcard CORS is rejected at configuration time. Remote `trust_remote_code` is denied unless the exact repository is on an allowlist; `always` is possible but produces a HIGH finding. All logs, errors and reports are redacted.

**Consequences:** A remote deployment cannot start "open by accident". Operators must configure a key and explicit hosts/CORS. Local UX is unchanged.

---

## ADR-009 — Bundled suites are original smoke tests

**Context:** Copying MMLU/GSM8K/HumanEval datasets raises licensing and reproducibility questions and would imply comparability we cannot support.

**Decision:** Author small original suites per capability, with provenance text that says they are not the official benchmarks. Provide an optional `lm_eval` adapter for operators who install the harness, without fabricating results.

**Consequences:** Bundled scores are useful for relative smoke checks only; the UI and reports say so.

---

## ADR-010 — Frontend without a component or chart framework

**Decision:** React 19 + TypeScript + Vite with hand-built accessible primitives and SVG charts, local font packages, CSS-variable design tokens. No Tailwind/UI kit/chart library.

**Why:** The surface is small and data-dense; a full kit adds bundle weight and generic styling. Hand-built primitives enforce the design system and state coverage.

**Consequences:** More component code to maintain, but no framework lock-in; verified by typecheck and production build.

---

## ADR-011 — Historical distribution alias inside the engine

**Context:** Renaming the distribution to `fsociety-ai` broke `heretic` at runtime because
the engine looks up `importlib.metadata.version("heretic-llm")` for its banner, version info
and reproducibility metadata. This was reproduced with the installed wheel.

**Decision:** Add `src/heretic/_distribution.py`, a three-function shim that resolves
`heretic-llm` → `fsociety-ai` (and back) and preserve every existing manifest key. Replace
the three direct metadata lookups. This is the only behavioural change in the engine, is
covered by upstream tests plus an installed-wheel check, and does not alter algorithms,
configuration keys, plugin contracts or serialization.

**Alternatives:** keep the old distribution name (loses project identity), add a fake
`.dist-info` (fragile and dishonest), or require environment variables (breaks users).

**Consequences:** Old reproducibility manifests that pin `heretic-llm` versions still report
a mismatch, exactly as they would for any engine version change; reproduction continues to
require an identical environment.

---

## ADR-012 — CSP: inline styles and data-URI fonts

**Context:** The dashboard uses React inline styles for dynamic widths and bundles fonts as
data URIs under Vite's default asset handling. A strict `style-src 'self'` / `font-src
'self'` policy blocked both (verified in the browser console).

**Decision:** Allow `'unsafe-inline'` for styles and `data:` for images/fonts. Scripts remain
strictly `'self'`; `object-src 'none'`, `frame-ancestors 'none'`, `base-uri 'self'`. The
dashboard never renders untrusted HTML and has no `dangerouslySetInnerHTML`.

**Consequences:** Reduced CSP strength against style-based exfiltration, accepted for a
same-origin, no-user-content operator console. Any future user-generated HTML requires a
re-evaluation.
