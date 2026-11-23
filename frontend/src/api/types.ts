/**
 * Typed contracts for the FSOCIETY AI HTTP API (v1).
 *
 * These mirror the server's Pydantic models. Every field that the API may
 * legitimately omit is nullable so the UI can render "unknown" instead of
 * inventing a value.
 */

export type Confidence = "measured" | "reported" | "estimated" | "unknown";
export type SourceKind =
  | "measured"
  | "reported"
  | "estimated"
  | "declared"
  | "unknown";

/* ------------------------------------------------------------------ */
/* System                                                              */
/* ------------------------------------------------------------------ */

export interface HealthResponse {
  status: "ok" | "degraded";
  version: string;
  uptime_seconds: number | null;
  checks: Record<string, string>;
}

export interface SystemInfo {
  platform_name: string;
  version: string;
  plugin_api_version: number;
  python_version: string;
  engine_available: boolean;
  engine_version: string | null;
  api_version: string;
  started_at: string;
  workspace: string | null;
}

export interface EngineStatus {
  available: boolean;
  missing: string[];
  heretic_version: string | null;
  torch_version: string | null;
  transformers_version: string | null;
  message: string;
  hint?: string | null;
}

/* ------------------------------------------------------------------ */
/* Hardware                                                            */
/* ------------------------------------------------------------------ */

export interface GpuInfo {
  index: number;
  name: string;
  backend: string;
  total_memory_bytes: number | null;
  free_memory_bytes: number | null;
  compute_capability: string | null;
  driver_version: string | null;
  source: SourceKind;
  confidence: Confidence;
}

export interface CpuInfo {
  model: string | null;
  physical_cores: number | null;
  logical_cores: number | null;
  frequency_mhz: number | null;
  architecture: string | null;
  source: SourceKind;
}

export interface TorchInfo {
  installed: boolean;
  version: string | null;
  cuda_available: boolean;
  cuda_version: string | null;
  mps_available: boolean;
  device_count: number;
  dtype_support: string[];
  error: string | null;
}

export interface HardwareProfile {
  schema_version?: number;
  profile_id: string;
  captured_at: string;
  hostname: string;
  os: string;
  os_release: string;
  architecture: string;
  python_version: string;
  cpu: CpuInfo;
  ram_total_bytes: number | null;
  ram_available_bytes: number | null;
  swap_total_bytes: number | null;
  gpus: GpuInfo[];
  backends: Record<string, boolean>;
  torch: TorchInfo;
  detection_mode: "measured" | "partial";
  warnings: string[];
  fingerprint: string;
}

/* ------------------------------------------------------------------ */
/* Models                                                              */
/* ------------------------------------------------------------------ */

export type ModelFormat =
  | "safetensors"
  | "pytorch"
  | "gguf"
  | "mlx"
  | "unknown";

export type ModelSource =
  | "local"
  | "huggingface_cache"
  | "huggingface_hub"
  | "unknown";

export interface ModelFile {
  path: string;
  size_bytes: number;
  sha256: string | null;
  format: ModelFormat;
}

export interface ModelInfo {
  schema_version?: number;
  model_id: string;
  source: ModelSource;
  path: string | null;
  revision: string | null;
  commit: string | null;
  architecture: string | null;
  model_type: string | null;
  parameter_count: number | null;
  active_parameter_count: number | null;
  hidden_size: number | null;
  num_hidden_layers: number | null;
  num_attention_heads: number | null;
  num_key_value_heads: number | null;
  head_dim: number | null;
  intermediate_size: number | null;
  vocab_size: number | null;
  max_position_embeddings: number | null;
  torch_dtype: string | null;
  quantization: string | null;
  size_bytes: number | null;
  files: ModelFile[];
  formats: string[];
  has_chat_template: boolean;
  is_multimodal: boolean;
  is_mixture_of_experts: boolean;
  license: string | null;
  tags: string[];
  requires_remote_code: boolean;
  metadata_source: SourceKind;
  compatibility: string[];
  warnings: string[];
  inspected_at: string;
}

export interface ModelListResponse {
  items: ModelInfo[];
  total?: number;
  warnings?: string[];
}

export interface ScanRequest {
  roots: string[];
  include_hf_cache: boolean;
}

export interface VerifyRequest {
  reference: string;
  manifest_path?: string;
}

export interface ModelValidation {
  model_id: string;
  path: string | null;
  checked_at: string;
  files_checked: number;
  hashes_verified: number;
  mismatches: string[];
  missing: string[];
  ok: boolean;
  manifest_path: string | null;
  details: Record<string, unknown>;
}

export interface MemoryBreakdown {
  weights_bytes: number;
  kv_cache_bytes: number;
  activations_bytes: number;
  overhead_bytes: number;
}

export interface ResourceEstimate {
  schema_version?: number;
  kind: "estimate";
  model_id: string;
  parameter_count: number;
  dtype: string;
  quantization: string;
  batch_size: number;
  sequence_length: number;
  breakdown: MemoryBreakdown;
  fits: boolean | null;
  headroom_bytes: number | null;
  recommended_dtype: string | null;
  recommended_quantization: string | null;
  recommended_batch_size: number | null;
  recommended_device_map: string | Record<string, number | string> | null;
  alternatives: string[];
  assumptions: string[];
  confidence: Confidence;
  hardware_profile_id: string | null;
  created_at: string;
}

export interface EstimateResponse {
  model: ModelInfo;
  estimate: ResourceEstimate;
  hardware: HardwareProfile;
}

export interface EstimateParams {
  reference: string;
  batch_size: number;
  sequence_length: number;
  dtype: string;
  quantization: string;
}

/* ------------------------------------------------------------------ */
/* Jobs & experiments                                                  */
/* ------------------------------------------------------------------ */

export type JobStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface JobRecord {
  job_id: string;
  kind: string;
  status: JobStatus;
  experiment_id: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  progress: number;
  message: string;
  request: Record<string, unknown>;
  result: Record<string, unknown>;
  error: string | null;
  cancellable: boolean;
  cancel_requested: boolean;
}

export interface JobListResponse {
  items: JobRecord[];
  total?: number;
}

export type ExperimentKind =
  | "optimization"
  | "evaluation"
  | "modification"
  | "export"
  | "verification";

export interface ExperimentRecord {
  experiment_id: string;
  name: string;
  kind: ExperimentKind;
  status: JobStatus;
  model_id: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  params: Record<string, unknown>;
  metrics: Record<string, unknown>;
  tags: string[];
  seed: number | null;
  config_hash: string | null;
  suite_hash: string | null;
  hardware_fingerprint: string | null;
  software: Record<string, string>;
  error: string | null;
  notes: string | null;
}

export interface ExperimentListResponse {
  items: ExperimentRecord[];
  total?: number;
}

export interface TrialRecord {
  trial_id: string;
  experiment_id: string;
  number: number;
  state: string;
  params: Record<string, unknown>;
  values: number[];
  objective_names: string[];
  duration_seconds: number | null;
  created_at: string;
}

export interface TrialListResponse {
  items: TrialRecord[];
  total?: number;
}

export interface ArtifactRecord {
  artifact_id: string;
  experiment_id: string | null;
  kind: string;
  path: string;
  sha256: string;
  size_bytes: number;
  created_at: string;
  metadata: Record<string, unknown>;
}

export interface ArtifactListResponse {
  items: ArtifactRecord[];
  total?: number;
}

/* ------------------------------------------------------------------ */
/* Optimization                                                        */
/* ------------------------------------------------------------------ */

export type DimensionKind = "float" | "int" | "categorical";

export interface DimensionSpec {
  name: string;
  kind: DimensionKind;
  low?: number;
  high?: number;
  log?: boolean;
  step?: number;
  choices?: string[];
}

export interface OptimizationJobRequest {
  name: string;
  space: { dimensions: DimensionSpec[] };
  n_trials: number;
  seed?: number;
  sampler?: "tpe" | "random" | string;
  directions?: string[];
  /**
   * The public contract does not require a model, but the local planner can
   * pin the run to a model. Sent only when the operator supplies one.
   */
  model_id?: string;
}

/* ------------------------------------------------------------------ */
/* Evaluation                                                          */
/* ------------------------------------------------------------------ */

export interface BenchmarkSuiteInfo {
  schema_version?: number;
  suite_id: string;
  name: string;
  version: string;
  description: string;
  grader: string;
  language: string;
  categories: string[];
  item_count: number;
  sha256: string;
  tags: string[];
  source?: string;
  provenance: string;
  limitations: string[];
}

export interface SuiteListResponse {
  items: BenchmarkSuiteInfo[];
  total?: number;
}

export interface BackendSpec {
  kind: "transformers" | "llama_cpp";
  model_id?: string;
  path?: string;
  device?: string;
  dtype?: string;
  revision?: string;
  n_ctx?: number;
  n_gpu_layers?: number;
}

export interface EvaluationJobRequest {
  suite_id: string;
  backend: BackendSpec;
  seed?: number;
  max_new_tokens?: number;
  temperature?: number;
  limit?: number;
  batch_size?: number;
  system_prompt?: string;
}

export interface CompareRequest {
  baseline_report_id: string;
  candidate_report_id: string;
}

export interface MetricDelta {
  metric: string;
  baseline: number | null;
  candidate: number | null;
  delta: number | null;
  relative_delta: number | null;
  direction: "higher_is_better" | "lower_is_better" | "neutral";
  regressed: boolean;
}

export interface ComparisonResult {
  schema_version?: number;
  baseline_report_id: string;
  candidate_report_id: string;
  comparable: boolean;
  incomparability_reasons: string[];
  metric_deltas: MetricDelta[];
  regressed_items: string[];
  improved_items: string[];
  warnings: string[];
  verdict: "improved" | "regressed" | "mixed" | "equivalent" | "incomparable";
  threshold: number;
}

/* ------------------------------------------------------------------ */
/* Plugins, exports, security, settings                                */
/* ------------------------------------------------------------------ */

export interface PluginInfo {
  name: string;
  version: string;
  api_version: number;
  kind: string;
  class_name: string;
  module: string;
  path: string | null;
  description: string;
  author: string | null;
  license: string | null;
  dependencies: string[];
  permissions: string[];
  builtin: boolean;
  valid: boolean;
  errors: string[];
  warnings: string[];
}

export interface PluginListResponse {
  items: PluginInfo[];
  total?: number;
}

export interface SecurityPolicy {
  trust_remote_code: string;
  trusted_repositories: string[];
  plugin_isolation: boolean;
  plugin_timeout_seconds: number;
  plugin_allow_network: boolean;
  api_auth_required: boolean;
  api_key_configured: boolean;
  cors_origins: string[];
  warnings: string[];
}

export interface EventRecord {
  event_id: number | null;
  created_at: string;
  level: string;
  category: string;
  message: string;
  data: Record<string, unknown>;
}

export interface EventListResponse {
  items: EventRecord[];
  total?: number;
}

export interface SecurityFinding {
  severity: string;
  title: string;
  detail: string;
  status: string;
}

export interface FindingsResponse {
  items: SecurityFinding[];
  total?: number;
}

export interface SbomComponent {
  type?: string;
  name?: string;
  version?: string;
  purl?: string;
  licenses?: Array<{ license?: { id?: string; name?: string } }>;
  [key: string]: unknown;
}

export interface SbomDocument {
  bomFormat: string;
  specVersion: string;
  serialNumber: string;
  metadata: Record<string, unknown>;
  components: SbomComponent[];
}

export type PlatformSettings = Record<string, unknown>;

/* ------------------------------------------------------------------ */
/* Error shape                                                         */
/* ------------------------------------------------------------------ */

export interface ApiErrorEnvelope {
  error: {
    code: string;
    message: string;
    details?: unknown;
    hint?: string;
  };
}

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

const TERMINAL_JOB_STATUSES = new Set([
  "succeeded",
  "failed",
  "cancelled",
  "canceled",
  "completed",
  "error",
  "finished",
]);

export function isTerminalJobStatus(status: string | null | undefined): boolean {
  if (!status) return false;
  return TERMINAL_JOB_STATUSES.has(status.toLowerCase());
}

export function isJobActive(status: string | null | undefined): boolean {
  return status === "queued" || status === "running";
}
