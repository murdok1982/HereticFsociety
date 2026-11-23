import type { ReactNode } from "react";

interface TableWrapProps {
  /** Accessible name for the scrollable table region. */
  label: string;
  children: ReactNode;
  className?: string;
}

/**
 * Focusable scroll container for wide tables. Keyboard users can reach it with
 * Tab and scroll with the arrow keys; mobile rows collapse to cards instead.
 */
export function TableWrap({ label, children, className }: TableWrapProps) {
  return (
    <div
      className={["table-wrap", className ?? ""].filter(Boolean).join(" ")}
      role="region"
      aria-label={label}
      tabIndex={0}
    >
      {children}
    </div>
  );
}
