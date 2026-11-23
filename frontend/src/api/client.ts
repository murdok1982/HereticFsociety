/**
 * Typed fetch client for the FSOCIETY AI API.
 *
 * - Every request supports cancellation via AbortSignal and a hard timeout.
 * - Errors are normalised into ApiError with the server's structured code.
 * - The API key is injected from sessionStorage at request time.
 */

import { getApiKey } from "../lib/apiKey";
import { isRecord } from "../lib/format";
import type {
  ArtifactListResponse,
  BenchmarkSuiteInfo,
  ComparisonResult,
  CompareRequest,
  EstimateParams,
  EstimateResponse,
  EvaluationJobRequest,
  EventListResponse,
  ExperimentListResponse,
  ExperimentRecord,
  FindingsResponse,
  HardwareProfile,
  HealthResponse,
  JobListResponse,
  JobRecord,
  ModelInfo,
  ModelListResponse,
  ModelValidation,
  OptimizationJobRequest,
  PluginListResponse,
  PlatformSettings,
  SbomDocument,
  ScanRequest,
  SecurityPolicy,
  SuiteListResponse,
  SystemInfo,
  EngineStatus,
  TrialListResponse,
  VerifyRequest,
} from "./types";

export interface ApiErrorInit {
  code: string;
  message: string;
  status: number;
  details?: unknown;
  hint?: string;
}

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: unknown;
  readonly hint: string | undefined;

  constructor(init: ApiErrorInit) {
    super(init.message);
    this.name = "ApiError";
    this.code = init.code;
    this.status = init.status;
    this.details = init.details;
    this.hint = init.hint;
  }

  get isUnauthorized(): boolean {
    return this.status === 401 || this.code === "unauthorized";
  }

  get isUnreachable(): boolean {
    return this.code === "network" || this.code === "timeout";
  }
}

const DEFAULT_TIMEOUT_MS = 20_000;
const SCAN_TIMEOUT_MS = 180_000;
const ESTIMATE_TIMEOUT_MS = 60_000;
const HEALTH_TIMEOUT_MS = 8_000;

export function getApiBase(): string {
  const configured = import.meta.env.VITE_API_BASE;
  const base = typeof configured === "string" && configured.trim()
    ? configured.trim()
    : "/api/v1";
  return base.replace(/\/+$/, "");
}

/** Direct links to real backend surfaces. */
export const API_LINKS = {
  base: getApiBase(),
  health: `${getApiBase()}/health`,
  openapi: "/api/docs",
} as const;

interface RequestOptions {
  method?: "GET" | "POST";
  body?: unknown;
  signal?: AbortSignal;
  timeoutMs?: number;
}

type QueryValue = string | number | boolean | null | undefined;

function toQuery(params: Record<string, QueryValue>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, String(value));
  }
  const serialized = search.toString();
  return serialized ? `?${serialized}` : "";
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const {
    method = "GET",
    body,
    signal,
    timeoutMs = DEFAULT_TIMEOUT_MS,
  } = options;

  const controller = new AbortController();
  let timedOut = false;
  const timer = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  const abortFromCaller = () => controller.abort();
  if (signal) {
    if (signal.aborted) {
      controller.abort();
    } else {
      signal.addEventListener("abort", abortFromCaller, { once: true });
    }
  }

  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const apiKey = getApiKey();
  if (apiKey) headers["X-API-Key"] = apiKey;

  let response: Response;
  try {
    response = await fetch(`${getApiBase()}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
      credentials: "same-origin",
    });
  } catch (error) {
    if (timedOut) {
      throw new ApiError({
        code: "timeout",
        status: 0,
        message: `Request timed out after ${Math.round(timeoutMs / 1000)}s.`,
        hint: "The backend is slow or unreachable. Retry once it is responsive.",
      });
    }
    if (controller.signal.aborted) {
      throw new ApiError({
        code: "aborted",
        status: 0,
        message: "Request cancelled.",
      });
    }
    throw new ApiError({
      code: "network",
      status: 0,
      message: "API unreachable. The dashboard could not connect to the FSOCIETY API.",
      hint: "Start the backend on 127.0.0.1:8000; Vite proxies /api to that address in development.",
      details: error instanceof Error ? error.message : String(error),
    });
  } finally {
    window.clearTimeout(timer);
    if (signal) signal.removeEventListener("abort", abortFromCaller);
  }

  let rawText = "";
  try {
    rawText = await response.text();
  } catch {
    rawText = "";
  }

  let payload: unknown = null;
  if (rawText) {
    try {
      payload = JSON.parse(rawText) as unknown;
    } catch {
      payload = rawText;
    }
  }

  if (!response.ok) {
    const envelope =
      isRecord(payload) && isRecord(payload.error) ? payload.error : null;
    const serverCode =
      envelope && typeof envelope.code === "string" ? envelope.code : null;
    const serverMessage =
      envelope && typeof envelope.message === "string" ? envelope.message : null;
    const serverHint =
      envelope && typeof envelope.hint === "string" ? envelope.hint : null;
    const code =
      serverCode ??
      (response.status === 401 ? "unauthorized" : `http_${response.status}`);
    let message = serverMessage;
    if (!message && typeof payload === "string" && payload.trim()) {
      message = payload.trim().slice(0, 400);
    }
    if (!message && response.status === 401) {
      message = "Authentication required. Configure an API key in Settings.";
    }
    if (!message) {
      message = `Request failed with HTTP ${response.status}.`;
    }
    const hint =
      serverHint ??
      (response.status === 401
        ? "Open Settings and paste the API key configured on the backend. The key is kept in sessionStorage for this tab only."
        : undefined);
    throw new ApiError({
      code,
      message,
      status: response.status,
      details: envelope?.details,
      hint,
    });
  }

  if (typeof payload === "string") {
    throw new ApiError({
      code: "invalid_response",
      status: response.status,
      message: `Expected JSON from ${path} but received non-JSON content.`,
      hint: "Check VITE_API_BASE and the reverse-proxy configuration: /api must reach the FastAPI service.",
      details: payload.slice(0, 400),
    });
  }

  return payload as T;
}

/* ------------------------------------------------------------------ */
/* System                                                              */
/* ------------------------------------------------------------------ */

export const api = {
  getHealth(signal?: AbortSignal): Promise<HealthResponse> {
    return request<HealthResponse>("/health", {
      signal,
      timeoutMs: HEALTH_TIMEOUT_MS,
    });
  },

  getVersion(signal?: AbortSignal): Promise<SystemInfo> {
    return request<SystemInfo>("/version", { signal });
  },

  getSystem(signal?: AbortSignal): Promise<SystemInfo> {
    return request<SystemInfo>("/system", { signal });
  },

  getEngine(signal?: AbortSignal): Promise<EngineStatus> {
    return request<EngineStatus>("/system/engine", { signal });
  },

  getHardware(refresh = false, signal?: AbortSignal): Promise<HardwareProfile> {
    return request<HardwareProfile>(`/hardware${toQuery({ refresh })}`, {
      signal,
      timeoutMs: 60_000,
    });
  },

  /* ---------------------------------------------------------------- */
  /* Models                                                           */
  /* ---------------------------------------------------------------- */

  getModels(
    includeHfCache = true,
    signal?: AbortSignal,
  ): Promise<ModelListResponse> {
    return request<ModelListResponse>(
      `/models${toQuery({ include_hf_cache: includeHfCache })}`,
      { signal, timeoutMs: 90_000 },
    );
  },

  scanModels(body: ScanRequest, signal?: AbortSignal): Promise<ModelListResponse> {
    return request<ModelListResponse>("/models/scan", {
      method: "POST",
      body,
      signal,
      timeoutMs: SCAN_TIMEOUT_MS,
    });
  },

  inspectModel(
    reference: string,
    revision?: string,
    signal?: AbortSignal,
  ): Promise<ModelInfo> {
    return request<ModelInfo>(
      `/models/inspect${toQuery({ reference, revision })}`,
      { signal, timeoutMs: 90_000 },
    );
  },

  verifyModel(
    body: VerifyRequest,
    signal?: AbortSignal,
  ): Promise<ModelValidation> {
    return request<ModelValidation>("/models/verify", {
      method: "POST",
      body,
      signal,
      timeoutMs: SCAN_TIMEOUT_MS,
    });
  },

  estimateModel(
    params: EstimateParams,
    signal?: AbortSignal,
  ): Promise<EstimateResponse> {
    return request<EstimateResponse>(
      `/models/estimate${toQuery({
        reference: params.reference,
        batch_size: params.batch_size,
        sequence_length: params.sequence_length,
        dtype: params.dtype,
        quantization: params.quantization,
      })}`,
      { signal, timeoutMs: ESTIMATE_TIMEOUT_MS },
    );
  },

  /* ---------------------------------------------------------------- */
  /* Evaluation                                                       */
  /* ---------------------------------------------------------------- */

  getSuites(signal?: AbortSignal): Promise<SuiteListResponse> {
    return request<SuiteListResponse>("/evaluation/suites", { signal });
  },

  createEvaluationJob(
    body: EvaluationJobRequest,
    signal?: AbortSignal,
  ): Promise<JobRecord> {
    return request<JobRecord>("/evaluation/jobs", {
      method: "POST",
      body,
      signal,
      timeoutMs: 60_000,
    });
  },

  compareReports(
    body: CompareRequest,
    signal?: AbortSignal,
  ): Promise<ComparisonResult> {
    return request<ComparisonResult>("/evaluation/compare", {
      method: "POST",
      body,
      signal,
      timeoutMs: 60_000,
    });
  },

  /* ---------------------------------------------------------------- */
  /* Optimization                                                     */
  /* ---------------------------------------------------------------- */

  createOptimizationJob(
    body: OptimizationJobRequest,
    signal?: AbortSignal,
  ): Promise<JobRecord> {
    return request<JobRecord>("/optimization/jobs", {
      method: "POST",
      body,
      signal,
      timeoutMs: 60_000,
    });
  },

  /* ---------------------------------------------------------------- */
  /* Jobs & experiments                                               */
  /* ---------------------------------------------------------------- */

  getJobs(
    params: { limit?: number; status?: string } = {},
    signal?: AbortSignal,
  ): Promise<JobListResponse> {
    return request<JobListResponse>(
      `/jobs${toQuery({ limit: params.limit ?? 50, status: params.status })}`,
      { signal },
    );
  },

  getJob(jobId: string, signal?: AbortSignal): Promise<JobRecord> {
    return request<JobRecord>(`/jobs/${encodeURIComponent(jobId)}`, { signal });
  },

  cancelJob(jobId: string, signal?: AbortSignal): Promise<JobRecord> {
    return request<JobRecord>(
      `/jobs/${encodeURIComponent(jobId)}/cancel`,
      { method: "POST", signal },
    );
  },

  getExperiments(
    params: { limit?: number; kind?: string } = {},
    signal?: AbortSignal,
  ): Promise<ExperimentListResponse> {
    return request<ExperimentListResponse>(
      `/experiments${toQuery({ limit: params.limit ?? 50, kind: params.kind })}`,
      { signal },
    );
  },

  getExperiment(
    experimentId: string,
    signal?: AbortSignal,
  ): Promise<ExperimentRecord> {
    return request<ExperimentRecord>(
      `/experiments/${encodeURIComponent(experimentId)}`,
      { signal },
    );
  },

  getExperimentTrials(
    experimentId: string,
    signal?: AbortSignal,
  ): Promise<TrialListResponse> {
    return request<TrialListResponse>(
      `/experiments/${encodeURIComponent(experimentId)}/trials`,
      { signal },
    );
  },

  getExperimentArtifacts(
    experimentId: string,
    signal?: AbortSignal,
  ): Promise<ArtifactListResponse> {
    return request<ArtifactListResponse>(
      `/experiments/${encodeURIComponent(experimentId)}/artifacts`,
      { signal },
    );
  },

  /* ---------------------------------------------------------------- */
  /* Plugins, exports                                                 */
  /* ---------------------------------------------------------------- */

  getPlugins(signal?: AbortSignal): Promise<PluginListResponse> {
    return request<PluginListResponse>("/plugins", { signal });
  },

  getExports(signal?: AbortSignal): Promise<ArtifactListResponse> {
    return request<ArtifactListResponse>("/exports", { signal });
  },

  /* ---------------------------------------------------------------- */
  /* Security & settings                                              */
  /* ---------------------------------------------------------------- */

  getSecurityPolicy(signal?: AbortSignal): Promise<SecurityPolicy> {
    return request<SecurityPolicy>("/security/policy", { signal });
  },

  getAuditEvents(
    limit = 100,
    signal?: AbortSignal,
  ): Promise<EventListResponse> {
    return request<EventListResponse>(
      `/security/audit${toQuery({ limit })}`,
      { signal },
    );
  },

  getFindings(signal?: AbortSignal): Promise<FindingsResponse> {
    return request<FindingsResponse>("/security/findings", { signal });
  },

  getSbom(signal?: AbortSignal): Promise<SbomDocument> {
    return request<SbomDocument>("/security/sbom", { signal });
  },

  getSettings(signal?: AbortSignal): Promise<PlatformSettings> {
    return request<PlatformSettings>("/settings", { signal });
  },

  getEvents(limit = 100, signal?: AbortSignal): Promise<EventListResponse> {
    return request<EventListResponse>(`/events${toQuery({ limit })}`, { signal });
  },

  /** Convenience for the Settings connection test. */
  testConnection(signal?: AbortSignal): Promise<PlatformSettings> {
    return request<PlatformSettings>("/settings", {
      signal,
      timeoutMs: HEALTH_TIMEOUT_MS,
    });
  },
};

export type SuiteInfo = BenchmarkSuiteInfo;
export type Engine = EngineStatus;
