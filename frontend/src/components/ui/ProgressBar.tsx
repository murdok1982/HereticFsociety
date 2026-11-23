import type { Tone } from "../../lib/status";

interface ProgressBarProps {
  /** Percentage in the 0–100 range; null renders an indeterminate bar. */
  value: number | null;
  label?: string;
  tone?: Tone;
  showValue?: boolean;
}

export function ProgressBar({
  value,
  label = "Progress",
  tone = "accent",
  showValue = true,
}: ProgressBarProps) {
  const determinate = value !== null;
  return (
    <div className="progress-block">
      {label || showValue ? (
        <div className="progress-meta">
          <span>{label}</span>
          {showValue ? (
            <span className="mono">{determinate ? `${value.toFixed(0)}%` : "pending"}</span>
          ) : null}
        </div>
      ) : null}
      <div
        className={[
          "progress-track",
          determinate ? "" : "progress-track--indeterminate",
          `progress-track--${tone}`,
        ]
          .filter(Boolean)
          .join(" ")}
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={determinate ? Math.round(value) : undefined}
        aria-valuetext={determinate ? `${Math.round(value)} percent` : "in progress"}
      >
        <span
          className="progress-fill"
          style={determinate ? { width: `${value}%` } : undefined}
        />
      </div>
    </div>
  );
}
