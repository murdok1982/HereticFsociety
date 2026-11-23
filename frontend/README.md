# FSOCIETY AI — Operator Console

React 19 + TypeScript + Vite dashboard for the FSOCIETY AI platform. Every metric,
model, job, suite, finding and setting is fetched from the FastAPI backend. There
are no fixtures, simulated GPU values or fabricated benchmark results in `src/`.

## Stack

| Layer      | Choice                                                   |
| ---------- | -------------------------------------------------------- |
| UI         | React 19 (resolved 19.3.x), React Router 7               |
| Language   | TypeScript 5.8 strict (`tsc --noEmit`)                   |
| Build      | Vite 6 (resolved 6.4.x), `outDir: dist`, `base: "/"`     |
| Fonts      | `@fontsource-variable/inter`, `@fontsource-variable/jetbrains-mono` |
| Components | Hand-built accessible primitives; no UI/chart libraries  |
| Data       | `fetch` with AbortController, timeouts and typed errors   |

## Run

```bash
export PATH=/tmp/opencode/node/bin:$PATH   # environment-specific; any Node 20.19+/22.12+ works
cd frontend
npm install
npm run dev          # http://127.0.0.1:5173
npm run typecheck    # tsc --noEmit
npm run build        # typecheck + production bundle
```

The dev server proxies `/api` to `http://127.0.0.1:8000`, so start the backend
first:

```bash
FSOCIETY_WORKSPACE=/path/to/workspace uv run fsociety serve
```

`.npmrc` disables bin shims because this checkout may live on exFAT, which cannot
create symlinks. Package scripts invoke `node ./node_modules/...` directly, so
`npm run typecheck` and `npm run build` work without `node_modules/.bin`.

### API base

Defaults to `/api/v1`. Override with `VITE_API_BASE` (see `.env.example`). The
sidebar and Settings page display the active base and the FastAPI docs link
(`/api/docs`).

### Authentication

When the backend requires an API key, open **Settings**, paste the key and press
**Save & test connection**. The key is stored in `sessionStorage` for the current
tab only, injected as `X-API-Key` at request time, and never written to disk.
Expired/missing credentials produce a 401 error state with a direct Settings link.

## Routes

| Route              | Purpose                                                           |
| ------------------ | ----------------------------------------------------------------- |
| `/`                | Live system, engine, hardware and recent activity                 |
| `/models`          | Scan, inspect, verify and estimate local/HF-cache models          |
| `/hardware`        | CPU/RAM/GPU cards, backend matrix, real `?refresh=true` detection |
| `/optimizer`       | Declarative search-space builder + live optimization job          |
| `/evaluation`      | Suites, evaluation jobs, report viewer and baseline comparison    |
| `/experiments`     | Filterable history; `/experiments/:id` detail, trials, artifacts  |
| `/plugins`         | Registry validity, permissions and diagnostics                    |
| `/exports`         | Export bundles registered by the CLI                              |
| `/security`        | Policy, findings, audit log and CycloneDX SBOM download           |
| `/settings`        | Redacted settings groups, API key, engine and version             |

## Data-state contract

Every remote view implements:

- **loading** — shimmer skeletons, never placeholder domain values;
- **error** — normalised `ApiError` with server code/message/hint, Retry button,
  and an explicit `API unreachable` state for network failures;
- **empty** — a concrete next action (run a scan, run a suite, export via CLI…);
- **stale** — a warning banner when a background refresh fails but old data exists.

Polling pauses while `document.visibilityState === "hidden"`, resumes on
visibility change, and aborts in-flight requests on unmount or dependency change.

## Source layout

```
src/
  api/          typed contracts + fetch client
  components/
    layout/     shell, navigation, responsive drawer
    ui/         buttons, fields, tables, drawer, async/error/empty states, job card
  hooks/        useApiQuery, useMutation, useJobPoll, useNow, useApiKey, title
  lib/          formatting, status tones, sessionStorage API key
  pages/        ten routed sections
  styles/       tokens.css (design tokens) + app.css (system and responsive rules)
```

## Known integration notes

- `POST /optimization/jobs` follows the documented contract (`directions`,
  optional seed/sampler). The in-progress backend currently also requires
  `model_id`; the form exposes an optional model reference and sends it when
  provided. If the backend rejects a body, the structured 422 is displayed.
- Report JSON is not fetched through any undocumented endpoint. The viewer renders
  experiment metrics plus per-category/per-item tables discovered in artifact
  metadata, and shows artifact paths/metadata otherwise.
- GPU, engine and model data can legitimately be absent on CPU-only hosts. The UI
  reports those conditions from the API instead of filling in synthetic values.
