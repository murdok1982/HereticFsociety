import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type {
  ArtifactListResponse,
  ArtifactRecord,
  BackendSpec,
  BenchmarkSuiteInfo,
  CompareRequest,
  ComparisonResult,
  EvaluationJobRequest,
  ExperimentRecord,
  MetricDelta,
} from "../api/types";
import { isTerminalJobStatus } from "../api/types";
import { useApiQuery, type ApiQueryResult } from "../hooks/useApiQuery";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useJobPoll } from "../hooks/useJobPoll";
import { useMutation } from "../hooks/useMutation";
import { useNow } from "../hooks/useNow";
import {
  formatDateTime,
  formatDuration,
  formatNumber,
  formatRelativeDelta,
  formatRelativeTime,
  formatValue,
  isRecord,
  pluralize,
  shortHash,
} from "../lib/format";
import type { Tone } from "../lib/status";
import { ArtifactTable } from "../components/ui/ArtifactTable";
import { AsyncState } from "../components/ui/AsyncState";
import { Badge, StatusBadge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { DynamicTable } from "../components/ui/DynamicTable";
import { EmptyState } from "../components/ui/EmptyState";
import { SelectField, TextField } from "../components/ui/FormField";
import { InlineAlert } from "../components/ui/InlineAlert";
import { JobCard } from "../components/ui/JobCard";
import { JsonView, KeyValueGrid } from "../components/ui/JsonView";
import { PageHeader, Panel } from "../components/ui/Panel";
import { TableSkeleton } from "../components/ui/Skeleton";
import { TableWrap } from "../components/ui/TableWrap";

interface RunFieldErrors {
  suite?: string;
  model?: string;
  path?: string;
  seed?: string;
  maxTokens?: string;
  temperature?: string;
  limit?: string;
}

function suiteTone(suite: BenchmarkSuiteInfo): Tone {
  if (suite.item_count === 0) return "warning";
  if (suite.limitations.length > 0) return "info";
  return "success";
}

function SuiteCard({ suite }: { suite: BenchmarkSuiteInfo }) {
  return (
    <article className="suite-card">
      <header className="suite-card-head">
        <div>
          <h3 className="suite-name">{suite.name}</h3>
          <p className="mono small muted">
            {suite.suite_id} · v{suite.version}
          </p>
        </div>
        <Badge tone={suiteTone(suite)}>{suite.grader}</Badge>
      </header>
      <p className="suite-description">{suite.description}</p>
      <dl className="inline-meta">
        <div>
          <dt>Items</dt>
          <dd className="mono">{formatNumber(suite.item_count)}</dd>
        </div>
        <div>
          <dt>Language</dt>
          <dd className="mono">{suite.language}</dd>
        </div>
        <div>
          <dt>SHA-256</dt>
          <dd className="mono" title={suite.sha256}>
            {shortHash(suite.sha256, 14)}
          </dd>
        </div>
      </dl>
      {suite.tags.length > 0 ? (
        <div className="badge-row">
          {suite.tags.map((tag) => (
            <Badge tone="neutral" key={tag}>
              {tag}
            </Badge>
          ))}
        </div>
      ) : null}
      <details className="details-block">
        <summary>Provenance &amp; limitations</summary>
        <div className="suite-details">
          <div>
            <p className="subheading">Provenance</p>
            <p className="small">
              {suite.provenance || "The backend did not report provenance."}
            </p>
          </div>
          <div>
            <p className="subheading">Limitations</p>
            {suite.limitations.length === 0 ? (
              <p className="small muted">No limitations reported.</p>
            ) : (
              <ul className="plain-list small">
                {suite.limitations.map((limitation) => (
                  <li key={limitation}>{limitation}</li>
                ))}
              </ul>
            )}
          </div>
          <div>
            <p className="subheading">Categories</p>
            {suite.categories.length === 0 ? (
              <p className="small muted">No categories reported.</p>
            ) : (
              <div className="badge-row">
                {suite.categories.map((category) => (
                  <Badge tone="neutral" key={category}>
                    {category}
                  </Badge>
                ))}
              </div>
            )}
          </div>
        </div>
      </details>
    </article>
  );
}

function recordRows(value: unknown, labelKey: string): Array<Record<string, unknown>> {
  if (Array.isArray(value)) {
    return value.filter(isRecord);
  }
  if (isRecord(value)) {
    const entries = Object.entries(value);
    if (entries.length > 0 && entries.every(([, entry]) => isRecord(entry))) {
      return entries.map(([key, entry]) => ({
        [labelKey]: key,
        ...(entry as Record<string, unknown>),
      }));
    }
    return [value];
  }
  return [];
}

function findSection(
  sources: Array<Record<string, unknown>>,
  keys: string[],
  labelKey: string,
): Array<Record<string, unknown>> {
  for (const source of sources) {
    for (const key of keys) {
      const rows = recordRows(source[key], labelKey);
      if (rows.length > 0) return rows;
    }
  }
  return [];
}

function metricCards(metrics: Record<string, unknown>): Array<[string, unknown]> {
  return Object.entries(metrics).filter(
    ([, value]) =>
      typeof value === "number" ||
      typeof value === "string" ||
      typeof value === "boolean",
  );
}

function ReportView({
  experiment,
  artifacts,
  artifactsQuery,
}: {
  experiment: ExperimentRecord;
  artifacts: ArtifactRecord[];
  artifactsQuery: ApiQueryResult<ArtifactListResponse>;
}) {
  const sources: Array<Record<string, unknown>> = [
    experiment.metrics,
    ...artifacts.map((artifact) => artifact.metadata ?? {}),
  ];
  const rawWarnings = experiment.metrics["warnings"];
  const warnings = Array.isArray(rawWarnings)
    ? rawWarnings.filter((entry): entry is string => typeof entry === "string")
    : [];
  const reportIds = Array.from(
    new Set(
      artifacts
        .map((artifact) => artifact.metadata?.["report_id"])
        .filter((value): value is string => typeof value === "string"),
    ),
  );
  const categoryRows = findSection(
    sources,
    ["category_metrics", "per_category", "categories"],
    "category",
  );
  const itemRows = findSection(
    sources,
    ["items", "results", "per_item", "samples"],
    "item",
  );
  const primitiveMetrics = metricCards(experiment.metrics);
  const nestedMetrics = Object.entries(experiment.metrics).filter(
    ([, value]) => isRecord(value) || Array.isArray(value),
  );

  return (
    <div className="report-view">
      <div className="badge-row">
        <StatusBadge status={experiment.status} />
        <Badge tone="neutral" mono>
          {experiment.kind}
        </Badge>
        {experiment.suite_hash ? (
          <Badge tone="neutral" mono title={experiment.suite_hash}>
            suite {shortHash(experiment.suite_hash, 12)}
          </Badge>
        ) : null}
        {experiment.model_id ? (
          <Badge tone="info" mono>
            {experiment.model_id}
          </Badge>
        ) : null}
        <Link
          className="btn btn--ghost btn--sm"
          to={`/experiments/${encodeURIComponent(experiment.experiment_id)}`}
        >
          <span>Open experiment</span>
        </Link>
      </div>

      {experiment.error ? (
        <InlineAlert tone="danger" title="Experiment reported an error">
          {experiment.error}
        </InlineAlert>
      ) : null}

      {warnings.length > 0 ? (
        <InlineAlert tone="warning" title="Evaluation warnings">
          <ul className="plain-list">
            {warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </InlineAlert>
      ) : null}

      <KeyValueGrid
        columns={3}
        data={{
          experiment_id: experiment.experiment_id,
          seed: experiment.seed,
          config_hash: experiment.config_hash,
          suite_hash: experiment.suite_hash,
          hardware_fingerprint: experiment.hardware_fingerprint,
          created_at: formatDateTime(experiment.created_at),
          finished_at: experiment.finished_at
            ? formatDateTime(experiment.finished_at)
            : null,
        }}
      />

      <section className="drawer-section">
        <h3 className="subheading">Metrics</h3>
        {primitiveMetrics.length === 0 ? (
          <p className="muted small">No scalar metrics recorded for this experiment.</p>
        ) : (
          <div className="metric-grid">
            {primitiveMetrics.map(([key, value]) => (
              <div className="metric-tile" key={key}>
                <span className="metric-label">{key.replace(/_/g, " ")}</span>
                <span className="metric-value mono">{formatValue(value)}</span>
              </div>
            ))}
          </div>
        )}
        {nestedMetrics.map(([key, value]) => (
          <details className="details-block" key={key}>
            <summary>{key.replace(/_/g, " ")}</summary>
            {Array.isArray(value) ? (
              <JsonView value={value} maxHeight={260} />
            ) : isRecord(value) ? (
              <DynamicTable rows={recordRows(value, "group")} maxRows={50} />
            ) : (
              <JsonView value={value} />
            )}
          </details>
        ))}
      </section>

      <section className="drawer-section">
        <h3 className="subheading">Per-category results</h3>
        {categoryRows.length === 0 ? (
          <p className="muted small">
            No per-category table was present in the experiment metrics or artifact
            metadata. The raw experiment JSON below remains the authoritative record.
          </p>
        ) : (
          <DynamicTable
            rows={categoryRows}
            caption="Per-category metrics"
            maxRows={50}
          />
        )}
      </section>

      <section className="drawer-section">
        <h3 className="subheading">Per-item results</h3>
        {itemRows.length === 0 ? (
          <p className="muted small">
            Per-item results were not included in the artifact metadata. Use the
            artifact path from the table below to open the JSON report on the host.
          </p>
        ) : (
          <DynamicTable rows={itemRows} caption="Per-item results" maxRows={100} />
        )}
      </section>

      <section className="drawer-section">
        <h3 className="subheading">Artifacts</h3>
        {artifactsQuery.error ? (
          <InlineAlert
            tone="warning"
            title="Artifacts could not be listed"
            action={
              <Button
                variant="ghost"
                size="sm"
                icon="refresh"
                onClick={artifactsQuery.reload}
              >
                Retry
              </Button>
            }
          >
            {artifactsQuery.error.message}
          </InlineAlert>
        ) : artifacts.length === 0 ? (
          <EmptyState
            title="No artifacts stored"
            description="The experiment has no report artifacts yet. The raw experiment JSON below is still available."
            icon="exports"
            compact
          />
        ) : (
          <>
            {reportIds.length > 0 ? (
              <p className="small muted">
                Report {pluralize(reportIds.length, "id")} recorded in artifact metadata:{" "}
                <span className="mono">{reportIds.join(", ")}</span>
              </p>
            ) : null}
            <ArtifactTable items={artifacts} />
          </>
        )}
      </section>

      <details className="details-block">
        <summary>Raw experiment JSON</summary>
        <JsonView value={experiment} maxHeight={360} />
      </details>
    </div>
  );
}

function DeltaTable({ deltas }: { deltas: MetricDelta[] }) {
  if (deltas.length === 0) {
    return <p className="muted small">No metric deltas were returned.</p>;
  }
  return (
    <TableWrap label="Metric deltas">
      <table className="data-table data-table--compact data-table--stack">
        <caption className="sr-only">Metric deltas</caption>
        <thead>
          <tr>
            <th scope="col">Metric</th>
            <th scope="col">Baseline</th>
            <th scope="col">Candidate</th>
            <th scope="col">Delta</th>
            <th scope="col">Relative</th>
            <th scope="col">Direction</th>
            <th scope="col">Verdict</th>
          </tr>
        </thead>
        <tbody>
          {deltas.map((delta) => (
            <tr key={delta.metric}>
              <td className="mono" data-label="Metric">{delta.metric}</td>
              <td className="mono" data-label="Baseline">{formatValue(delta.baseline)}</td>
              <td className="mono" data-label="Candidate">{formatValue(delta.candidate)}</td>
              <td className="mono" data-label="Delta">{formatValue(delta.delta)}</td>
              <td className="mono" data-label="Relative">{formatRelativeDelta(delta.relative_delta)}</td>
              <td className="small" data-label="Direction">{delta.direction.replace(/_/g, " ")}</td>
              <td data-label="Verdict">
                <Badge tone={delta.regressed ? "danger" : "success"}>
                  {delta.regressed ? "regressed" : "ok"}
                </Badge>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}

function ComparisonView({ result }: { result: ComparisonResult }) {
  const verdictTone: Tone =
    result.verdict === "improved"
      ? "success"
      : result.verdict === "regressed"
        ? "danger"
        : result.verdict === "equivalent"
          ? "neutral"
          : "warning";
  const reasons = result.incomparability_reasons ?? [];

  return (
    <div className="comparison-view">
      <div className="badge-row">
        <Badge tone={result.comparable ? verdictTone : "warning"}>
          {result.comparable ? result.verdict : "incomparable"}
        </Badge>
        <Badge tone="neutral" mono>
          threshold {(result.threshold * 100).toFixed(1)}%
        </Badge>
        <span className="small mono muted">
          {result.baseline_report_id} → {result.candidate_report_id}
        </span>
      </div>

      {!result.comparable ? (
        <InlineAlert tone="danger" title="Reports are not comparable">
          {reasons.length === 0 ? (
            <p>The comparison endpoint refused equivalence without detailing why.</p>
          ) : (
            <ul className="plain-list">
              {reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          )}
        </InlineAlert>
      ) : null}

      {result.warnings.length > 0 ? (
        <InlineAlert tone="warning" title="Comparison warnings">
          <ul className="plain-list">
            {result.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </InlineAlert>
      ) : null}

      <DeltaTable deltas={result.metric_deltas ?? []} />

      <div className="grid grid-2">
        <div>
          <h4 className="subheading">Improved items</h4>
          {result.improved_items.length === 0 ? (
            <p className="muted small">None reported.</p>
          ) : (
            <ul className="plain-list mono small">
              {result.improved_items.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          )}
        </div>
        <div>
          <h4 className="subheading">Regressed items</h4>
          {result.regressed_items.length === 0 ? (
            <p className="muted small">None reported.</p>
          ) : (
            <ul className="plain-list mono small">
              {result.regressed_items.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <details className="details-block">
        <summary>Raw comparison JSON</summary>
        <JsonView value={result} maxHeight={320} />
      </details>
    </div>
  );
}

export function EvaluationPage() {
  useDocumentTitle("Evaluation Center");
  const now = useNow(15_000);
  const suites = useApiQuery((signal) => api.getSuites(signal), []);
  const [suiteId, setSuiteId] = useState("");
  const [backendKind, setBackendKind] = useState<"transformers" | "llama_cpp">(
    "transformers",
  );
  const [modelId, setModelId] = useState("");
  const [modelPath, setModelPath] = useState("");
  const [seed, setSeed] = useState("");
  const [maxNewTokens, setMaxNewTokens] = useState("512");
  const [temperature, setTemperature] = useState("0.7");
  const [limit, setLimit] = useState("");
  const [fieldErrors, setFieldErrors] = useState<RunFieldErrors>({});
  const [jobId, setJobId] = useState<string | null>(null);
  const [reportInput, setReportInput] = useState("");
  const [reportExperimentId, setReportExperimentId] = useState<string | null>(null);
  const [reportFormError, setReportFormError] = useState<string | null>(null);
  const [baselineId, setBaselineId] = useState("");
  const [candidateId, setCandidateId] = useState("");
  const [compareError, setCompareError] = useState<string | null>(null);

  const submit = useMutation((body: EvaluationJobRequest, signal) =>
    api.createEvaluationJob(body, signal),
  );
  const job = useJobPoll(jobId);
  const cancel = useMutation((id: string, signal) => api.cancelJob(id, signal));
  const recent = useApiQuery(
    (signal) => api.getExperiments({ limit: 8, kind: "evaluation" }, signal),
    [],
    { pollMs: 15_000 },
  );
  const reportExperiment = useApiQuery(
    (signal) => api.getExperiment(reportExperimentId as string, signal),
    [reportExperimentId],
    { enabled: Boolean(reportExperimentId), keepPreviousData: false },
  );
  const reportArtifacts = useApiQuery(
    (signal) => api.getExperimentArtifacts(reportExperimentId as string, signal),
    [reportExperimentId],
    { enabled: Boolean(reportExperimentId), keepPreviousData: false },
  );
  const compare = useMutation((body: CompareRequest, signal) =>
    api.compareReports(body, signal),
  );
  const refreshedJob = useRef<string | null>(null);

  useEffect(() => {
    if (!suiteId && suites.data?.items[0]) {
      setSuiteId(suites.data.items[0].suite_id);
    }
  }, [suiteId, suites.data]);

  useEffect(() => {
    if (
      job.data &&
      isTerminalJobStatus(job.data.status) &&
      refreshedJob.current !== job.data.job_id
    ) {
      refreshedJob.current = job.data.job_id;
      recent.reload();
      if (job.data.status === "succeeded" && job.data.experiment_id) {
        setReportInput(job.data.experiment_id);
        setReportExperimentId(job.data.experiment_id);
      }
    }
  }, [job.data, recent]);

  const validateRun = (): RunFieldErrors => {
    const errors: RunFieldErrors = {};
    if (!suiteId) errors.suite = "Select a benchmark suite.";
    if (backendKind === "transformers" && !modelId.trim()) {
      errors.model = "A model id or local directory is required.";
    }
    if (backendKind === "llama_cpp" && !modelPath.trim()) {
      errors.path = "A GGUF file path inside the workspace is required.";
    }
    if (seed.trim() !== "") {
      const value = Number(seed);
      if (!Number.isInteger(value) || value < 0 || value > 2_147_483_647) {
        errors.seed = "Seed must be an integer between 0 and 2^31-1.";
      }
    }
    const tokens = Number(maxNewTokens);
    if (!Number.isInteger(tokens) || tokens < 1 || tokens > 8192) {
      errors.maxTokens = "Use an integer between 1 and 8192.";
    }
    const temp = Number(temperature);
    if (!Number.isFinite(temp) || temp < 0 || temp > 2) {
      errors.temperature = "Temperature must be between 0.0 and 2.0.";
    }
    if (limit.trim() !== "") {
      const value = Number(limit);
      if (!Number.isInteger(value) || value < 1 || value > 5000) {
        errors.limit = "Limit must be an integer between 1 and 5000.";
      }
    }
    return errors;
  };

  const runEvaluation = async (): Promise<void> => {
    const errors = validateRun();
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;

    const backend: BackendSpec =
      backendKind === "transformers"
        ? { kind: "transformers", model_id: modelId.trim() }
        : { kind: "llama_cpp", path: modelPath.trim() };
    const body: EvaluationJobRequest = {
      suite_id: suiteId,
      backend,
      max_new_tokens: Number(maxNewTokens),
      temperature: Number(temperature),
    };
    if (seed.trim() !== "") body.seed = Number(seed);
    if (limit.trim() !== "") body.limit = Number(limit);

    const record = await submit.run(body);
    if (record) setJobId(record.job_id);
  };

  const loadReport = (explicitId?: string): void => {
    const value = (explicitId ?? reportInput).trim();
    if (!value) {
      setReportFormError("Enter an evaluation experiment id.");
      return;
    }
    setReportFormError(null);
    setReportInput(value);
    setReportExperimentId(value);
  };

  const runCompare = async (): Promise<void> => {
    const baseline = baselineId.trim();
    const candidate = candidateId.trim();
    if (!baseline || !candidate) {
      setCompareError("Both baseline and candidate report ids are required.");
      return;
    }
    if (baseline === candidate) {
      setCompareError("Choose two different report ids.");
      return;
    }
    setCompareError(null);
    await compare.run({
      baseline_report_id: baseline,
      candidate_report_id: candidate,
    });
  };

  const artifactItems = reportArtifacts.data?.items ?? [];

  return (
    <>
      <PageHeader
        eyebrow="Benchmarking"
        title="Evaluation Center"
        description="Run reproducible, provenance-stamped benchmark suites against a real backend, inspect stored reports and compare candidates against a baseline."
        actions={
          <Link className="btn btn--secondary btn--md" to="/experiments?kind=evaluation">
            <span>Evaluation history</span>
          </Link>
        }
      />

      <AsyncState
        query={suites}
        skeleton={<TableSkeleton rows={5} />}
        context="Benchmark suites"
        isEmpty={(data) => data.items.length === 0}
        empty={
          <EmptyState
            title="No benchmark suites registered"
            description="The API has no suites available. Install the bundled suites or check the backend configuration."
            icon="evaluation"
          />
        }
      >
        {(suiteData) => (
          <div className="grid grid-2 evaluation-top">
            <Panel
              title="Benchmark suites"
              subtitle={`${pluralize(suiteData.items.length, "suite")} available`}
            >
              <div className="suite-list">
                {suiteData.items.map((suite) => (
                  <SuiteCard suite={suite} key={suite.suite_id} />
                ))}
              </div>
            </Panel>

            <Panel
              title="Run evaluation"
              subtitle="Submits a background job; progress is polled until completion"
            >
              <div className="form-grid form-grid--2">
                <SelectField
                  label="Suite"
                  required
                  value={suiteId}
                  onChange={(event) => setSuiteId(event.target.value)}
                  error={fieldErrors.suite}
                >
                  <option value="">Select a suite…</option>
                  {suiteData.items.map((suite) => (
                    <option value={suite.suite_id} key={suite.suite_id}>
                      {suite.name} · v{suite.version}
                    </option>
                  ))}
                </SelectField>
                <SelectField
                  label="Backend"
                  value={backendKind}
                  onChange={(event) =>
                    setBackendKind(event.target.value as "transformers" | "llama_cpp")
                  }
                >
                  <option value="transformers">transformers</option>
                  <option value="llama_cpp">llama_cpp</option>
                </SelectField>
              </div>

              {backendKind === "transformers" ? (
                <TextField
                  label="Model id or local directory"
                  required
                  value={modelId}
                  onChange={(event) => setModelId(event.target.value)}
                  placeholder="org/model-name or /workspace/models/model"
                  error={fieldErrors.model}
                  hint="The backend resolves this reference through the model manager."
                />
              ) : (
                <TextField
                  label="GGUF path"
                  required
                  value={modelPath}
                  onChange={(event) => setModelPath(event.target.value)}
                  placeholder="models/gguf/model.Q4_K_M.gguf"
                  error={fieldErrors.path}
                  hint="Path must resolve inside the API workspace."
                />
              )}

              <div className="form-grid form-grid--2">
                <TextField
                  label="Seed (optional)"
                  type="number"
                  min={0}
                  step={1}
                  value={seed}
                  onChange={(event) => setSeed(event.target.value)}
                  error={fieldErrors.seed}
                />
                <TextField
                  label="Max new tokens"
                  type="number"
                  min={1}
                  max={8192}
                  step={1}
                  required
                  value={maxNewTokens}
                  onChange={(event) => setMaxNewTokens(event.target.value)}
                  error={fieldErrors.maxTokens}
                />
                <TextField
                  label="Temperature"
                  type="number"
                  min={0}
                  max={2}
                  step={0.05}
                  required
                  value={temperature}
                  onChange={(event) => setTemperature(event.target.value)}
                  error={fieldErrors.temperature}
                />
                <TextField
                  label="Item limit (optional)"
                  type="number"
                  min={1}
                  max={5000}
                  step={1}
                  value={limit}
                  onChange={(event) => setLimit(event.target.value)}
                  error={fieldErrors.limit}
                  hint="Blank runs the full suite."
                />
              </div>

              {submit.error ? (
                <InlineAlert tone="danger" title={`Submission failed · ${submit.error.code}`}>
                  <p>{submit.error.message}</p>
                  {submit.error.hint ? <p className="small">{submit.error.hint}</p> : null}
                  {submit.error.details !== undefined ? (
                    <details className="details-block">
                      <summary>Server details</summary>
                      <JsonView value={submit.error.details} maxHeight={220} />
                    </details>
                  ) : null}
                </InlineAlert>
              ) : null}

              <div className="form-actions form-actions--end">
                <Button
                  variant="primary"
                  icon="evaluation"
                  loading={submit.pending}
                  onClick={() => void runEvaluation()}
                >
                  Start evaluation
                </Button>
              </div>
            </Panel>
          </div>
        )}
      </AsyncState>

      <Panel
        title="Active evaluation job"
        subtitle="Progress is reported by the backend; status is announced to assistive technology"
        actions={
          jobId ? (
            <Button
              variant="ghost"
              size="sm"
              icon="refresh"
              onClick={job.reload}
              loading={job.refreshing}
            >
              Refresh
            </Button>
          ) : undefined
        }
      >
        {!jobId ? (
          <EmptyState
            title="No evaluation job running"
            description="Choose a suite and a real backend above. Submitted jobs appear here with live progress and cancellation controls."
            icon="evaluation"
            compact
          />
        ) : (
          <AsyncState query={job} skeleton={<TableSkeleton rows={3} />} context="Evaluation job">
            {(record) => (
              <>
                <JobCard
                  job={record}
                  onCancel={(id) => void cancel.run(id).then(() => job.reload())}
                  cancelPending={cancel.pending}
                />
                {cancel.error ? (
                  <InlineAlert tone="warning" title="Cancellation failed">
                    {cancel.error.message}
                  </InlineAlert>
                ) : null}
                {record.status === "succeeded" && record.experiment_id ? (
                  <InlineAlert
                    tone="success"
                    title="Evaluation finished"
                    action={
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => loadReport(record.experiment_id ?? "")}
                      >
                        Load report below
                      </Button>
                    }
                  >
                    Report stored under experiment{" "}
                    <code>{record.experiment_id}</code>.
                  </InlineAlert>
                ) : null}
              </>
            )}
          </AsyncState>
        )}
      </Panel>

      <Panel
        title="Report viewer"
        subtitle="Reads the persisted experiment, its metrics and artifact metadata"
      >
        <div className="inline-form">
          <TextField
            label="Evaluation experiment id"
            value={reportInput}
            onChange={(event) => setReportInput(event.target.value)}
            placeholder="exp_…"
            list="evaluation-report-ids"
            error={reportFormError ?? undefined}
          />
          <datalist id="evaluation-report-ids">
            {(recent.data?.items ?? []).map((experiment) => (
              <option
                value={experiment.experiment_id}
                key={experiment.experiment_id}
              >
                {experiment.name}
              </option>
            ))}
          </datalist>
          <Button
            variant="primary"
            icon="evaluation"
            onClick={() => loadReport()}
            disabled={!reportExperimentId && !reportInput.trim()}
          >
            Load report
          </Button>
          {reportExperimentId ? (
            <Button
              variant="ghost"
              icon="close"
              onClick={() => {
                setReportExperimentId(null);
                setReportInput("");
              }}
            >
              Clear
            </Button>
          ) : null}
        </div>

        {!reportExperimentId ? (
          <EmptyState
            title="No report loaded"
            description="Paste an evaluation experiment id, or pick one from the recent runs table. The viewer renders stored metrics and any per-category or per-item tables found in artifact metadata."
            icon="evaluation"
            compact
          />
        ) : (
          <AsyncState
            query={reportExperiment}
            skeleton={<TableSkeleton rows={5} />}
            context="Stored experiment"
          >
            {(experiment: ExperimentRecord) => (
              <ReportView
                experiment={experiment}
                artifacts={artifactItems}
                artifactsQuery={reportArtifacts}
              />
            )}
          </AsyncState>
        )}
      </Panel>

      <Panel
        title="Compare reports"
        subtitle="The API refuses to mark materially different conditions comparable"
      >
        <div className="form-grid form-grid--3">
          <TextField
            label="Baseline report id"
            value={baselineId}
            onChange={(event) => setBaselineId(event.target.value)}
            placeholder="eval_…"
            hint="Use the report id recorded in artifact metadata."
          />
          <TextField
            label="Candidate report id"
            value={candidateId}
            onChange={(event) => setCandidateId(event.target.value)}
            placeholder="eval_…"
          />
          <div className="field-align-end">
            <Button
              variant="secondary"
              icon="evaluation"
              loading={compare.pending}
              onClick={() => void runCompare()}
            >
              Compare
            </Button>
          </div>
        </div>

        {compareError ? (
          <InlineAlert tone="warning" title="Comparison not submitted">
            {compareError}
          </InlineAlert>
        ) : null}

        {compare.error ? (
          <InlineAlert tone="danger" title={`Comparison failed · ${compare.error.code}`}>
            <p>{compare.error.message}</p>
            {compare.error.hint ? <p className="small">{compare.error.hint}</p> : null}
          </InlineAlert>
        ) : null}

        {compare.data ? <ComparisonView result={compare.data} /> : null}
      </Panel>

      <Panel
        title="Recent evaluation runs"
        subtitle="Persisted experiments of kind evaluation"
        flush
        actions={
          <Button
            variant="ghost"
            size="sm"
            icon="refresh"
            onClick={recent.reload}
            aria-label="Refresh evaluation history"
          />
        }
      >
        <AsyncState
          query={recent}
          skeleton={<TableSkeleton rows={4} />}
          isEmpty={(data) => data.items.length === 0}
          empty={
            <EmptyState
              title="No evaluation experiments recorded"
              description="Run a suite above to create the first reproducible evaluation record."
              icon="evaluation"
              compact
            />
          }
        >
          {(data) => (
            <TableWrap label="Evaluation history">
              <table className="data-table data-table--stack">
                <caption className="sr-only">Recent evaluation runs</caption>
                <thead>
                  <tr>
                    <th scope="col">Experiment</th>
                    <th scope="col">Status</th>
                    <th scope="col">Model</th>
                    <th scope="col">Seed</th>
                    <th scope="col">Created</th>
                    <th scope="col">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((experiment) => (
                    <tr key={experiment.experiment_id}>
                      <td data-label="Experiment">
                        <Link
                          to={`/experiments/${encodeURIComponent(experiment.experiment_id)}`}
                          className="cell-primary"
                        >
                          {experiment.name}
                        </Link>
                        <div className="cell-sub mono">{experiment.experiment_id}</div>
                      </td>
                      <td data-label="Status">
                        <StatusBadge status={experiment.status} />
                      </td>
                      <td className="mono small" data-label="Model">{experiment.model_id ?? "—"}</td>
                      <td className="mono" data-label="Seed">{experiment.seed ?? "—"}</td>
                      <td data-label="Created" title={formatDateTime(experiment.created_at)}>
                        {formatRelativeTime(experiment.created_at, now)}
                        {experiment.finished_at && experiment.started_at ? (
                          <div className="cell-sub">
                            {formatDuration(
                              (new Date(experiment.finished_at).getTime() -
                                new Date(experiment.started_at).getTime()) /
                                1000,
                            )}
                          </div>
                        ) : null}
                      </td>
                      <td className="cell-actions" data-label="Actions">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => loadReport(experiment.experiment_id)}
                          aria-label={`View report for ${experiment.experiment_id}`}
                        >
                          View report
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          )}
        </AsyncState>
      </Panel>

      <div className="page-footnote">
        <span>
          Report JSON is not fetched through an undocumented endpoint. Metrics,
          per-category and per-item tables come from the experiment record and
          artifact metadata exposed by the documented API.
        </span>
      </div>
    </>
  );
}
