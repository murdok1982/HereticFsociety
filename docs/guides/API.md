# API Reference — `/api/v1`

Base URL: `http://127.0.0.1:8000`. OpenAPI schema: `/api/openapi.json`, interactive docs:
`/api/docs` (disable with `FSOCIETY_API_DOCS_ENABLED=false`). All payloads are JSON.
Request bodies are validated with Pydantic (`extra="forbid"`); unknown fields are rejected.

## Authentication

* Loopback clients are trusted by default (configurable with
  `api_require_key_for_loopback`).
* Any non-loopback client **must** send `X-API-Key: <key>` or
  `Authorization: Bearer <key>`.
* If no key is configured, non-loopback requests get `403 remote_access_disabled`.
* Wrong/missing key: `401 authentication_required` / `401 invalid_api_key`.

```bash
export KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
FSOCIETY_API_KEY="$KEY" fsociety serve &
curl -H "X-API-Key: $KEY" http://127.0.0.1:8000/api/v1/hardware
```

## Errors

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed.",
    "details": {"errors": [{"field": "suite_id", "message": "Field required"}]},
    "hint": "optional remediation",
    "request_id": "9f6c..."
  }
}
```

Unhandled errors return `500 internal_error` **without** tracebacks; use `request_id` with
the audit log. Rate limit: `429 rate_limited` with `Retry-After` and
`X-RateLimit-Remaining`.

## Endpoints

### Public

| Method | Path | Response |
| :--- | :--- | :--- |
| GET | `/health` | `{status, checks, version, uptime_seconds}` |
| GET | `/version` | `SystemInfo` |

### System

| Method | Path | Notes |
| :--- | :--- | :--- |
| GET | `/system` | platform/engine info |
| GET | `/system/engine` | availability, missing modules, versions, hint |
| GET | `/system/workspace` | directory layout and offline flag |
| GET | `/hardware?refresh=false` | full `HardwareProfile` with provenance |
| GET | `/events?limit=&category=` | persisted events |
| GET | `/settings` | redacted settings (never the key) |

### Models

| Method | Path | Notes |
| :--- | :--- | :--- |
| GET | `/models?include_hf_cache=&limit=` | scan models dir + HF cache |
| POST | `/models/scan` | `{roots: string[], include_hf_cache: bool}` |
| GET | `/models/inspect?reference=&revision=&hash_files=` | metadata |
| POST | `/models/verify` | `{reference, manifest_path?}` |
| GET | `/models/estimate?reference=&batch_size=&sequence_length=&dtype=&quantization=` | `{model, estimate, hardware}` |

Paths are relative to the workspace; absolute paths are only allowed inside the workspace
or configured models directory.

### Jobs

| Method | Path | Notes |
| :--- | :--- | :--- |
| POST | `/optimization/jobs` | analytical resource planning (see body below) |
| POST | `/evaluation/jobs` | run a bundled suite with a real backend |
| GET | `/jobs?limit=&status=` | recent jobs |
| GET | `/jobs/{job_id}` | status/progress/result/error |
| POST | `/jobs/{job_id}/cancel` | cooperative cancellation |

Optimization body:

```json
{
  "name": "plan-7b",
  "model_id": "Qwen/Qwen3-4B-Instruct-2507",
  "space": {"dimensions": [
    {"name": "dtype", "kind": "categorical", "choices": ["bfloat16", "float16"]},
    {"name": "batch_size", "kind": "int", "low": 1, "high": 16}
  ]},
  "base": {"sequence_length": 4096},
  "n_trials": 20,
  "seed": 42,
  "sampler": "tpe"
}
```

The objective minimizes estimated total bytes and `does_not_fit`; results are explicitly
analytical estimates. The API never executes arbitrary objective code.

Evaluation body:

```json
{
  "suite_id": "python_coding",
  "backend": {"kind": "transformers", "model_id": "Qwen/Qwen3-4B-Instruct-2507",
               "device": "cuda", "dtype": "bfloat16"},
  "seed": 42,
  "max_new_tokens": 256,
  "temperature": 0.0,
  "limit": 20,
  "batch_size": 1
}
```

If the required runtime is unavailable the job fails with `engine_unavailable` and an
install hint. `llama_cpp` accepts `path`, `n_ctx`, `n_gpu_layers` instead of `model_id`.

### Experiments and reports

| Method | Path | Notes |
| :--- | :--- | :--- |
| GET | `/experiments?limit=&offset=&kind=&status=` | history |
| GET | `/experiments/{id}` | params, metrics, hashes, fingerprint |
| GET | `/experiments/{id}/trials` | persisted trials |
| GET | `/experiments/{id}/artifacts` | artifacts with sha256 |
| GET | `/experiments/{id}/report?format=json\|markdown` | stored evaluation report |

### Evaluation

| Method | Path | Notes |
| :--- | :--- | :--- |
| GET | `/evaluation/suites` | bundled suite metadata |
| GET | `/evaluation/suites/{id}` | suite + items |
| GET | `/evaluation/reports/{report_id}` | stored report |
| POST | `/evaluation/compare` | `{baseline_report_id, candidate_report_id, threshold}` |

Comparison returns `comparable=false` with reasons when suite hash, grader version or
evaluation configuration differ. Hardware differences appear under `warnings`.

### Plugins, exports, security

| Method | Path | Notes |
| :--- | :--- | :--- |
| GET | `/plugins` | discovered plugins with validity/errors |
| POST | `/plugins/reload` | rescan workspace plugins |
| POST | `/plugins/{name}/run` | `{method, payload, allow_in_process}` (isolated by default) |
| GET | `/exports` | export artifacts |
| POST | `/exports/bundle` | `{files, destination, metadata}` |
| GET | `/security/policy` | trust/plugin/API policy summary |
| GET | `/security/audit` | audit events |
| GET | `/security/findings` | configuration findings (not a CVE scan) |
| GET | `/security/sbom` | CycloneDX component inventory (cached 5 min) |
| GET | `/security/dependency-audit?refresh=` | pip-audit wrapper or explicit "unavailable" |

## Hardening

Security headers on every response: CSP, `X-Content-Type-Options`, `X-Frame-Options:
DENY`, `Referrer-Policy`, `Permissions-Policy`, COOP/CORP. TrustedHost, CORS allowlist
(no wildcards), request body limit, sliding-window rate limit, `X-Request-ID`.

`/api/docs` and `/api/openapi.json` are unauthenticated by design (schema only, no data).
Set `FSOCIETY_API_DOCS_ENABLED=false` on an internet-facing deployment.

Mutating requests that carry a foreign `Origin` are rejected (`403 origin_not_allowed`);
requests without `Origin` (CLI/curl) and loopback development origins are allowed.
Chunked request bodies must declare `Content-Length` (`413`), compressed bodies are refused
(`415`), and API responses carry `Cache-Control: no-store`. HSTS is emitted only when the
request reaches the app over HTTPS — add it at the TLS terminator if it proxies plain HTTP.

## Operational notes

* Jobs run in a thread pool (2 workers) inside one process; interrupted jobs are marked
  failed on restart.
* Rate limiting and job state are per-process; do not run multiple workers without a
  shared queue/limiter.
* The dashboard consumes exactly these endpoints; see `frontend/src/api/types.ts`.
