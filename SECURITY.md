# Security Policy

## Supported versions

| Version | Supported |
| :--- | :--- |
| 3.0.x | Yes |
| 2.x (upstream Heretic) | Upstream project policy |

## Reporting a vulnerability

Do not open a public issue for security-sensitive reports. Contact the maintainers
privately through the repository's security advisory feature
(`Security → Report a vulnerability`) on GitHub, or by email to the address listed in
`pyproject.toml` if a private channel is available.

Include: affected version/commit, deployment mode (loopback, reverse proxy, container),
reproduction steps or a minimal proof of concept, impact, and any suggested fix.
Do not include real credentials in the report.

**Never test against systems you do not own or have explicit permission to test.**

## Threat model (summary)

FSOCIETY AI is a single-operator engineering tool, not a multi-tenant service.

| Trust boundary | Control |
| :--- | :--- |
| Browser → API | API key (constant-time), loopback trust, trusted hosts, CORS allowlist |
| API → filesystem | Symlink-aware path jail, absolute-path policy, report/export root containment |
| Model repository → process | `trust_remote_code` denied by default; exact `org/name` allowlist |
| Plugin → host | Subprocess isolation, env allowlist, timeouts, output caps, process-group kill, optional `unshare -n` |
| Logs / reports | Secret redaction before persistence |
| Supply chain | SHA-256 artifacts, CycloneDX SBOM, optional `pip-audit` |

## Secure deployment checklist

1. Never expose the API directly to the internet. Use a reverse proxy with TLS.
2. Set `FSOCIETY_API_KEY` to a high-entropy value; keep it out of shell history
   (`read -rs`, secret manager or environment file with `chmod 600`).
3. Bind to `127.0.0.1` unless remote access is required. If remote, configure
   `FSOCIETY_API_ALLOWED_HOSTS` explicitly.
4. Keep `FSOCIETY_TRUST_REMOTE_CODE=never`. If a model genuinely requires remote code,
   use `allowlist` with the exact repository and audit the code first.
5. Keep `FSOCIETY_PLUGIN_ISOLATION=true` and `FSOCIETY_PLUGIN_ALLOW_NETWORK=false`.
6. Run the service as an unprivileged user with a dedicated workspace volume.
7. Review `fsociety security findings` and `fsociety security audit` after configuration changes.
8. Run `fsociety security deps` (requires `pip-audit`) as part of upgrades.
9. Back up `data/fsociety.sqlite3` and reports before upgrades.

## Python support

The platform/API profile supports Python 3.10+. The engine profile requires Python 3.11+
because fixed Pillow/filelock/dnspython releases require it; on 3.10 engine dependencies are
not installed and the CLI reports the engine as unavailable. Python 3.10 engine installs are
not security-approved.

## Known limitations

* Plugin isolation is a process boundary, not a container/VM sandbox. Do not run
  untrusted plugins on a host that holds unrelated secrets.
* Rate limiting and job execution are per-process. Multiple uvicorn workers do not share
  limits or the job queue.
* `trust_remote_code=always` is possible but produces a HIGH configuration finding; it is
  intended only for fully controlled research environments.
* Analytical memory figures are estimates, not verified runtime usage.
* The dashboard stores the API key in `sessionStorage` (never `localStorage`); an XSS
  would be able to read it. CSP restricts script origins, but defense-in-depth still
  applies.
* Only the tested Python versions and platforms in CI are supported; GPU behavior depends
  on drivers, CUDA/ROCm builds and hardware that CI may not reproduce.
