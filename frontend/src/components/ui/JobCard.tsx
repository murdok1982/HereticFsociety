import { Link } from "react-router-dom";
import type { JobRecord } from "../../api/types";
import { isTerminalJobStatus } from "../../api/types";
import { formatDateTime, formatProgress, formatRelativeTime, progressPercent } from "../../lib/format";
import { useNow } from "../../hooks/useNow";
import { Badge, StatusBadge } from "./Badge";
import { Button } from "./Button";
import { InlineAlert } from "./InlineAlert";
import { JsonView } from "./JsonView";
import { ProgressBar } from "./ProgressBar";

interface JobCardProps {
  job: JobRecord;
  onCancel?: (jobId: string) => void;
  cancelPending?: boolean;
  compact?: boolean;
}

export function JobCard({ job, onCancel, cancelPending = false, compact = false }: JobCardProps) {
  const now = useNow(10_000);
  const terminal = isTerminalJobStatus(job.status);
  const canCancel =
    Boolean(onCancel) && job.cancellable && !terminal && !job.cancel_requested;
  const hasRequest = Object.keys(job.request ?? {}).length > 0;
  const hasResult = Object.keys(job.result ?? {}).length > 0;

  return (
    <article className={["job-card", compact ? "job-card--compact" : ""].filter(Boolean).join(" ")}>
      <header className="job-card-head">
        <div className="job-card-identity">
          <Badge tone="neutral" mono>
            {job.kind}
          </Badge>
          <code className="job-id" title={job.job_id}>
            {job.job_id}
          </code>
        </div>
        <div className="job-card-actions">
          <StatusBadge status={job.status} />
          {canCancel ? (
            <Button
              variant="danger"
              size="sm"
              loading={cancelPending}
              onClick={() => onCancel?.(job.job_id)}
            >
              Cancel
            </Button>
          ) : null}
        </div>
      </header>

      <div className="job-card-live" aria-live="polite">
        <ProgressBar
          value={progressPercent(job.progress)}
          label={job.status === "queued" ? "Waiting for a worker" : "Job progress"}
          tone={terminal ? "success" : "accent"}
        />
        {job.message ? <p className="job-message">{job.message}</p> : null}
        {job.cancel_requested && !terminal ? (
          <p className="small warning-text">Cancellation requested — waiting for the worker to stop.</p>
        ) : null}
      </div>

      {job.error ? (
        <InlineAlert tone="danger" title="Job failed">
          {job.error}
        </InlineAlert>
      ) : null}

      <dl className="job-meta">
        <div>
          <dt>Created</dt>
          <dd title={formatDateTime(job.created_at)}>{formatRelativeTime(job.created_at, now)}</dd>
        </div>
        <div>
          <dt>Started</dt>
          <dd>{job.started_at ? formatRelativeTime(job.started_at, now) : "—"}</dd>
        </div>
        <div>
          <dt>Finished</dt>
          <dd>{job.finished_at ? formatRelativeTime(job.finished_at, now) : "—"}</dd>
        </div>
        <div>
          <dt>Progress</dt>
          <dd className="mono">{formatProgress(job.progress)}</dd>
        </div>
      </dl>

      {job.experiment_id ? (
        <p className="job-experiment">
          Experiment:{" "}
          <Link to={`/experiments/${encodeURIComponent(job.experiment_id)}`}>
            {job.experiment_id}
          </Link>
        </p>
      ) : null}

      {!compact && (hasRequest || hasResult) ? (
        <div className="job-details">
          {hasRequest ? (
            <details className="details-block">
              <summary>Request payload</summary>
              <JsonView value={job.request} maxHeight={220} />
            </details>
          ) : null}
          {hasResult ? (
            <details className="details-block">
              <summary>Result payload</summary>
              <JsonView value={job.result} maxHeight={260} />
            </details>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}
