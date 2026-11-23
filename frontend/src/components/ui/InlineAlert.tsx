import type { ReactNode } from "react";
import type { Tone } from "../../lib/status";

interface InlineAlertProps {
  tone?: Tone;
  title?: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  className?: string;
}

export function InlineAlert({
  tone = "info",
  title,
  children,
  action,
  className,
}: InlineAlertProps) {
  const role = tone === "danger" ? "alert" : "status";
  return (
    <div
      className={["inline-alert", `inline-alert--${tone}`, className ?? ""]
        .filter(Boolean)
        .join(" ")}
      role={role}
    >
      <div className="inline-alert-body">
        {title ? <p className="inline-alert-title">{title}</p> : null}
        {children ? <div className="inline-alert-text">{children}</div> : null}
      </div>
      {action ? <div className="inline-alert-action">{action}</div> : null}
    </div>
  );
}
