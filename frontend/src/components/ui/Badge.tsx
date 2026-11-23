import type { ReactNode } from "react";
import type { Tone } from "../../lib/status";
import { toneForStatus } from "../../lib/status";

interface BadgeProps {
  children: ReactNode;
  tone?: Tone;
  mono?: boolean;
  title?: string;
  className?: string;
}

export function Badge({
  children,
  tone = "neutral",
  mono = false,
  title,
  className,
}: BadgeProps) {
  return (
    <span
      className={[
        "badge",
        `badge--${tone}`,
        mono ? "badge--mono" : "",
        className ?? "",
      ]
        .filter(Boolean)
        .join(" ")}
      title={title}
    >
      {children}
    </span>
  );
}

interface StatusBadgeProps {
  status: string | null | undefined;
  label?: string;
  title?: string;
}

export function StatusBadge({ status, label, title }: StatusBadgeProps) {
  const tone = toneForStatus(status);
  const text = label ?? (status ? status.replace(/[_-]+/g, " ") : "unknown");
  return (
    <Badge tone={tone} title={title} className="status-badge">
      <span className={`dot dot--${tone}`} aria-hidden="true" />
      {text}
    </Badge>
  );
}
