import { Link } from "react-router-dom";
import type { ApiError } from "../../api/client";
import { JsonView } from "./JsonView";
import { Button } from "./Button";
import { Icon } from "./Icon";

interface ErrorStateProps {
  error: ApiError;
  onRetry?: () => void;
  context?: string;
  compact?: boolean;
}

function errorTitle(error: ApiError): string {
  if (error.isUnauthorized) return "API key required";
  if (error.code === "network") return "API unreachable";
  if (error.code === "timeout") return "Backend timed out";
  if (error.code === "invalid_response") return "Unexpected API response";
  return "Request failed";
}

export function ErrorState({ error, onRetry, context, compact = false }: ErrorStateProps) {
  const title = errorTitle(error);
  return (
    <div
      className={["error-state", compact ? "error-state--compact" : ""].filter(Boolean).join(" ")}
      role="alert"
    >
      <div className="error-state-head">
        <span className="error-state-icon" aria-hidden="true">
          <Icon name="alert" size={compact ? 18 : 22} />
        </span>
        <div>
          <p className="error-state-title">{title}</p>
          <p className="error-state-context">
            {context ? `${context} · ` : null}
            <code>{error.code}</code>
            {error.status ? <span> · HTTP {error.status}</span> : null}
          </p>
        </div>
      </div>
      <p className="error-state-message">{error.message}</p>
      {error.hint ? <p className="error-state-hint">{error.hint}</p> : null}
      {error.details !== undefined && error.details !== null ? (
        <details className="error-state-details">
          <summary>Technical details</summary>
          <JsonView value={error.details} maxHeight={240} />
        </details>
      ) : null}
      <div className="error-state-actions">
        {onRetry ? (
          <Button variant="secondary" icon="refresh" onClick={onRetry}>
            Retry
          </Button>
        ) : null}
        {error.isUnauthorized ? (
          <Link className="btn btn--primary btn--md" to="/settings#api-key">
            <span>Open Settings</span>
          </Link>
        ) : null}
      </div>
    </div>
  );
}
