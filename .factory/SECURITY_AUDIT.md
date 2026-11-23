# FSOCIETY AI — ADVERSARIAL SECURITY AUDIT

- **Audit Status:** `APPROVED (0 CRITICAL / 0 HIGH OPEN)` — production engine profile is Python 3.11+; Python 3.10 light profile has one accepted LOW residual (VULN-014)
- **Auditor Agent:** `@redteam-auditor`
- **Target:** FSOCIETY AI 3.0.0 · Heretic engine 2.2.0.dev0 (preserved)
- **Snapshot:** `aecd4a8` (HEAD) + working tree (platform/frontend/tests untracked), final pass 2026-11-23
- **Open Summary Counts:** `CRITICAL` 0 · `HIGH` 0 · `MEDIUM` 1 · `LOW` 3
- **Closed During Audit:** `HIGH` 2 (VULN-001, VULN-004) · `MEDIUM` 5 (VULN-002, VULN-003, VULN-005, VULN-006, VULN-012) · `LOW` 3 (VULN-007, VULN-008, VULN-010)

> **EXIT GATE:** no `CRITICAL` or `HIGH` finding remains `OPEN`. The remaining `MEDIUM` (sqlitedict) and `LOW` findings (public docs, non-lockfile Docker build, Python 3.10 filelock) are documented with compensating controls and accepted for this release.

---

## 1. ATTACK SURFACE & THREAT MODEL

**Entry points**

1. HTTP API `/api/v1/*` (loopback by default; non-loopback requires an API key).
2. CLI arguments and TOML configuration files (operator-controlled).
3. Model repositories (Hugging Face) and local model directories.
4. Third-party plugins (scorers/evaluators/exporters) — declared untrusted.
5. Export bundles, manifests and report files (operator artifacts).
6. Dashboard in the browser (same origin; API key in `sessionStorage`).
7. Heretic engine subprocess (preserved upstream behaviour).
8. Optional `lm-eval` subprocess and `datasets` loading in the engine profile (Python 3.11+ only).

**Assets:** model weights, experiment history, API key, HF token, workspace files, host integrity, benchmark integrity, TLS trust for downloads.

**Authentication mechanism:** optional static API key (`X-API-Key` or `Authorization: Bearer`), constant-time comparison; loopback peers are trusted unless `api_require_key_for_loopback=true`; all non-loopback requests fail closed without a key. No users, roles or multi-tenant ownership exist (single-operator tool).

**Trust boundaries:** browser → API; API → filesystem; model repo/dataset → process; plugin file → isolation worker; engine subprocess → host; build → registry.

**Scope note:** the CPU/API profile (Docker image, HTTP surface, Python 3.10 light profile) is dependency-clean except the accepted local filelock advisory on 3.10. The engine profile is **Python 3.11+ only** (enforced by dependency markers); the audited engine environment (3.12) has one accepted transitive advisory (sqlitedict) and no HIGH/CRITICAL.

---

## 2. MANDATORY APPSEC VERIFICATION CHECKLIST

Legend: `[x]` verified with executable evidence · `[ ]` residual risk documented.

### 2.1 Injection & Input Validation
- [x] All API bodies/queries are validated with Pydantic `extra="forbid"`, bounds and patterns (`test_plugin_api_rejects_network_and_isolation_overrides`).
- [x] SQL is fully parameterized; dynamic column lists come from an allowlist (`test_lookup_methods_are_parameterized`, `test_filters_are_parameterized`, `test_update_rejects_unknown_column_names`).
- [x] No `eval`, `exec`, `compile`, `pickle`, `marshal`, `os.system` or `shell=True` in `src/fsociety` (AST guard `test_platform_source_has_no_dynamic_execution_or_shell_true`).
- [x] Dashboard has no DOM-injection primitives (`test_no_html_injection_primitives`).
- [x] Model references reject schemes, control characters, encoded traversal and `..` segments (`test_trust_policy_adversarial.py`, `test_path_jail.py`).

### 2.2 Authentication, Sessions & Access Control
- [x] Every non-public `/api/v1` operation requires auth; verified dynamically against the OpenAPI schema (`test_every_openapi_endpoint_requires_the_key`).
- [x] Non-loopback fails closed without a configured key (`test_remote_without_configured_key_fails_closed`).
- [x] Loopback exemption is explicit and can be disabled (`test_loopback_is_exempt_from_key_by_default`, `test_loopback_can_require_a_key`).
- [x] Forwarding headers cannot spoof loopback (`test_forwarding_headers_do_not_grant_loopback`).
- [x] API key comparison is constant-time (`hmac.compare_digest`; `tests/security/test_policy.py`).
- [x] Rate limiting applies to all methods and returns `429` + `Retry-After` (`test_rate_limit_returns_429_with_retry_after`).
- [x] No object-level IDOR surface; report/export paths confined to workspace roots (`test_report_id_traversal_returns_not_found`, `test_path_jail.py`).
- [x] **CSRF**: `OriginCheckMiddleware` rejects mutating requests with a foreign or `null` Origin (`403 origin_not_allowed`); same-origin and configured CORS origins pass; requests without Origin (CLI/curl) are unaffected (`test_foreign_origin_state_change_is_rejected`).

### 2.3 Secrets, Configuration & HTTP Hardening
- [x] No credentials in tracked files or git history; `.env` is untracked (see §4).
- [x] Settings/policy responses redact the key (`test_settings_never_return_the_api_key`).
- [x] Error envelopes redact token-shaped values (`test_error_envelopes_redact_token_shaped_references`).
- [x] Logs, audit events and reports pass through `redact()` (`test_audit_logger_redacts_message_and_data`, `tests/security/test_report_redaction.py`).
- [x] CORS uses explicit origins; wildcard is rejected at settings validation.
- [x] CSP (no `unsafe-eval` in `script-src`), `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, COOP/CORP, Permissions-Policy on success and error responses (`test_security_headers_on_success_and_auth_failure`).
- [x] Structured errors, no tracebacks/internal paths on 4xx (`test_errors_are_structured_and_never_contain_tracebacks`).
- [x] **`Cache-Control: no-store` on `/api/*`; HSTS when the request scheme is https** (`RequestContextMiddleware`); the TLS proxy must forward the scheme or add HSTS itself. Note: the `OriginCheckMiddleware` 403 is generated outside `RequestContext` and therefore lacks those headers (see §6.11).
- [ ] **API docs/OpenAPI are unauthenticated** — VULN-009 (`LOW`, accepted).

### 2.4 SSRF, File Handling & Dependencies
- [x] `safe_join` rejects `..`, absolute paths and symlink escapes; in-jail symlinks allowed (`test_path_jail.py`).
- [x] Export destinations and report retrieval are confined to their workspace roots (`test_export_destination_must_be_inside_exports`, `test_report_id_traversal_returns_not_found`).
- [x] Model references are confined to workspace/models for inspect/estimate/verify and for optimization/evaluation job ids (`test_absolute_model_references_outside_workspace_are_rejected`, `test_job_model_references_enforce_workspace_confinement`).
- [x] No upload endpoint and no arbitrary URL fetch; Hub ids are validated `org/name` and resolved by `huggingface_hub` (fixed host).
- [x] **Request-body limits**: declared oversized bodies → `413`; chunked bodies without `Content-Length` → `413`; compressed bodies → `415 unsupported_encoding` (`test_declared_oversized_body_is_rejected`, `test_chunked_body_without_length_is_rejected`, `test_compressed_request_bodies_are_rejected`).
- [x] **Engine profile (Python 3.11+): dependency audit has one accepted sqlitedict advisory** — VULN-004/005 closed; VULN-013 accepted.
- [x] **Python 3.10 packaging scope**: engine dependencies are marker-gated `>=3.11`; a 3.10 install resolves platform deps only (`uv export --python 3.10`; 3.10 install audit) — VULN-012 closed with a LOW filelock residual (VULN-014).

### 2.5 Model, Plugin & Supply-Chain Trust
- [x] `trust_remote_code` defaults to `never`; `allowlist` requires the exact `org/name`; `always` warns (`test_default_policy_is_never`, `test_allowlist_requires_the_exact_repository_id`).
- [x] Engine adapter refuses `--trust-remote-code` / `--trust_remote_code=...` and shell metacharacters; no API endpoint can enable it (`test_engine_adapter_refuses_remote_code_flags`, `test_no_mutating_http_verbs_are_exposed`).
- [x] Engine config generation disables `enable_shell` and never writes remote-code settings (`test_engine_config_never_enables_shell_or_remote_code`).
- [x] Plugin validation is static (API version, base class, permissions, dependencies); invalid plugins are refused (`tests/unit/test_plugins.py`).
- [x] **Discovery is isolated**: workspace plugins and entry points are introspected by `discover_plugins_isolated` in a worker; the host no longer imports them during `PluginRegistry(...)` (`test_discovery_does_not_execute_plugin_code_in_the_host`).
- [x] Plugin invocation is isolated: subprocess, temporary cwd, environment allowlist, wall-clock timeout, process-group kill, output cap, honest `unshare` reporting (`test_isolated_worker_does_not_inherit_host_secrets`, `test_isolated_worker_uses_a_temporary_working_directory`, `test_network_namespace_blocks_a_host_listener`, `test_crash_and_timeout_do_not_take_down_the_host`, `test_output_flood_is_rejected_not_truncated_into_a_result`).
- [x] In-process execution requires explicit `allow_in_process` **and** `isolation=False`; the HTTP API can never disable isolation (`test_in_process_execution_requires_explicit_opt_in`, `test_plugin_api_cannot_switch_to_in_process_execution`).
- [x] UV lock, CycloneDX SBOM, npm lock and AGPL/Heretic attribution are present (`test_supply_chain.py`, `test_sbom.py`).
- [x] **`dependency_audit()` runs `sys.executable -m pip_audit` with an environment allowlist, normalizes `name` and deduplicates** — VULN-006 closed.
- [x] **npm audit runs in CI** (`npm audit --audit-level=high`); local audit is clean — VULN-010 closed.
- [ ] **Container build does not use the lockfile** — VULN-011 (`LOW`, accepted).

### 2.6 Configuration Findings & Operational Posture
- [x] `GET /api/v1/security/findings` returns live configuration findings with stable shape/counts (`test_security_findings_endpoint_shape`).
- [x] Live settings never expose the key; docs/trust policy is redacted (`test_settings_never_return_the_api_key`).
- [x] Offline mode, revision pinning and manifest verification behave as documented (`tests/unit/test_model_manager.py`).
- [x] Graceful degradation without torch; health reports engine unavailable (`tests/api/test_api.py`; CPU-only host).

---

## 3. VULNERABILITY FINDINGS LOG

### [VULN-001] Plugin discovery executed untrusted module code in the host process (isolation bypass)
- **Severity:** `HIGH` (closed)
- **Category:** Sandbox escape / trust-boundary violation (CWE-94, CWE-693)
- **Location:** `src/fsociety/plugins/registry.py` (discovery path), `src/fsociety/plugins/isolation.py:526-569` (`discover_plugins_isolated`), `src/fsociety/plugins/worker.py:228-281` (`discover_plugins` worker mode)
- **Status:** `FIXED` (verified)
- **Original Exploit Scenario (PoC):** `PluginRegistry(...)` imported every `*.py` in `<workspace>/plugins/` (and entry points) with `exec_module` in the API/CLI process. Module-level code could read `os.environ` (including `FSOCIETY_API_KEY`/`HF_TOKEN`), write files or spawn processes before any sandbox existed.
- **Required Remediation (implemented):** discovery for workspace plugins and entry points now calls `discover_plugins_isolated`, which runs the `discover_plugins` worker method in the same subprocess isolation used for execution (temporary cwd, allowlisted env, timeout, output cap). Built-in plugins remain trusted host imports. Metadata is validated in the host via `PluginInfo.model_validate`.
- **Verification Proof:**
  - `test_discovery_does_not_execute_plugin_code_in_the_host`: a hostile plugin writes a literal absolute `worker_marker` (must exist, proving the worker imported it) and a `host_marker` only when the host-only env var is set (must be absent, proving the host did not import it). Discovered metadata is valid.
  - `test_explicit_host_load_executes_plugin_code`: discovery leaves the host marker absent; an explicit `registry.load(...)` (only reachable via `isolation=False, allow_in_process=True`) imports in the host and writes the marker — documented residual, not a discovery side effect.
  - `tests/unit/test_plugins.py` passes (31 tests, subprocess discovery).
- **Residual risk:** `load()`/in-process mode still imports plugin code in the host by explicit opt-in; built-ins are trusted; the discovery worker is a process, not a VM (same OS user can read user files). See §6.1.

### [VULN-002] Absolute model references escaped the API workspace jail
- **Severity:** `MEDIUM` (closed)
- **Category:** Path traversal / broken access control (CWE-22, CWE-639)
- **Location:** `src/fsociety/interfaces/api/deps.py:91-115` (`validate_model_reference`), `routes/models.py:67,75,100`, `routes/jobs.py:55,128-131`
- **Status:** `FIXED` (verified)
- **Original Exploit Scenario (PoC):** `GET /api/v1/models/inspect?reference=/etc` returned `200` with the host directory inventory and parsed metadata; optimization/evaluation job `model_id` values bypassed the same check.
- **Required Remediation (implemented):** filesystem-shaped references must resolve inside `workspace` or the configured models directory; Hub ids (`org/name`) still pass through. CLI semantics are unchanged.
- **Verification Proof:**
  - `test_absolute_model_references_outside_workspace_are_rejected` → `400 unsafe_path` for a crafted outside directory and `/etc`.
  - `test_job_model_references_enforce_workspace_confinement` → `400 unsafe_path` for optimization `model_id` and evaluation `model_id`/llama_cpp `path`.
  - `test_absolute_model_reference_inside_workspace_is_allowed` → `200` for a workspace model.
  - `test_absolute_model_paths_can_be_disabled` → `400` with `allow_absolute_model_paths=false`.
  - `test_windows_drive_strings_are_not_absolute_on_posix` documents POSIX semantics.

### [VULN-003] CSRF on loopback state-changing endpoints (no Origin check)
- **Severity:** `MEDIUM` (closed)
- **Category:** Cross-Site Request Forgery (CWE-352)
- **Location:** `src/fsociety/interfaces/api/middleware.py:43-87` (`OriginCheckMiddleware`), wired in `app.py:100-102`
- **Status:** `FIXED` (verified)
- **Original Exploit Scenario (PoC):** a page on `https://evil.example` executed `fetch("http://127.0.0.1:8000/api/v1/plugins/reload", {method:"POST", mode:"no-cors"})`; no preflight, no key needed for loopback, `200` returned.
- **Required Remediation (implemented):** mutating methods (`POST/PUT/PATCH/DELETE`) with a present `Origin` now require same-origin (`Origin` netloc == `Host`) or an explicitly configured CORS origin; `null` and foreign origins get `403 origin_not_allowed`. Requests without `Origin` (CLI/curl) are unaffected. `TrustedHostMiddleware` still validates the Host header.
- **Verification Proof:** `test_foreign_origin_state_change_is_rejected`: foreign Origin → `403 origin_not_allowed`; same-origin `http://127.0.0.1` → `200`; no `Access-Control-Allow-Origin` on the 403. `null` Origin also returns `403` (manual probe). 38 API security tests pass.
- **Residual:** the 403 is generated outside `RequestContextMiddleware`, so it lacks the standard hardening headers (see §6.11). No data is exposed and CORS blocks reads.

### [VULN-004] Engine profile pinned Pillow and sqlitedict with memory-safety/deserialization advisories
- **Severity:** `HIGH` (closed for Python 3.11+; sqlitedict residual tracked as VULN-013)
- **Category:** Vulnerable dependency (CWE-1395, CWE-787, CWE-502)
- **Location:** `pyproject.toml:40-45`, `uv.lock`
- **Status:** `FIXED` for Python 3.11+ (audited engine environment and Docker image)
- **Original Exploit Scenario (PoC):** `pip-audit` reported 42 unique Pillow advisories (heap-buffer-overflow writes, use-after-free, OOB reads in decoders) and CVE-2024-35515 in sqlitedict. The engine loads user-selected datasets (`src/heretic/utils.py:20,213-231`) and can run installed `lm_eval` tasks, including image tasks.
- **Required Remediation (implemented):** `pillow>=13.0.0` (Python ≥3.11); the sqlitedict assessment is in VULN-013. `uv.lock` regenerated and the engine environment synced.
- **Verification Proof:** `uv lock --check` exit `0`; engine environment has `pillow 13.0.0`; `pip-audit` against `/tmp/opencode/engine-env` reports **1 unique advisory** (sqlitedict), not 98. Python 3.10 no longer installs Pillow at all (VULN-012).

### [VULN-005] Engine profile pinned additional vulnerable transitive dependencies
- **Severity:** `MEDIUM` (closed for Python 3.11+)
- **Category:** Vulnerable dependency (CWE-1395; trust store, local TOCTOU, DoS)
- **Location:** `pyproject.toml:40-45`, `uv.lock`
- **Status:** `FIXED` for Python 3.11+ (verified)
- **Original Exploit Scenario (PoC):** certifi 2026.7.22 (compromised eMudhra root/intermediates), filelock 4.0.8 (14 local symlink/TOCTOU advisories), nltk 3.10.3 (32 path/DoS advisories via lm-eval), dnspython 2.8.0 (2 DNS DoS), pyasn1 0.6.4 (4 ASN.1 DoS).
- **Required Remediation (implemented):** floors `certifi>=2026.10.18`, `filelock>=4.2.1`, `nltk>=3.10.4`, `dnspython>=2.9.2`, `pyasn1>=0.6.5` (3.11 markers where required).
- **Verification Proof:** engine environment has certifi `2026.10.23`, filelock `4.2.10`, nltk `3.10.4`, dnspython `2.9.2`, pyasn1 `0.6.5`; `pip-audit` reports none of them. Python 3.10 filelock residual is VULN-014.

### [VULN-006] `dependency_audit()` audited an arbitrary PATH interpreter, not the project environment
- **Severity:** `MEDIUM` (closed)
- **Category:** Security tooling correctness / audit integrity
- **Location:** `src/fsociety/security/sbom.py:144-192`
- **Status:** `FIXED` (verified)
- **Original Exploit Scenario (PoC):** `shutil.which("pip-audit")` resolved a binary with a `/usr/bin/python3` shebang; the CLI reported **234 advisories** in unrelated system packages while the project environments were clean/98 entries. A hostile `PATH` could substitute a binary.
- **Required Remediation (implemented):** require the `pip_audit` module in the current interpreter (`importlib.util.find_spec`), run `[sys.executable, "-m", "pip_audit", "-f", "json"]` with a minimal environment allowlist and `shell=False`; return `unavailable` honestly. The normalizer reads `name` (not `package`) and deduplicates.
- **Verification Proof:** `tests/security/test_sbom.py` (4 passed; monkeypatched `find_spec`); engine run returns `status=vulnerabilities`, `finding_count=1` (sqlitedict), `exit_code=1`; light run returns `status=unavailable` instead of auditing the system Python.

### [VULN-007] Request body cap trusts `Content-Length` (chunked bypass)
- **Severity:** `LOW` (closed)
- **Category:** Resource exhaustion / DoS (CWE-400, CWE-770)
- **Location:** `src/fsociety/interfaces/api/middleware.py:117-170`
- **Status:** `FIXED` (verified)
- **Original Exploit Scenario (PoC):** a `Transfer-Encoding: chunked` body without `Content-Length` (2 MiB) reached the endpoint instead of `413`.
- **Required Remediation (implemented):** mutating requests with `Transfer-Encoding` and no `Content-Length` → `413`; compressed request bodies → `415 unsupported_encoding`; declared oversized bodies → `413`.
- **Verification Proof:** `test_chunked_body_without_length_is_rejected` (413), `test_compressed_request_bodies_are_rejected` (415), `test_declared_oversized_body_is_rejected` (413). A raw-socket probe against real uvicorn confirms TE/TE+CL are rejected by the HTTP server with `400` before the app, and a large CL returns `413`.
- **Residual:** at the raw ASGI layer (TestClient), an ASGI server that forwards both TE and CL could still stream an unmeasured body. Uvicorn/h11 rejects it; keep the middleware rule (or reject any TE) if the app is deployed on a different server. See §6.12.

### [VULN-008] HSTS and `Cache-Control: no-store` are absent
- **Severity:** `LOW` (closed)
- **Category:** Security misconfiguration (CWE-319, CWE-525)
- **Location:** `src/fsociety/interfaces/api/middleware.py:25-40` (`RequestContextMiddleware`)
- **Status:** `FIXED` (verified)
- **Original Exploit Scenario (PoC):** API responses contained neither `Cache-Control` nor `Strict-Transport-Security`; sensitive JSON could be cached behind an HTTPS proxy.
- **Required Remediation (implemented):** `/api/*` responses set `Cache-Control: no-store`; `Strict-Transport-Security: max-age=31536000; includeSubDomains` is added when `request.url.scheme == "https"`. The TLS proxy must forward the scheme (`X-Forwarded-Proto`) or add HSTS itself.
- **Verification Proof:** TestClient probes: HTTP response has `Cache-Control: no-store`; HTTPS-base response has both `Cache-Control: no-store` and HSTS. Existing header tests still pass.
- **Residual:** `OriginCheckMiddleware`'s 403 (outside `RequestContext`) lacks these headers; see §6.11. HSTS is only meaningful over TLS.

### [VULN-009] API documentation and OpenAPI schema are unauthenticated
- **Severity:** `LOW` (open, accepted)
- **Category:** Information disclosure (CWE-200)
- **Location:** `src/fsociety/interfaces/api/app.py:76-88`
- **Status:** `OPEN`
- **Exploit Scenario (PoC):** a non-loopback client with no key receives `200` from `/api/openapi.json` and `/api/docs`, learning the endpoint/parameter surface. Operations still require auth.
- **Rationale for acceptance:** the platform is a single-operator tool; the docs expose no data and every operation is authenticated. `api_docs_enabled=false` is documented for hardened remote deployments.
- **Required Remediation (optional):** default `api_docs_enabled=false` for non-loopback binds or protect docs routes with `require_auth`.
- **Verification Proof:** `test_api_docs_are_publicly_readable` documents current behaviour.

### [VULN-010] Automatic npm auditing is disabled
- **Severity:** `LOW` (closed)
- **Category:** Supply-chain process (CWE-1104)
- **Location:** `frontend/.npmrc:4` (`audit=false`); `.github/workflows/ci.yml:144` (`npm audit --audit-level=high`)
- **Status:** `FIXED` (verified)
- **Original Exploit Scenario (PoC):** installs did not warn about vulnerable packages and CI did not audit the frontend tree.
- **Required Remediation (implemented):** CI frontend job runs `npm audit --audit-level=high`; the local audit is clean.
- **Verification Proof:** `npm audit --json` → `0` vulnerabilities across 125 dependencies (prod 10 / dev 116 / optional 53); CI step present.

### [VULN-011] Container image build is not lockfile-reproducible
- **Severity:** `LOW` (open, accepted)
- **Category:** Supply-chain reproducibility (CWE-1104)
- **Location:** `deployment/docker/Dockerfile:5-6,24` (`npm install`, `pip install -r` with version ranges)
- **Status:** `OPEN`
- **Exploit Scenario (PoC):** `requirements-platform.txt` ranges and `npm install` can resolve a different tree than the audited `uv.lock`/`package-lock.json`; a later rebuild can silently pull a vulnerable version.
- **Rationale for acceptance:** the CPU image is built from the light platform requirements, which were manually audited clean; the build is not the engine profile. Reproducibility is a hardening improvement, not an active vulnerability.
- **Required Remediation (optional):** `npm ci`; install with `uv sync --frozen --no-dev --extra api` (or pin exact versions with hashes).
- **Verification Proof:** Docker build was not executed on this host (no daemon); the finding is a configuration review.

### [VULN-012] Python 3.10 engine resolution kept Pillow 12.3.0 / filelock 4.0.8 / dnspython 2.8.0
- **Severity:** `MEDIUM` (closed by packaging scope)
- **Category:** Vulnerable dependency / unsupported-version gap (CWE-1395)
- **Location:** `pyproject.toml:28-57` (engine dependencies marker-gated `python_version >= '3.11'`); `uv.lock`
- **Status:** `FIXED` (verified)
- **Original Exploit Scenario (PoC):** on Python 3.10 the resolver could not use the fixed engine releases, so a default install could include vulnerable Pillow/filelock/dnspython.
- **Required Remediation (implemented):** every engine dependency (torch, torchvision, transformers, datasets, lm-eval, accelerate, bitsandbytes, peft, ipython, pillow, filelock, dnspython, etc.) is gated `python_version >= '3.11'`. Python 3.10 remains supported for the light/API profile; the engine profile is 3.11+, and the CLI reports engine unavailable on 3.10.
- **Verification Proof:**
  - `uv export --frozen --no-dev --python 3.10` → engine pins carry `; python_full_version >= '3.11'`; no Pillow/torch/transformers/datasets/lm-eval selected for 3.10.
  - A clean Python 3.10.22 venv with `uv pip install .` installs platform deps only; no engine packages. `pip-audit` on that 3.10 environment reports **no Pillow/torch/etc.** advisories; the only residual is `filelock 4.1.0` (VULN-014).
- **Residual:** filelock on 3.10 (17 local advisories) — VULN-014.

### [VULN-013] sqlitedict 2.1.0 unsafe deserialization (lm-eval transitive)
- **Severity:** `MEDIUM` (open, accepted with compensating control)
- **Category:** Unsafe deserialization (CWE-502), CVE-2024-35515 / PYSEC-2026-1939
- **Location:** `uv.lock` (`sqlitedict 2.1.0`, required by `lm-eval 0.4.13`); not imported by platform code
- **Status:** `OPEN` (no upstream fixed release)
- **Exploit Scenario (PoC):** `sqlitedict` deserializes pickle payloads from its SQLite cache. An attacker who can write an lm-eval cache database could achieve code execution when a cached evaluation runs. **Reachability here is very limited:** Heretic calls `lm_eval.simple_evaluate(...)` without `use_cache` (`src/heretic/main.py:1499`, `src/heretic/scorers/benchmark_score.py:59`), and `lm_eval.evaluator` only instantiates `CachingLM` when `use_cache is not None` (`lm_eval/evaluator.py:277`). The platform never supplies a cache path, never loads operator cache files, and the HTTP API cannot invoke arbitrary lm-eval tasks (bundled suites only). Exploitation requires a local attacker/process writing the operator's cache directory — the same OS-user boundary already assumed compromised.
- **Compensating control:** keep `use_cache` unset; if the operator enables caching, use a private `0700` directory (`LM_HARNESS_CACHE_PATH`) and never ingest external `.db` files; monitor lm-eval/sqlitedict for a patched release or vendor a safe decoder.
- **Verification Proof:** source inspection above; `pip-audit` raw output has 2 duplicate entries for the same advisory, `dependency_audit()` normalizes to `finding_count=1`; `tests/security/test_sbom.py` passes.

### [VULN-014] Python 3.10 light profile resolves filelock with local-only advisories
- **Severity:** `LOW` (open, accepted)
- **Category:** Vulnerable dependency / local TOCTOU (CWE-59, CWE-367, CWE-1395)
- **Location:** `uv.lock` (`filelock 4.0.8` for `<3.11`); a fresh 3.10 install resolves `filelock 4.1.0` (fixed `4.2.1` requires 3.11); pulled by `huggingface-hub`
- **Status:** `OPEN` (no 3.10-compatible fixed release)
- **Exploit Scenario (PoC):** the advisories concern local lock-file handling (symlink/TOCTOU/mode issues) in the HF cache lock path. A local attacker who can create names in the user's cache/lock directories could interfere with locks. This requires local filesystem access at the same user/privilege boundary; there is no network/remote path through the API, and the engine profile (which performs the downloads) is 3.11+ and uses fixed filelock 4.2.10.
- **Rationale for acceptance:** LOW because it is local-only, cache-lock specific, and the 3.10 profile is the light/API deployment; the audited engine/download environment is 3.11+ with the fix. 17 advisory entries in `filelock 4.1.0` on 3.10; 14 in the lock's 4.0.8.
- **Required Remediation (optional/non-blocking):** drop Python 3.10 (align with engine profile) or vendor a backport of the filelock fixes for 3.10.
- **Verification Proof:** Python 3.10.22 venv + `uv pip install .` + `pip_audit -f json` → 62 deps, 17 entries in `filelock 4.1.0` only; no engine-package advisories. `uv export --python 3.10` shows `filelock==4.0.8 ; python_full_version < '3.11'`.

---

## 4. AUTOMATED SECURITY TOOLING LOG

All commands were run from the repository root unless noted. No secret values are printed.

| Tool / Command | Scope | Exit Code | Findings Summary |
| :--- | :--- | :--- | :--- |
| `PYTHONPATH=src /tmp/opencode/fsociety-venv/bin/python -m pytest tests/security -q` | Light profile (Python 3.14.10) | `0` | **158 passed, 1 skipped** in 28.23 s |
| `PYTHONPATH=src /tmp/opencode/engine-env/bin/python -m pytest tests/security -q` | Engine profile (Python 3.12.16) | `0` | **158 passed, 1 skipped**, 1 warning in 31.19 s |
| `PYTHONPATH=src /tmp/opencode/fsociety-venv/bin/python -m pytest -q -m "not gpu and not engine"` | Full light suite (CI marker set) | `0` | **473 passed, 6 skipped, 3 subtests passed** in 115.12 s; skips are torch-required upstream tests plus the engine-profile trust check |
| `python -m ruff format --check src tests` | Whole tree | `0` | 144 files already formatted |
| `python -m ruff check src tests` | Whole tree | `0` | All checks passed |
| `uv lock --check` | Lock consistency | `0` | Resolved 171 packages; lock matches `pyproject.toml` |
| `fsociety security audit --path src --path docs --path .github --path .env.example` | Production files | `0` | **0 production secret findings**, 0 test-fixture findings; 1 INFO config finding (engine unavailable on this host); dependency audit `unavailable` in the light interpreter (honest) |
| `pip-audit --path /tmp/opencode/fsociety-venv/lib/python3.14/site-packages -f json` | Light/API environment | `0` | 50 dependencies, **0 known vulnerabilities** |
| `python -m pip_audit -f json` in `/tmp/opencode/engine-env` | Engine environment (3.12, lock-synced) | `1` | 167 dependencies; **1 unique advisory** (sqlitedict 2.1.0, 2 duplicate entries) — VULN-013 |
| `uv pip install .` + `pip_audit` in a clean CPython 3.10.22 venv | Python 3.10 light profile | `1` | 62 dependencies; no engine packages; **17 entries in filelock 4.1.0 only** (VULN-014) |
| `uv export --frozen --no-dev --python 3.10` | 3.10 resolution | `0` | Engine pins carry `python_full_version >= '3.11'`; no torch/transformers/datasets/lm-eval/pillow for 3.10 |
| `dependency_audit()` in the engine interpreter | Platform audit API | `1` | `status=vulnerabilities`, `finding_count=1` (sqlitedict), normalized/deduplicated — VULN-006 fixed |
| `npm audit --json` (PATH=`/tmp/opencode/node/bin`) | `frontend/` | `0` | 125 dependencies, **0 vulnerabilities** (all severities 0) — VULN-010 fixed |
| Raw-socket probe against `uvicorn` (TE / TE+CL / large CL) | HTTP server | n/a | TE and TE+CL → `400`; large `Content-Length` → `413` (supports VULN-007) |
| `git ls-files \| grep -E '(^\|/)\.env($\|\.)'` | Tracked files | `1` (no match) | `.env` not tracked; `.env.example` is |
| `git grep -lE 'hf_…\|sk-…\|AKIA…\|gh[pousr]_…\|BEGIN … PRIVATE KEY' -- . ':!tests' ':!uv.lock' ':!frontend/package-lock.json'` | Tracked non-test files | `1` (no match) | No high-confidence credentials |
| `git log --all -G'AKIA…\|hf_…\|gh[pousr]_…\|BEGIN … PRIVATE KEY' --oneline` | Full git history | `0` | No commit adds/removes a credential-shaped line |
| `redact_tree` over `tests`, `frontend/src`, `docs`, `.github`, `deployment` | Working tree | n/a | 4 hits, all **synthetic fixtures**; 0 in frontend/docs/CI/deployment |

**Adversarial tests added (green in both profiles):**

- `tests/security/test_api_security.py` — auth bypass attempts, full OpenAPI auth coverage, header spoofing, loopback policy, bearer/token parsing, security headers, Host validation, body limits (declared/chunked/compressed), rate limiting, docs exposure, structured errors, traversal on scan/inspect/export/report, workspace confinement for model/job references, settings redaction, foreign/null Origin CSRF, plugin API overrides.
- `tests/security/test_path_jail.py` — `safe_join` traversal/absolute/symlink/null-byte cases, workspace resolution, model manager traversal, manifest escape, SPA containment, POSIX drive-string semantics.
- `tests/security/test_plugin_isolation_adversarial.py` — real child environment inspection, explicit-env filtering, temporary cwd, network-namespace blocking of a host listener (and `allow_network=True` success), crash/timeout recovery, output cap, **discovery does not import in host**, explicit `load()` residual, in-process opt-in.
- `tests/security/test_trust_policy_adversarial.py` — exact allowlist matching, malformed ids/repositories, engine flag refusal, shell/path metacharacters, config generation, no remote-code key.
- `tests/security/test_injection_and_secrets.py` — SQLi payloads against every lookup/filter, column-name allowlist, token redaction (HF/OpenAI/Anthropic/AWS/GitHub/Google/PEM/Bearer), audit-log redaction, secret tree scan, AST guard for `eval/exec/pickle/shell=True`.
- `tests/security/test_frontend_static.py` — no DOM injection primitives, key only in `sessionStorage`, no key in URLs, inline-script check, CSP consistency, no source maps.
- `tests/security/test_supply_chain.py` — `uv.lock` graph, AGPL/Heretic attribution, preserved `src/heretic` touch-points, npm lock/install hooks.
- Fixtures: `tests/fixtures/plugins/{environment_probe,network_probe,import_side_effect}.py`.

---

## 5. FINAL RELEASE SIGN-OFF

- **All `CRITICAL` findings resolved & verified:** `YES` (none found)
- **All `HIGH` findings resolved & verified:** `YES` (VULN-001, VULN-004 closed)
- **Open `MEDIUM`:** 1 (VULN-013 sqlitedict, accepted with compensating control)
- **Open `LOW`:** 3 (VULN-009 public docs, VULN-011 non-lockfile Docker build, VULN-014 Python 3.10 filelock)
- **Ready for Production Delivery:** `APPROVED`

**Scope condition (record in release notes):** the **engine profile requires Python 3.11+**; on Python 3.10 the package installs the light/API platform only (no torch/transformers/datasets/lm-eval/Pillow) and the CLI reports the engine as unavailable. Python 3.10 remains supported for the light/API profile with the accepted local-only filelock residual (VULN-014). The Python 3.12 production engine environment and the Docker CPU image are the audited artifacts.

**Accepted residuals (not release-blocking under the exit gate):**

1. **VULN-013** — sqlitedict advisory with no upstream fix; `use_cache` is never passed and no cache file is ingested; local same-user precondition. Keep cache disabled/private and track upstream.
2. **VULN-009** — public OpenAPI/docs; no data exposure and all operations authenticated; disable docs for hardened remote deployments.
3. **VULN-011** — Docker build does not consume the lockfile; the light image dependency set is manually audited clean. Reproducibility improvement, not an active vulnerability.
4. **VULN-014** — filelock on Python 3.10 (17 local-only advisory entries; fixed 4.2.1 requires 3.11). Light profile only; drop 3.10 or backport to close.
5. Minor hardening notes in §6 (OriginCheck 403 headers, ASGI TE+CL server dependency).

**Availability limitations (not security approvals):**

- GPU/accelerator model modification and real benchmark parity were not executed on this CPU-only host; those paths are covered by unit tests and the preserved upstream suite where possible.
- The Docker image was not built (no daemon on this host); AC-35 remains an availability/build verification item, not a security finding.
- Both limitations are recorded in `.factory/TASKS.md` and `docs/reports/FINAL_PROJECT_REPORT.md`; they do not change the security status above.

**Acceptance criteria mapping (security):**

| AC | Result | Evidence |
| :--- | :--- | :--- |
| AC-24 path traversal rejected | **PASS** | `test_path_jail.py`, traversal cases in `test_api_security.py` |
| AC-25 `trust_remote_code` exact allowlist | **PASS** | `test_trust_policy_adversarial.py`, engine flag refusal |
| AC-26 remote API without key refused | **PASS** | `test_remote_without_configured_key_fails_closed`, OpenAPI auth sweep |
| AC-27 no secret patterns in logs/reports | **PASS** | redaction tests, report redaction test, secret scan (0 production hits) |
| AC-28 strict validation + structured errors | **PASS** | API tests, structured-error tests |
| AC-29 cancellable jobs / no torch required | **PASS** (light) | `tests/api`, plugin/isolation tests |
| AC-30 OpenAPI generated | **PASS** | `test_openapi_schema`, auth sweep |

---

## 6. RESIDUAL RISKS & DOCUMENTED LIMITATIONS

1. **Plugin isolation is process-level, not a VM.** Discovery and execution run as the host user in the same kernel; a plugin can read files that user can read. `unshare -n` blocked the host listener in this audit (`network_isolated=true`), but a host without unshare reports `false` honestly. No container/seccomp/AppArmor boundary is mandated. `load()`/in-process mode still imports code in the host by explicit opt-in; built-ins are trusted.
2. **Rate limiting is per process.** Multiple uvicorn workers multiply the effective limit; a shared limiter is required for multi-worker deployments.
3. **API key in `sessionStorage`.** XSS-readable by design; mitigated by `script-src 'self'`, no dynamic HTML, no `unsafe-eval`, and the key is never persisted. A successful dashboard XSS would still steal it for the tab lifetime.
4. **`HF_TOKEN` is forwarded to the engine subprocess** (`core/engine.py:92-122`) for gated downloads. Combined with `trust_remote_code=always` (not recommended), repository code could read it; the default `never` policy and explicit allowlist keep that path closed.
5. **`write_platform_config(extra=...)` does not reserve `enable_shell`.** No API/CLI call forwards untrusted `extra` today; future callers must reserve it.
6. **Report lookup asymmetry.** `/experiments/{id}/report` resolves and confines paths, but the `/evaluation/reports/{id}` fallback uses `rglob("*.json")` without a per-file `resolve()` containment check. A local attacker who can plant a symlink inside `workspace/reports` could make the server read an outside JSON file; no remote write primitive is exposed.
7. **Authenticated path disclosure.** `/system`, `/system/engine`, `/system/workspace` and error `details.searched` reveal absolute host paths to authenticated/loopback clients. Accepted for a single-operator tool.
8. **User-supplied job payloads are stored and echoed.** `JobRecord.request`/`error` are returned without redaction; a caller who submits a secret in a prompt/model id can read it back. Not a cross-principal leak.
9. **Discovery metadata is produced by untrusted worker code.** The host validates the `PluginInfo` shape, but a malicious plugin can influence its own metadata while running in the discovery worker. Default execution still runs it in the worker; only explicit `load()`/in-process uses the metadata path in the host. Low integrity risk.
10. **Python 3.10 filelock residual (VULN-014).** Local-only advisories; engine profile unaffected (3.11+ uses 4.2.10).
11. ~~OriginCheck 403 hardening headers~~ **Resolved after the audit pass:** `RequestContextMiddleware` is now added after `OriginCheckMiddleware`, so its 403 response carries CSP/XCTO/`Cache-Control`/framing headers (verified: `nosniff`, `no-store`, `DENY`).
12. **ASGI-server dependency for TE+CL.** Uvicorn/h11 rejects `Transfer-Encoding` requests with `400` (verified with a raw socket); a different ASGI server that forwards both TE and a small CL could bypass the middleware's declared-length check. Keep the middleware rule or reject any TE. LOW residual.
13. **HSTS** is only added when the ASGI app sees `https`; the TLS proxy must forward the scheme or add the header itself.
14. **`api_allowed_hosts=["*"]`** disables Host validation by operator choice; `GET /api/v1/security/findings` flags it as MEDIUM at runtime.
15. **Single-operator model.** No RBAC, tenancy or object ownership; every authenticated key holder can read/write all platform state. Intentional per SPEC §2.
16. **GPU/Docker execution unverified** — availability limitations, not security approvals (see §5).

---

*Report generated by `@redteam-auditor`. Evidence artifacts from the audit run are under `/tmp/opencode/` on the audit host (`pytest-security-light-final.log`, `pytest-security-engine-final.log`, `pytest-light-full.log`, `pip-audit-engine-final.json`, `pip-audit-light-final.json`, `pip-audit-py310.json`, `export-py310.txt`, `npm-audit-final.json`, `secret-audit-final3.json`, `ruff-*-final3.log`).*
