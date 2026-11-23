# Guides Index

Operational documentation for FSOCIETY AI. For design and requirements, start at
[`docs/sdd/`](../sdd/).

| Guide | Use it when |
| :--- | :--- |
| [Installation](INSTALLATION.md) | Setting up a host, GPU wheels, lightweight profile, dashboard build |
| [Migration](MIGRATION.md) | Moving from `heretic-llm` 2.x / HereticFsociety to FSOCIETY AI 3.0 |
| [CLI](CLI.md) | Every command, flags, JSON output and exit codes |
| [API](API.md) | Endpoints, schemas, auth, errors and hardening |
| [Configuration](CONFIGURATION.md) | All settings, precedence, secrets and trust policy |
| [Optimization](OPTIMIZATION.md) | Search spaces, seeded Optuna runs, planning and determinism |
| [Benchmarking](BENCHMARKING.md) | Suites, graders, backends, reports and comparability |
| [Plugins](PLUGINS.md) | Plugin contract, discovery, isolation and examples |
| [Security](SECURITY.md) | Deployment checklist, verification commands and limits |
| [Troubleshooting](TROUBLESHOOTING.md) | Common failures and their real causes |

## Quick commands

```bash
fsociety doctor
fsociety hardware
fsociety models list
fsociety serve
```

## Honesty policy

* Estimates are labelled estimates; only executed measurements are reported as measurements.
* Bundled evaluation suites are small original smoke suites, not official benchmarks.
* GPU-only and container-only checks that could not be executed are reported as
  skipped/unverified in the [final report](../reports/FINAL_PROJECT_REPORT.md).
