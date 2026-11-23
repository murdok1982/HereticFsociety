import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { ExperimentRecord, TrialRecord } from "../api/types";
import { useApiQuery } from "../hooks/useApiQuery";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useNow } from "../hooks/useNow";
import {
  formatDateTime,
  formatDuration,
  formatRelativeTime,
  formatValue,
  isRecord,
  pluralize,
  truncate,
} from "../lib/format";
import { ArtifactTable } from "../components/ui/ArtifactTable";
import { AsyncState } from "../components/ui/AsyncState";
import { Badge, StatusBadge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { DynamicTable } from "../components/ui/DynamicTable";
import { EmptyState } from "../components/ui/EmptyState";
import { InlineAlert } from "../components/ui/InlineAlert";
import { JsonView, KeyValueGrid } from "../components/ui/JsonView";
import { PageHeader, Panel } from "../components/ui/Panel";
import { TableSkeleton } from "../components/ui/Skeleton";
import { TableWrap } from "../components/ui/TableWrap";

function trialSummary(trial: TrialRecord): string {
  const parts = Object.entries(trial.params).map(
    ([key, value]) => `${key}=${formatValue(value)}`,
  );
  return truncate(parts.join("  "), 110);
}

function MetricsPanel({ experiment }: { experiment: ExperimentRecord }) {
  const scalarMetrics = Object.entries(experiment.metrics).filter(
    ([, value]) =>
      typeof value === "number" ||
      typeof value === "string" ||
      typeof value === "boolean",
  );
  const structuredMetrics = Object.entries(experiment.metrics).filter(
    ([, value]) => isRecord(value) || Array.isArray(value),
  );

  return (
    <Panel title="Metrics" subtitle="Objective values and grader aggregates">
      {scalarMetrics.length === 0 ? (
        <p className="muted small">No scalar metrics recorded.</p>
      ) : (
        <div className="metric-grid">
          {scalarMetrics.map(([key, value]) => (
            <div className="metric-tile" key={key}>
              <span className="metric-label">{key.replace(/_/g, " ")}</span>
              <span className="metric-value mono">{formatValue(value)}</span>
            </div>
          ))}
        </div>
      )}
      {structuredMetrics.map(([key, value]) => (
        <details className="details-block" key={key}>
          <summary>{key.replace(/_/g, " ")}</summary>
          {Array.isArray(value) ? (
            <DynamicTable
              rows={value.filter(isRecord)}
              caption={key}
              maxRows={100}
            />
          ) : isRecord(value) ? (
            <DynamicTable
              rows={Object.entries(value).map(([label, entry]) => ({
                key: label,
                value: entry,
              }))}
              maxRows={100}
            />
          ) : (
            <JsonView value={value} />
          )}
        </details>
      ))}
      {Object.keys(experiment.metrics).length === 0 ? (
        <p className="muted small">The experiment has no metrics payload.</p>
      ) : null}
    </Panel>
  );
}

export function ExperimentDetailPage() {
  const { id } = useParams<{ id: string }>();
  const now = useNow(15_000);
  useDocumentTitle(id ? `Experiment ${id}` : "Experiment");

  const experiment = useApiQuery(
    (signal) => api.getExperiment(id as string, signal),
    [id],
    { enabled: Boolean(id), keepPreviousData: false },
  );
  const trials = useApiQuery(
    (signal) => api.getExperimentTrials(id as string, signal),
    [id],
    { enabled: Boolean(id), keepPreviousData: false },
  );
  const artifacts = useApiQuery(
    (signal) => api.getExperimentArtifacts(id as string, signal),
    [id],
    { enabled: Boolean(id), keepPreviousData: false },
  );

  if (!id) {
    return (
      <EmptyState
        title="Missing experiment id"
        description="The route did not include an experiment id."
        action={
          <Link className="btn btn--secondary btn--sm" to="/experiments">
            <span>Back to experiments</span>
          </Link>
        }
      />
    );
  }

  const reloadAll = (): void => {
    experiment.reload();
    trials.reload();
    artifacts.reload();
  };

  return (
    <>
      <div className="breadcrumb">
        <Link to="/experiments">
          <span>Experiments</span>
        </Link>
        <span aria-hidden="true">/</span>
        <span className="mono">{id}</span>
      </div>

      <AsyncState
        query={experiment}
        skeleton={<TableSkeleton rows={6} />}
        context="Experiment record"
      >
        {(record) => (
          <>
            <PageHeader
              eyebrow={`${record.kind} experiment`}
              title={record.name}
              description={
                <span className="mono small">
                  {record.experiment_id} · created{" "}
                  {formatRelativeTime(record.created_at, now)}
                </span>
              }
              actions={
                <>
                  <StatusBadge status={record.status} />
                  <Button icon="refresh" onClick={reloadAll} loading={experiment.refreshing}>
                    Refresh
                  </Button>
                </>
              }
            />

            {record.error ? (
              <InlineAlert tone="danger" title="Experiment reported an error">
                {record.error}
              </InlineAlert>
            ) : null}
            {record.notes ? (
              <InlineAlert tone="info" title="Operator notes">
                {record.notes}
              </InlineAlert>
            ) : null}

            <Panel title="Metadata" subtitle="Reproducibility identifiers and timestamps">
              <KeyValueGrid
                columns={3}
                data={{
                  kind: record.kind,
                  status: record.status,
                  model_id: record.model_id,
                  seed: record.seed,
                  created_at: formatDateTime(record.created_at),
                  started_at: record.started_at
                    ? formatDateTime(record.started_at)
                    : null,
                  finished_at: record.finished_at
                    ? formatDateTime(record.finished_at)
                    : null,
                  config_hash: record.config_hash,
                  suite_hash: record.suite_hash,
                  hardware_fingerprint: record.hardware_fingerprint,
                }}
              />
              {record.tags.length > 0 ? (
                <div className="badge-row">
                  {record.tags.map((tag) => (
                    <Badge tone="neutral" key={tag}>
                      {tag}
                    </Badge>
                  ))}
                </div>
              ) : null}
            </Panel>

            <div className="grid grid-2">
              <Panel title="Parameters" subtitle="Exact configuration persisted with the run">
                {Object.keys(record.params).length === 0 ? (
                  <p className="muted small">No parameters recorded.</p>
                ) : (
                  <JsonView value={record.params} maxHeight={320} />
                )}
              </Panel>
              <Panel title="Software" subtitle="Versions captured at execution time">
                {Object.keys(record.software).length === 0 ? (
                  <p className="muted small">No software versions recorded.</p>
                ) : (
                  <KeyValueGrid data={record.software} columns={1} />
                )}
              </Panel>
            </div>

            <MetricsPanel experiment={record} />

            <Panel
              title="Trials"
              subtitle="One row per optimizer trial"
              flush
              actions={
                trials.data ? (
                  <span className="mono small muted">
                    {pluralize(trials.data.items.length, "trial")}
                  </span>
                ) : undefined
              }
            >
              <AsyncState
                query={trials}
                skeleton={<TableSkeleton rows={4} />}
                isEmpty={(data) => data.items.length === 0}
                empty={
                  <EmptyState
                    title="No trials recorded"
                    description="Optimization experiments persist every trial. Evaluation experiments record aggregate metrics instead."
                    icon="optimizer"
                    compact
                    action={
                      <Link className="btn btn--secondary btn--sm" to="/optimizer">
                        <span>Open optimizer</span>
                      </Link>
                    }
                  />
                }
              >
                {(data) => (
                  <TableWrap label="Optimizer trials">
                    <table className="data-table data-table--compact data-table--stack">
                      <caption className="sr-only">Optimizer trials</caption>
                      <thead>
                        <tr>
                          <th scope="col">#</th>
                          <th scope="col">State</th>
                          <th scope="col">Values</th>
                          <th scope="col">Objectives</th>
                          <th scope="col">Duration</th>
                          <th scope="col">Params</th>
                          <th scope="col">Created</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.items.map((trial) => (
                          <tr key={trial.trial_id}>
                            <td className="mono" data-label="#">{trial.number}</td>
                            <td data-label="State">
                              <StatusBadge status={trial.state} />
                            </td>
                            <td className="mono" data-label="Values">
                              {trial.values.length > 0
                                ? trial.values.map((value) => formatValue(value)).join(" / ")
                                : "—"}
                            </td>
                            <td className="small" data-label="Objectives">
                              {trial.objective_names.length > 0
                                ? trial.objective_names.join(", ")
                                : "—"}
                            </td>
                            <td className="mono" data-label="Duration">
                              {formatDuration(trial.duration_seconds)}
                            </td>
                            <td className="small mono" data-label="Params" title={trialSummary(trial)}>
                              {trialSummary(trial) || "—"}
                            </td>
                            <td data-label="Created" title={formatDateTime(trial.created_at)}>
                              {formatRelativeTime(trial.created_at, now)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </TableWrap>
                )}
              </AsyncState>
            </Panel>

            <Panel
              title="Artifacts"
              subtitle="Reports, bundles and generated files"
              flush
              actions={
                artifacts.data ? (
                  <span className="mono small muted">
                    {pluralize(artifacts.data.items.length, "artifact")}
                  </span>
                ) : undefined
              }
            >
              <AsyncState
                query={artifacts}
                skeleton={<TableSkeleton rows={3} />}
                isEmpty={(data) => data.items.length === 0}
                empty={
                  <EmptyState
                    title="No artifacts"
                    description="This experiment produced no stored artifacts. Run an evaluation with report persistence enabled to generate JSON and Markdown reports."
                    icon="exports"
                    compact
                    action={
                      <Link className="btn btn--secondary btn--sm" to="/evaluation">
                        <span>Open evaluation</span>
                      </Link>
                    }
                  />
                }
              >
                {(data) => <ArtifactTable items={data.items} />}
              </AsyncState>
            </Panel>

            <Panel title="Raw record" subtitle="Authoritative JSON returned by the API">
              <details className="details-block">
                <summary>Experiment JSON</summary>
                <JsonView value={record} maxHeight={420} />
              </details>
            </Panel>
          </>
        )}
      </AsyncState>
    </>
  );
}
