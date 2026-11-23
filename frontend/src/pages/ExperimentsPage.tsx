import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { useApiQuery } from "../hooks/useApiQuery";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useNow } from "../hooks/useNow";
import { formatDateTime, formatRelativeTime, pluralize } from "../lib/format";
import { AsyncState } from "../components/ui/AsyncState";
import { Badge, StatusBadge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { EmptyState } from "../components/ui/EmptyState";
import { SelectField, TextField } from "../components/ui/FormField";
import { PageHeader, Panel } from "../components/ui/Panel";
import { TableSkeleton } from "../components/ui/Skeleton";
import { TableWrap } from "../components/ui/TableWrap";

const KIND_OPTIONS = [
  { value: "", label: "All kinds" },
  { value: "evaluation", label: "Evaluation" },
  { value: "optimization", label: "Optimization" },
  { value: "modification", label: "Modification" },
  { value: "export", label: "Export" },
  { value: "verification", label: "Verification" },
];

const STATUS_OPTIONS = [
  { value: "", label: "All statuses" },
  { value: "queued", label: "Queued" },
  { value: "running", label: "Running" },
  { value: "succeeded", label: "Succeeded" },
  { value: "failed", label: "Failed" },
  { value: "cancelled", label: "Cancelled" },
];

export function ExperimentsPage() {
  useDocumentTitle("Experiments");
  const now = useNow(30_000);
  const [searchParams, setSearchParams] = useSearchParams();
  const kind = searchParams.get("kind") ?? "";
  const status = searchParams.get("status") ?? "";
  const search = searchParams.get("q") ?? "";

  const experiments = useApiQuery(
    (signal) => api.getExperiments({ limit: 100, kind: kind || undefined }, signal),
    [kind],
  );

  const setParam = (key: string, value: string): void => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    setSearchParams(next, { replace: true });
  };

  const filtered = useMemo(() => {
    const items = experiments.data?.items ?? [];
    const needle = search.trim().toLowerCase();
    return items.filter((experiment) => {
      if (status && experiment.status !== status) return false;
      if (!needle) return true;
      return [
        experiment.name,
        experiment.experiment_id,
        experiment.model_id ?? "",
        experiment.kind,
      ]
        .join(" ")
        .toLowerCase()
        .includes(needle);
    });
  }, [experiments.data, search, status]);

  const filtersActive = Boolean(kind || status || search);

  return (
    <>
      <PageHeader
        eyebrow="History"
        title="Experiments"
        description="Every optimization, evaluation and modification run is persisted with its seed, configuration hash, suite hash and hardware fingerprint."
        actions={
          <Button icon="refresh" onClick={experiments.reload} loading={experiments.refreshing}>
            Refresh
          </Button>
        }
      />

      <Panel
        title="Filters"
        subtitle="The kind filter is applied server-side; status and text filter the returned page."
      >
        <div className="form-grid form-grid--3">
          <SelectField
            label="Kind"
            value={kind}
            onChange={(event) => setParam("kind", event.target.value)}
          >
            {KIND_OPTIONS.map((option) => (
              <option value={option.value} key={option.value || "all"}>
                {option.label}
              </option>
            ))}
          </SelectField>
          <SelectField
            label="Status"
            value={status}
            onChange={(event) => setParam("status", event.target.value)}
          >
            {STATUS_OPTIONS.map((option) => (
              <option value={option.value} key={option.value || "all"}>
                {option.label}
              </option>
            ))}
          </SelectField>
          <TextField
            label="Search"
            type="search"
            value={search}
            onChange={(event) => setParam("q", event.target.value)}
            placeholder="name, experiment id, model…"
          />
        </div>
        {filtersActive ? (
          <div className="form-actions">
            <Button
              variant="ghost"
              size="sm"
              icon="close"
              onClick={() => setSearchParams(new URLSearchParams(), { replace: true })}
            >
              Clear filters
            </Button>
          </div>
        ) : null}
      </Panel>

      <Panel
        title="Records"
        subtitle={
          experiments.data
            ? `${filtered.length} of ${pluralize(experiments.data.items.length, "experiment")} shown`
            : "Loading experiments"
        }
        flush
      >
        <AsyncState
          query={experiments}
          skeleton={<TableSkeleton rows={7} />}
          context="Experiment history"
          isEmpty={(data) => data.items.length === 0}
          empty={
            <EmptyState
              title={kind ? `No ${kind} experiments yet` : "No experiments recorded"}
              description="Experiments are created by evaluation and optimization jobs. Start a run, then return here for its full reproducibility record."
              icon="experiments"
              action={
                <div className="form-actions">
                  <Link className="btn btn--primary btn--sm" to="/evaluation">
                    <span>Run evaluation</span>
                  </Link>
                  <Link className="btn btn--secondary btn--sm" to="/optimizer">
                    <span>Start optimization</span>
                  </Link>
                </div>
              }
            />
          }
        >
          {() =>
            filtered.length === 0 ? (
              <EmptyState
                title="No experiments match these filters"
                description="Clear the status or search filters to see the full history for this kind."
                icon="experiments"
                compact
                action={
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => setSearchParams(new URLSearchParams(), { replace: true })}
                  >
                    Clear filters
                  </Button>
                }
              />
            ) : (
              <TableWrap label="Experiment history">
                <table className="data-table data-table--stack">
                  <caption className="sr-only">Experiment history</caption>
                  <thead>
                    <tr>
                      <th scope="col">Experiment</th>
                      <th scope="col">Kind</th>
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
                    {filtered.map((experiment) => (
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
                        <td data-label="Kind">
                          <Badge tone="neutral" mono>
                            {experiment.kind}
                          </Badge>
                        </td>
                        <td data-label="Status">
                          <StatusBadge status={experiment.status} />
                        </td>
                        <td className="mono small" data-label="Model">{experiment.model_id ?? "—"}</td>
                        <td className="mono" data-label="Seed">{experiment.seed ?? "—"}</td>
                        <td data-label="Created" title={formatDateTime(experiment.created_at)}>
                          {formatRelativeTime(experiment.created_at, now)}
                        </td>
                        <td className="cell-actions" data-label="Actions">
                          <Link
                            className="btn btn--ghost btn--sm"
                            to={`/experiments/${encodeURIComponent(experiment.experiment_id)}`}
                          >
                            <span>Open</span>
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            )
          }
        </AsyncState>
      </Panel>
    </>
  );
}
