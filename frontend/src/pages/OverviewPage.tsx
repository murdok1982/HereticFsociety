import { Link } from "react-router-dom";
import { api } from "../api/client";
import type {
  EngineStatus,
  ExperimentRecord,
  HardwareProfile,
  SystemInfo,
} from "../api/types";
import { useApiQuery } from "../hooks/useApiQuery";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useNow } from "../hooks/useNow";
import {
  formatBytes,
  formatDateTime,
  formatDuration,
  formatProgress,
  formatRelativeTime,
  formatValue,
  humanizeKey,
  pluralize,
} from "../lib/format";
import { AsyncState } from "../components/ui/AsyncState";
import { Badge, StatusBadge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { Icon, type IconName } from "../components/ui/Icon";
import { InlineAlert } from "../components/ui/InlineAlert";
import { KeyValueGrid } from "../components/ui/JsonView";
import { PageHeader, Panel } from "../components/ui/Panel";
import { StatCard } from "../components/ui/StatCard";
import { StatsSkeleton, TableSkeleton } from "../components/ui/Skeleton";
import { TableWrap } from "../components/ui/TableWrap";

interface OverviewCore {
  version: SystemInfo;
  engine: EngineStatus;
  hardware: HardwareProfile;
}

interface QuickLink {
  to: string;
  title: string;
  description: string;
  icon: IconName;
}

const QUICK_LINKS: QuickLink[] = [
  {
    to: "/models",
    title: "Model Manager",
    description: "Scan local roots, inspect metadata and verify hashes.",
    icon: "models",
  },
  {
    to: "/hardware",
    title: "Hardware Monitor",
    description: "Review detected CPU, RAM and accelerator provenance.",
    icon: "hardware",
  },
  {
    to: "/evaluation",
    title: "Evaluation Center",
    description: "Run reproducible suites and compare reports.",
    icon: "evaluation",
  },
  {
    to: "/security",
    title: "Security Posture",
    description: "Audit events, findings, policy and the SBOM.",
    icon: "security",
  },
];

function healthCheckTone(value: string): "success" | "warning" | "danger" | "neutral" {
  const normalized = value.toLowerCase();
  if (normalized === "ok" || normalized === "available" || normalized === "true") {
    return "success";
  }
  if (normalized === "unavailable" || normalized.startsWith("error")) return "danger";
  if (normalized === "read-only" || normalized === "false") return "warning";
  return Number.isFinite(Number(normalized)) ? "neutral" : "warning";
}

function EnginePanel({ engine }: { engine: EngineStatus }) {
  return (
    <Panel
      title="Modification engine"
      subtitle="Heretic runtime used for model modification jobs"
      actions={
        <Badge tone={engine.available ? "success" : "warning"}>
          {engine.available ? "available" : "unavailable"}
        </Badge>
      }
    >
      {!engine.available ? (
        <InlineAlert tone="warning" title={engine.message || "Engine is not installed"}>
          {engine.missing.length > 0 ? (
            <p>
              Missing packages:{" "}
              {engine.missing.map((name) => (
                <code className="inline-code" key={name}>
                  {name}
                </code>
              ))}
            </p>
          ) : (
            <p>The API reports that the engine cannot be imported.</p>
          )}
          {engine.hint ? <p className="mono small">{engine.hint}</p> : null}
        </InlineAlert>
      ) : (
        <p className="muted small">{engine.message}</p>
      )}
      <KeyValueGrid
        data={{
          heretic_version: engine.heretic_version,
          torch_version: engine.torch_version,
          transformers_version: engine.transformers_version,
        }}
      />
    </Panel>
  );
}

export function OverviewPage() {
  useDocumentTitle("Overview");
  const now = useNow(15_000);

  const health = useApiQuery((signal) => api.getHealth(signal), [], {
    pollMs: 30_000,
  });
  const core = useApiQuery<OverviewCore>(async (signal) => {
    const [version, engine, hardware] = await Promise.all([
      api.getVersion(signal),
      api.getEngine(signal),
      api.getHardware(false, signal),
    ]);
    return { version, engine, hardware };
  }, []);
  const jobs = useApiQuery(
    (signal) => api.getJobs({ limit: 6 }, signal),
    [],
    { pollMs: 10_000 },
  );
  const experiments = useApiQuery(
    (signal) => api.getExperiments({ limit: 5 }, signal),
    [],
    { pollMs: 20_000 },
  );

  const reloadAll = (): void => {
    health.reload();
    core.reload();
    jobs.reload();
    experiments.reload();
  };

  const healthError = health.error;
  const apiDown = Boolean(healthError) && core.data === null;

  return (
    <>
      <PageHeader
        eyebrow="Operator console"
        title="Overview"
        description="Live platform posture from the FSOCIETY API. Every value below is fetched; unknown fields render as an em dash."
        actions={
          <Button icon="refresh" onClick={reloadAll}>
            Refresh all
          </Button>
        }
      />

      {apiDown && healthError ? (
        <ErrorState
          error={healthError}
          onRetry={reloadAll}
          context="Platform overview"
        />
      ) : (
        <>
          {healthError ? (
            <InlineAlert
              tone="warning"
              title="Health probe failed"
              action={
                <Button variant="ghost" size="sm" icon="refresh" onClick={health.reload}>
                  Retry
                </Button>
              }
            >
              {healthError.message}
            </InlineAlert>
          ) : null}

          <AsyncState
            query={core}
            skeleton={<StatsSkeleton />}
            context="System overview"
          >
            {({ version, engine, hardware }) => (
              <>
                <div className="grid grid-4 stat-grid">
                  <StatCard
                    label="Platform"
                    value={version.platform_name}
                    hint={
                      <>
                        v{version.version} · API {version.api_version} · plugin{" "}
                        {version.plugin_api_version}
                      </>
                    }
                    icon="overview"
                  />
                  <StatCard
                    label="API health"
                    value={
                      health.data ? (
                        <StatusBadge status={health.data.status} />
                      ) : (
                        "—"
                      )
                    }
                    hint={
                      health.data
                        ? `uptime ${formatDuration(health.data.uptime_seconds)}`
                        : "health probe unavailable"
                    }
                    tone={
                      health.data?.status === "ok"
                        ? "success"
                        : health.data?.status === "degraded"
                          ? "warning"
                          : "default"
                    }
                  />
                  <StatCard
                    label="Engine"
                    value={engine.available ? engine.heretic_version ?? "available" : "unavailable"}
                    hint={
                      engine.available
                        ? "Heretic modification engine"
                        : pluralize(engine.missing.length, "missing package")
                    }
                    tone={engine.available ? "success" : "warning"}
                    mono
                    icon="plugins"
                  />
                  <StatCard
                    label="Accelerators"
                    value={hardware.gpus.length > 0 ? String(hardware.gpus.length) : "none"}
                    hint={
                      hardware.gpus.length > 0
                        ? hardware.gpus
                            .map((gpu) => `${gpu.name} · ${formatBytes(gpu.total_memory_bytes)}`)
                            .join(" · ")
                        : "No GPU detected by the backend"
                    }
                    icon="hardware"
                  />
                </div>

                <div className="grid grid-2">
                  <Panel
                    title="System"
                    subtitle="Build identity and workspace"
                    actions={
                      <span className="mono small muted">
                        started {formatRelativeTime(version.started_at, now)}
                      </span>
                    }
                  >
                    <KeyValueGrid
                      data={{
                        version: version.version,
                        api_version: version.api_version,
                        plugin_api_version: version.plugin_api_version,
                        python_version: version.python_version,
                        workspace: version.workspace,
                        started_at: formatDateTime(version.started_at),
                      }}
                    />
                    {health.data ? (
                      <div className="checks-block">
                        <h3 className="subheading">Health checks</h3>
                        <ul className="check-list">
                          {Object.entries(health.data.checks).map(([name, value]) => {
                            const tone = healthCheckTone(String(value));
                            return (
                              <li key={name}>
                                <span className={`dot dot--${tone}`} aria-hidden="true" />
                                <span className="check-name">{humanizeKey(name)}</span>
                                <span className="check-value mono">{formatValue(value)}</span>
                              </li>
                            );
                          })}
                        </ul>
                      </div>
                    ) : null}
                  </Panel>

                  <EnginePanel engine={engine} />
                </div>

                <Panel
                  title="Hardware summary"
                  subtitle="Detected profile"
                  actions={
                    <>
                      <Badge tone={hardware.detection_mode === "measured" ? "success" : "warning"}>
                        {hardware.detection_mode}
                      </Badge>
                      <Link className="btn btn--ghost btn--sm" to="/hardware">
                        <span>Open monitor</span>
                      </Link>
                    </>
                  }
                >
                  <div className="summary-strip">
                    <div>
                      <span className="summary-label">CPU</span>
                      <span className="summary-value">
                        {hardware.cpu.model ?? "Unknown CPU"}
                      </span>
                      <span className="summary-detail mono">
                        {formatValue(hardware.cpu.physical_cores)} cores ·{" "}
                        {formatValue(hardware.cpu.logical_cores)} threads
                      </span>
                    </div>
                    <div>
                      <span className="summary-label">Memory</span>
                      <span className="summary-value mono">
                        {formatBytes(hardware.ram_total_bytes)}
                      </span>
                      <span className="summary-detail mono">
                        {formatBytes(hardware.ram_available_bytes)} available
                      </span>
                    </div>
                    <div>
                      <span className="summary-label">Host</span>
                      <span className="summary-value">{hardware.hostname}</span>
                      <span className="summary-detail">
                        {hardware.os} {hardware.os_release} · {hardware.architecture}
                      </span>
                    </div>
                    <div>
                      <span className="summary-label">Profile</span>
                      <span className="summary-value mono small">{hardware.profile_id}</span>
                      <span className="summary-detail" title={formatDateTime(hardware.captured_at)}>
                        captured {formatRelativeTime(hardware.captured_at, now)}
                      </span>
                    </div>
                  </div>
                  {hardware.warnings.length > 0 ? (
                    <details className="details-block">
                      <summary>
                        {pluralize(hardware.warnings.length, "detection warning")}
                      </summary>
                      <ul className="plain-list">
                        {hardware.warnings.map((warning) => (
                          <li key={warning}>{warning}</li>
                        ))}
                      </ul>
                    </details>
                  ) : null}
                </Panel>
              </>
            )}
          </AsyncState>

          <div className="grid grid--records">
            <Panel
              title="Recent jobs"
              subtitle="Background work queued by the API"
              actions={
                <Button
                  variant="ghost"
                  size="sm"
                  icon="refresh"
                  onClick={jobs.reload}
                  aria-label="Refresh recent jobs"
                />
              }
            >
              <AsyncState
                query={jobs}
                skeleton={<TableSkeleton rows={4} />}
                isEmpty={(data) => data.items.length === 0}
                empty={
                  <EmptyState
                    title="No jobs yet"
                    description="Jobs appear here after you submit an optimization or evaluation run."
                    icon="clock"
                    action={
                      <Link className="btn btn--secondary btn--sm" to="/optimizer">
                        <span>Open optimizer</span>
                      </Link>
                    }
                    compact
                  />
                }
              >
                {(data) => (
                  <TableWrap label="Recent jobs table">
                    <table className="data-table data-table--compact data-table--stack">
                      <caption className="sr-only">Recent jobs</caption>
                      <thead>
                        <tr>
                          <th scope="col">Kind</th>
                          <th scope="col">Status</th>
                          <th scope="col">Progress</th>
                          <th scope="col">Created</th>
                          <th scope="col">Experiment</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.items.map((job) => (
                          <tr key={job.job_id}>
                            <td data-label="Kind">
                              <Badge tone="neutral" mono>
                                {job.kind}
                              </Badge>
                            </td>
                            <td data-label="Status">
                              <StatusBadge status={job.status} />
                            </td>
                            <td className="mono" data-label="Progress">
                              {formatProgress(job.progress)}
                            </td>
                            <td
                              title={formatDateTime(job.created_at)}
                              data-label="Created"
                            >
                              {formatRelativeTime(job.created_at, now)}
                            </td>
                            <td data-label="Experiment">
                              {job.experiment_id ? (
                                <Link
                                  to={`/experiments/${encodeURIComponent(job.experiment_id)}`}
                                  className="mono small"
                                >
                                  open
                                </Link>
                              ) : (
                                <span className="muted">—</span>
                              )}
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
              title="Recent experiments"
              subtitle="Persisted reproducibility records"
              actions={
                <>
                  <Button
                    variant="ghost"
                    size="sm"
                    icon="refresh"
                    onClick={experiments.reload}
                    aria-label="Refresh recent experiments"
                  />
                  <Link className="btn btn--ghost btn--sm" to="/experiments">
                    <span>View all</span>
                  </Link>
                </>
              }
            >
              <AsyncState
                query={experiments}
                skeleton={<TableSkeleton rows={4} />}
                isEmpty={(data) => data.items.length === 0}
                empty={
                  <EmptyState
                    title="No experiments recorded"
                    description="Run an evaluation suite or an optimization sweep to persist a record with hardware and config hashes."
                    icon="experiments"
                    action={
                      <Link className="btn btn--secondary btn--sm" to="/evaluation">
                        <span>Run an evaluation</span>
                      </Link>
                    }
                    compact
                  />
                }
              >
                {(data) => (
                  <TableWrap label="Recent experiments table">
                    <table className="data-table data-table--compact data-table--stack">
                      <caption className="sr-only">Recent experiments</caption>
                      <thead>
                        <tr>
                          <th scope="col">Experiment</th>
                          <th scope="col">Kind</th>
                          <th scope="col">Status</th>
                          <th scope="col">Created</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.items.map((experiment) => (
                          <ExperimentRow
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
          </div>

          <Panel title="Quick links" subtitle="Jump to the next action">
            <div className="quick-links">
              {QUICK_LINKS.map((link) => (
                <Link className="quick-link" to={link.to} key={link.to}>
                  <span className="quick-link-icon" aria-hidden="true">
                    <Icon name={link.icon} size={18} />
                  </span>
                  <span>
                    <span className="quick-link-title">{link.title}</span>
                    <span className="quick-link-description">{link.description}</span>
                  </span>
                  <Icon name="chevron-right" size={16} />
                </Link>
              ))}
            </div>
          </Panel>
        </>
      )}
    </>
  );
}

function ExperimentRow({
  experiment,
  now,
}: {
  experiment: ExperimentRecord;
  now: number;
}) {
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
      <td data-label="Kind">
        <Badge tone="neutral" mono>
          {experiment.kind}
        </Badge>
      </td>
      <td data-label="Status">
        <StatusBadge status={experiment.status} />
      </td>
      <td
        title={formatDateTime(experiment.created_at)}
        data-label="Created"
      >
        {formatRelativeTime(experiment.created_at, now)}
      </td>
    </tr>
  );
}
