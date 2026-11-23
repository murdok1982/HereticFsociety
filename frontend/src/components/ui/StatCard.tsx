import type { ReactNode } from "react";
import { Icon, type IconName } from "./Icon";

interface StatCardProps {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  icon?: IconName;
  mono?: boolean;
  tone?: "default" | "success" | "warning" | "danger";
}

export function StatCard({
  label,
  value,
  hint,
  icon,
  mono = false,
  tone = "default",
}: StatCardProps) {
  return (
    <div className={`stat-card stat-card--${tone}`}>
      <div className="stat-card-head">
        <span className="stat-label">{label}</span>
        {icon ? (
          <span className="stat-icon" aria-hidden="true">
            <Icon name={icon} size={16} />
          </span>
        ) : null}
      </div>
      <div className={["stat-value", mono ? "mono" : ""].filter(Boolean).join(" ")}>
        {value}
      </div>
      {hint ? <div className="stat-hint">{hint}</div> : null}
    </div>
  );
}
