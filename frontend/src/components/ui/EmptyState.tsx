import type { ReactNode } from "react";
import { Icon, type IconName } from "./Icon";

interface EmptyStateProps {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  icon?: IconName;
  compact?: boolean;
}

export function EmptyState({
  title,
  description,
  action,
  icon = "models",
  compact = false,
}: EmptyStateProps) {
  return (
    <div className={["empty-state", compact ? "empty-state--compact" : ""].filter(Boolean).join(" ")}>
      <span className="empty-state-icon" aria-hidden="true">
        <Icon name={icon} size={compact ? 20 : 26} />
      </span>
      <div className="empty-state-copy">
        <p className="empty-state-title">{title}</p>
        {description ? <div className="empty-state-description">{description}</div> : null}
        {action ? <div className="empty-state-action">{action}</div> : null}
      </div>
    </div>
  );
}
