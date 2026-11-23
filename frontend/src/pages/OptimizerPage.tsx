import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type {
  DimensionKind,
  DimensionSpec,
  ExperimentRecord,
  OptimizationJobRequest,
} from "../api/types";
import { isTerminalJobStatus } from "../api/types";
import { useApiQuery } from "../hooks/useApiQuery";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useJobPoll } from "../hooks/useJobPoll";
import { useMutation } from "../hooks/useMutation";
import { formatDateTime, formatNumber, formatRelativeTime } from "../lib/format";
import { useNow } from "../hooks/useNow";
import { EmptyState } from "../components/ui/EmptyState";
import { StatusBadge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { CheckboxField, SelectField, TextField } from "../components/ui/FormField";
import { InlineAlert } from "../components/ui/InlineAlert";
import { JobCard } from "../components/ui/JobCard";
import { JsonView } from "../components/ui/JsonView";
import { PageHeader, Panel } from "../components/ui/Panel";
import { TableSkeleton } from "../components/ui/Skeleton";
import { TableWrap } from "../components/ui/TableWrap";
import { AsyncState } from "../components/ui/AsyncState";
import { Icon } from "../components/ui/Icon";

interface DimensionDraft {
  id: string;
  name: string;
  kind: DimensionKind;
  low: string;
  high: string;
  log: boolean;
  step: string;
  choices: string;
}

interface DimensionFieldErrors {
  name?: string;
  low?: string;
  high?: string;
  step?: string;
  choices?: string;
}

interface ValidationResult {
  ok: boolean;
  formErrors: string[];
  dimensionErrors: Record<string, DimensionFieldErrors>;
  dimensions: DimensionSpec[];
}

let dimensionCounter = 0;

function createDimension(): DimensionDraft {
  dimensionCounter += 1;
  return {
    id: `dimension-${dimensionCounter}`,
    name: "",
    kind: "float",
    low: "",
    high: "",
    log: false,
    step: "",
    choices: "",
  };
}

function parseChoices(raw: string): string[] {
  const seen = new Set<string>();
  const values: string[] = [];
  for (const entry of raw.split(",")) {
    const value = entry.trim();
    if (value && !seen.has(value)) {
      seen.add(value);
      values.push(value);
    }
  }
  return values;
}

function validateDimension(
  dimension: DimensionDraft,
  allNames: string[],
): { errors: DimensionFieldErrors; spec: DimensionSpec | null } {
  const errors: DimensionFieldErrors = {};
  const name = dimension.name.trim();
  if (!name) {
    errors.name = "Name is required.";
  } else if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) {
    errors.name = "Use letters, digits and underscores; start with a letter or underscore.";
  } else if (name.length > 64) {
    errors.name = "Maximum 64 characters.";
  } else if (allNames.filter((candidate) => candidate === name).length > 1) {
    errors.name = "Dimension names must be unique.";
  }

  if (dimension.kind === "categorical") {
    const choices = parseChoices(dimension.choices);
    if (choices.length < 2) {
      errors.choices = "Provide at least two comma-separated choices.";
    } else if (choices.length > 64) {
      errors.choices = "Maximum 64 choices.";
    }
    if (Object.keys(errors).length > 0) {
      return { errors, spec: null };
    }
    return {
      errors,
      spec: { name, kind: "categorical", choices },
    };
  }

  const low = Number(dimension.low);
  const high = Number(dimension.high);
  if (dimension.low.trim() === "" || !Number.isFinite(low)) {
    errors.low = "Enter a finite lower bound.";
  }
  if (dimension.high.trim() === "" || !Number.isFinite(high)) {
    errors.high = "Enter a finite upper bound.";
  }
  if (!errors.low && !errors.high && low >= high) {
    errors.high = "Upper bound must be greater than the lower bound.";
  }
  if (dimension.kind === "int" && !errors.low && !Number.isInteger(low)) {
    errors.low = "Integer dimensions require an integer lower bound.";
  }
  if (dimension.kind === "int" && !errors.high && !Number.isInteger(high)) {
    errors.high = "Integer dimensions require an integer upper bound.";
  }
  if (dimension.log && !errors.low && low <= 0) {
    errors.low = "Log-scaled bounds must be positive.";
  }

  let step: number | undefined;
  if (dimension.step.trim() !== "") {
    step = Number(dimension.step);
    if (!Number.isFinite(step) || step <= 0) {
      errors.step = "Step must be a positive number.";
    } else if (dimension.kind === "int" && !Number.isInteger(step)) {
      errors.step = "Integer dimensions need an integer step.";
    }
  }

  if (Object.keys(errors).length > 0) {
    return { errors, spec: null };
  }

  const spec: DimensionSpec = {
    name,
    kind: dimension.kind,
    low,
    high,
    log: dimension.log,
  };
  if (step !== undefined) spec.step = step;
  return { errors, spec };
}

export function OptimizerPage() {
  useDocumentTitle("Optimizer");
  const now = useNow(15_000);
  const [name, setName] = useState("");
  const [modelId, setModelId] = useState("");
  const [nTrials, setNTrials] = useState("20");
  const [seed, setSeed] = useState("");
  const [sampler, setSampler] = useState<"tpe" | "random">("tpe");
  const [dimensions, setDimensions] = useState<DimensionDraft[]>([createDimension()]);
  const [formErrors, setFormErrors] = useState<string[]>([]);
  const [fieldErrors, setFieldErrors] = useState<{
    name?: string;
    modelId?: string;
    nTrials?: string;
    seed?: string;
  }>({});
  const [dimensionErrors, setDimensionErrors] = useState<
    Record<string, DimensionFieldErrors>
  >({});
  const [jobId, setJobId] = useState<string | null>(null);

  const models = useApiQuery((signal) => api.getModels(true, signal), []);
  const submit = useMutation((body: OptimizationJobRequest, signal) =>
    api.createOptimizationJob(body, signal),
  );
  const job = useJobPoll(jobId);
  const cancel = useMutation((id: string, signal) => api.cancelJob(id, signal));
  const recent = useApiQuery(
    (signal) => api.getExperiments({ limit: 8, kind: "optimization" }, signal),
    [],
    { pollMs: 15_000 },
  );
  const refreshedJob = useRef<string | null>(null);

  useEffect(() => {
    if (
      job.data &&
      isTerminalJobStatus(job.data.status) &&
      refreshedJob.current !== job.data.job_id
    ) {
      refreshedJob.current = job.data.job_id;
      recent.reload();
    }
  }, [job.data, recent]);

  const updateDimension = (id: string, patch: Partial<DimensionDraft>): void => {
    setDimensions((current) =>
      current.map((dimension) =>
        dimension.id === id ? { ...dimension, ...patch } : dimension,
      ),
    );
  };

  const removeDimension = (id: string): void => {
    setDimensions((current) =>
      current.length <= 1 ? current : current.filter((dimension) => dimension.id !== id),
    );
    setDimensionErrors((current) => {
      const next = { ...current };
      delete next[id];
      return next;
    });
  };

  const validate = (): ValidationResult => {
    const errors: string[] = [];
    const fields: {
      name?: string;
      modelId?: string;
      nTrials?: string;
      seed?: string;
    } = {};
    const dimFieldErrors: Record<string, DimensionFieldErrors> = {};
    const trimmedName = name.trim();

    if (!trimmedName) {
      fields.name = "A sweep name is required.";
      errors.push("Sweep name is required.");
    } else if (trimmedName.length > 120) {
      fields.name = "Maximum 120 characters.";
      errors.push("Sweep name is too long.");
    }

    if (!modelId.trim()) {
      fields.modelId = "A model reference is required for resource planning.";
      errors.push("Select or enter the model to plan for.");
    }

    const trials = Number(nTrials);
    if (!Number.isInteger(trials) || trials < 1 || trials > 500) {
      fields.nTrials = "Choose between 1 and 500 trials.";
      errors.push("Trial count must be an integer between 1 and 500.");
    }

    let parsedSeed: number | undefined;
    if (seed.trim() !== "") {
      parsedSeed = Number(seed);
      if (
        !Number.isInteger(parsedSeed) ||
        parsedSeed < 0 ||
        parsedSeed > 2_147_483_647
      ) {
        fields.seed = "Seed must be an integer between 0 and 2^31-1.";
        errors.push("Seed is outside the accepted range.");
      }
    }

    if (dimensions.length === 0) {
      errors.push("Add at least one search dimension.");
    } else if (dimensions.length > 24) {
      errors.push("A search space may contain at most 24 dimensions.");
    }

    const trimmedNames = dimensions.map((dimension) => dimension.name.trim());
    const specs: DimensionSpec[] = [];
    dimensions.forEach((dimension) => {
      const { errors: dimensionFieldError, spec } = validateDimension(
        dimension,
        trimmedNames,
      );
      if (Object.keys(dimensionFieldError).length > 0) {
        dimFieldErrors[dimension.id] = dimensionFieldError;
        errors.push(
          `Dimension ${dimension.name.trim() || "(unnamed)"} has validation errors.`,
        );
      } else if (spec) {
        specs.push(spec);
      }
    });

    setFieldErrors(fields);
    setDimensionErrors(dimFieldErrors);
    setFormErrors(errors);
    return {
      ok: errors.length === 0 && specs.length === dimensions.length,
      formErrors: errors,
      dimensionErrors: dimFieldErrors,
      dimensions: specs,
    };
  };

  const onSubmit = async (): Promise<void> => {
    const result = validate();
    if (!result.ok) return;
    const body: OptimizationJobRequest = {
      name: name.trim(),
      model_id: modelId.trim(),
      space: { dimensions: result.dimensions },
      n_trials: Number(nTrials),
      seed: seed.trim() === "" ? undefined : Number(seed),
      sampler,
    };
    const record = await submit.run(body);
    if (record) {
      setJobId(record.job_id);
    }
  };

  const cancelJob = async (id: string): Promise<void> => {
    const updated = await cancel.run(id);
    if (updated) job.reload();
  };

  return (
    <>
      <PageHeader
        eyebrow="Resource planning"
        title="Optimizer"
        description="Define a declarative search space and run a seeded Optuna sweep. Every trial is persisted with its configuration, hardware fingerprint and objective values."
        actions={
          <Link className="btn btn--secondary btn--md" to="/experiments?kind=optimization">
            <span>History</span>
          </Link>
        }
      />

      <Panel
        title="Search space"
        subtitle="Numeric bounds are inclusive. Log scale requires positive bounds."
      >
        {formErrors.length > 0 ? (
          <InlineAlert tone="danger" title="Fix the highlighted fields">
            <ul className="plain-list">
              {formErrors.map((error) => (
                <li key={error}>{error}</li>
              ))}
            </ul>
          </InlineAlert>
        ) : null}

        <div className="form-grid form-grid--4">
          <TextField
            label="Sweep name"
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="quantization-sweep"
            error={fieldErrors.name}
            maxLength={120}
          />
          <TextField
            label="Model reference"
            required
            value={modelId}
            onChange={(event) => setModelId(event.target.value)}
            list="optimizer-model-ids"
            placeholder="org/model or local path"
            error={fieldErrors.modelId}
            hint="Required: the analytical planner reads this model's metadata."
          />
          <datalist id="optimizer-model-ids">
            {(models.data?.items ?? []).map((model) => (
              <option value={model.model_id} key={model.model_id} />
            ))}
          </datalist>
          <TextField
            label="Trials"
            type="number"
            min={1}
            max={500}
            step={1}
            required
            value={nTrials}
            onChange={(event) => setNTrials(event.target.value)}
            error={fieldErrors.nTrials}
            hint="1–500."
          />
          <TextField
            label="Seed (optional)"
            type="number"
            min={0}
            step={1}
            value={seed}
            onChange={(event) => setSeed(event.target.value)}
            error={fieldErrors.seed}
            hint="Seeded sweeps are reproducible."
          />
        </div>

        <div className="form-grid form-grid--3">
          <SelectField
            label="Sampler"
            value={sampler}
            onChange={(event) => setSampler(event.target.value as "tpe" | "random")}
          >
            <option value="tpe">TPE</option>
            <option value="random">Random</option>
          </SelectField>
        </div>

        <fieldset className="dimension-fieldset">
          <legend className="subheading">Dimensions</legend>
          <div className="dimension-list">
            {dimensions.map((dimension, index) => {
              const errors = dimensionErrors[dimension.id] ?? {};
              return (
                <div className="dimension-card" key={dimension.id}>
                  <div className="dimension-head">
                    <span className="dimension-index mono">
                      {String(index + 1).padStart(2, "0")}
                    </span>
                    <span className="dimension-title">
                      {dimension.name.trim() || "Unnamed dimension"}
                    </span>
                    <Button
                      variant="ghost"
                      size="sm"
                      icon="trash"
                      aria-label={`Remove dimension ${index + 1}`}
                      disabled={dimensions.length <= 1}
                      onClick={() => removeDimension(dimension.id)}
                    />
                  </div>
                  <div className="form-grid form-grid--4">
                    <TextField
                      label="Name"
                      required
                      value={dimension.name}
                      onChange={(event) =>
                        updateDimension(dimension.id, { name: event.target.value })
                      }
                      placeholder="dtype"
                      error={errors.name}
                      maxLength={64}
                    />
                    <SelectField
                      label="Kind"
                      value={dimension.kind}
                      onChange={(event) =>
                        updateDimension(dimension.id, {
                          kind: event.target.value as DimensionKind,
                        })
                      }
                    >
                      <option value="float">Float</option>
                      <option value="int">Integer</option>
                      <option value="categorical">Categorical</option>
                    </SelectField>
                    {dimension.kind === "categorical" ? (
                      <TextField
                        label="Choices"
                        required
                        value={dimension.choices}
                        onChange={(event) =>
                          updateDimension(dimension.id, { choices: event.target.value })
                        }
                        placeholder="bfloat16, float16, float32"
                        error={errors.choices}
                        className="field--span-2"
                        hint="Comma-separated; duplicates are removed."
                      />
                    ) : (
                      <>
                        <TextField
                          label="Low"
                          type="number"
                          step="any"
                          required
                          value={dimension.low}
                          onChange={(event) =>
                            updateDimension(dimension.id, { low: event.target.value })
                          }
                          error={errors.low}
                        />
                        <TextField
                          label="High"
                          type="number"
                          step="any"
                          required
                          value={dimension.high}
                          onChange={(event) =>
                            updateDimension(dimension.id, { high: event.target.value })
                          }
                          error={errors.high}
                        />
                      </>
                    )}
                  </div>
                  {dimension.kind !== "categorical" ? (
                    <div className="dimension-options">
                      <TextField
                        label="Step (optional)"
                        type="number"
                        step="any"
                        min={0}
                        value={dimension.step}
                        onChange={(event) =>
                          updateDimension(dimension.id, { step: event.target.value })
                        }
                        error={errors.step}
                        className="field--compact"
                      />
                      <CheckboxField
                        label="Log scale"
                        checked={dimension.log}
                        onChange={(checked) =>
                          updateDimension(dimension.id, { log: checked })
                        }
                        hint="Only valid when both bounds are positive."
                      />
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
          <div className="form-actions">
            <Button
              variant="secondary"
              icon="plus"
              onClick={() => setDimensions((current) => [...current, createDimension()])}
              disabled={dimensions.length >= 24}
            >
              Add dimension
            </Button>
          </div>
        </fieldset>

        {submit.error ? (
          <InlineAlert tone="danger" title={`Submission failed · ${submit.error.code}`}>
            <p>{submit.error.message}</p>
            {submit.error.hint ? <p className="small">{submit.error.hint}</p> : null}
            {submit.error.details !== undefined ? (
              <details className="details-block">
                <summary>Server details</summary>
                <JsonView value={submit.error.details} maxHeight={240} />
              </details>
            ) : null}
          </InlineAlert>
        ) : null}

        <div className="form-actions form-actions--end">
          <Button
            variant="primary"
            icon="optimizer"
            loading={submit.pending}
            onClick={() => void onSubmit()}
          >
            Start optimization
          </Button>
        </div>
      </Panel>

      <Panel
        title="Active job"
        subtitle="Live progress for the most recently submitted sweep"
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
            title="No optimization job submitted"
            description="Configure at least one dimension, give the sweep a name, then start the optimization. Progress and results appear here."
            icon="optimizer"
            compact
          />
        ) : (
          <AsyncState query={job} skeleton={<TableSkeleton rows={3} />} context="Optimization job">
            {(record) => (
              <>
                <JobCard
                  job={record}
                  onCancel={(id) => void cancelJob(id)}
                  cancelPending={cancel.pending}
                />
                {record.status === "succeeded" && record.experiment_id ? (
                  <InlineAlert
                    tone="success"
                    title="Sweep finished"
                    action={
                      <Link
                        className="btn btn--primary btn--sm"
                        to={`/experiments/${encodeURIComponent(record.experiment_id)}`}
                      >
                        <span>Open experiment</span>
                      </Link>
                    }
                  >
                    Results are persisted under experiment{" "}
                    <code>{record.experiment_id}</code>.
                  </InlineAlert>
                ) : null}
                {cancel.error ? (
                  <InlineAlert tone="warning" title="Cancellation failed">
                    {cancel.error.message}
                  </InlineAlert>
                ) : null}
              </>
            )}
          </AsyncState>
        )}
      </Panel>

      <Panel
        title="Recent optimization experiments"
        subtitle="Persisted sweeps ordered by creation time"
        flush
        actions={
          <Button
            variant="ghost"
            size="sm"
            icon="refresh"
            onClick={recent.reload}
            aria-label="Refresh optimization history"
          />
        }
      >
        <AsyncState
          query={recent}
          skeleton={<TableSkeleton rows={4} />}
          isEmpty={(data) => data.items.length === 0}
          empty={
            <EmptyState
              title="No optimization history"
              description="Completed sweeps appear here. Start one above to establish a reproducible record."
              icon="experiments"
              compact
            />
          }
        >
          {(data) => (
            <TableWrap label="Optimization history">
              <table className="data-table data-table--stack">
                <caption className="sr-only">Optimization history</caption>
                <thead>
                  <tr>
                    <th scope="col">Experiment</th>
                    <th scope="col">Status</th>
                    <th scope="col">Model</th>
                    <th scope="col">Seed</th>
                    <th scope="col">Created</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((experiment) => (
                    <OptimizerExperimentRow
                      key={experiment.experiment_id}
                      experiment={experiment}
                      now={now}
                    />
                  ))}
                </tbody>
              </table>
            </TableWrap>
          )}
        </AsyncState>
      </Panel>

      <div className="page-footnote">
        <Icon name="alert" size={14} />
        <span>
          Objective values are computed from analytical memory estimates unless a
          custom objective is configured on the backend. They are never reported as
          measurements.
        </span>
      </div>
    </>
  );
}

function OptimizerExperimentRow({
  experiment,
  now,
}: {
  experiment: ExperimentRecord;
  now: number;
}) {
  const trials = experiment.metrics["trial_count"];
  return (
    <tr>
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
        {trials !== undefined ? (
          <div className="cell-sub">{formatNumber(Number(trials))} trials</div>
        ) : null}
      </td>
    </tr>
  );
}
