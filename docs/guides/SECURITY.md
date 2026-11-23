# Security Guide

The authoritative policy, threat model and reporting process live in
[`SECURITY.md`](../../SECURITY.md) at the repository root. This guide covers the
operational checks specific to the platform layer.

## Defaults that matter

| Setting | Default | Why |
| :--- | :--- | :--- |
| `api_host` | `127.0.0.1` | The API is not exposed by accident |
| API key | unset | Non-loopback requests are refused until configured |
| `trust_remote_code` | `never` | Model repositories cannot execute code |
| `plugin_isolation` | `true` | Plugin methods run in a subprocess |
| `plugin_allow_network` | `false` | Requests network isolation when the kernel allows it |
| CORS | disabled | Same-origin dashboard; wildcard is rejected at startup |

## Verify your deployment

```bash
fsociety security policy
fsociety security findings        # configuration-derived, not a CVE scan
fsociety security audit --path src --path .github
fsociety security sbom --output sbom.cdx.json
fsociety security deps            # requires pip-audit
curl -sI http://127.0.0.1:8000/api/v1/health   # inspect security headers
```

Expected on a loopback install: no HIGH findings; the modification engine may be
reported as unavailable (INFO). `security audit` excludes synthetic test fixtures from
the failure count and lists them separately.

## Remote access checklist

1. Generate a 256-bit key: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
2. Export `FSOCIETY_API_KEY` from a secret manager, never from a committed file.
3. Set `FSOCIETY_API_ALLOWED_HOSTS` to the exact proxy hostname.
4. Terminate TLS at a reverse proxy; forward `X-API-Key` and preserve the `Host` header.
5. Keep the workspace on a volume readable only by the service user.
6. Do not enable `trust_remote_code=always`; use `allowlist` with audited repositories.
7. Do not disable plugin isolation on a host holding unrelated secrets.

## Plugin isolation limits

Plugins run as the same OS user in a subprocess with timeouts, output caps, an environment
allowlist and optional network namespace. They can still read files the service user can
read and exploit kernel vulnerabilities. Treat third-party plugins as code you are choosing
to run; use a container or VM for a stronger boundary.

## Model integrity

```bash
fsociety models verify ./models/my-model --manifest exports/my-model/manifest.json
fsociety export bundle --file ... --destination my-bundle
```

Hashes are SHA-256 over relative path + content. `verify` reports mismatches and missing
files, and exits non-zero.

## What the platform does not do

* It does not scan model weights for malicious tensors.
* It does not provide multi-tenant authorization or per-user isolation.
* It does not guarantee safe strided/pickle-based model formats; prefer safetensors.
* It does not replace a CVE scanner or an OS-level sandbox.

## Incident response

1. Stop the API (`SIGTERM`), revoke the API/HF tokens.
2. Preserve `logs/audit.jsonl`, `data/fsociety.sqlite3` and reports for analysis.
3. Identify the request ID from the error envelope or `X-Request-ID`.
4. Rotate credentials, review `security audit` and plugin inventory, then redeploy from a
   clean environment.
5. Report per `SECURITY.md`.
