import type { CSSProperties } from "react";

interface SkeletonProps {
  width?: string;
  height?: string;
  className?: string;
  style?: CSSProperties;
}

export function Skeleton({
  width = "100%",
  height = "1rem",
  className,
  style,
}: SkeletonProps) {
  return (
    <span
      className={["skeleton", className ?? ""].filter(Boolean).join(" ")}
      style={{ width, height, ...style }}
      aria-hidden="true"
    />
  );
}

export function SkeletonLines({ count = 3 }: { count?: number }) {
  return (
    <div className="skeleton-lines" aria-hidden="true">
      {Array.from({ length: count }, (_, index) => (
        <Skeleton
          key={index}
          width={index === count - 1 ? "62%" : "100%"}
          height="0.8rem"
        />
      ))}
    </div>
  );
}

export function PanelSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="panel panel--skeleton" aria-hidden="true">
      <div className="panel-head">
        <Skeleton width="32%" height="1rem" />
        <Skeleton width="12%" height="0.8rem" />
      </div>
      <div className="panel-body">
        <div className="skeleton-stack">
          {Array.from({ length: rows }, (_, index) => (
            <Skeleton
              key={index}
              width={index % 3 === 0 ? "92%" : index % 3 === 1 ? "76%" : "88%"}
              height="0.8rem"
            />
          ))}
        </div>
      </div>
    </div>
  );
}

export function StatsSkeleton({ count = 4 }: { count?: number }) {
  return (
    <div className="grid grid-4 stat-grid" aria-hidden="true">
      {Array.from({ length: count }, (_, index) => (
        <div className="stat-card stat-card--skeleton" key={index}>
          <div className="stat-card-head">
            <Skeleton width="42%" height="0.625rem" />
          </div>
          <Skeleton
            width="76%"
            height="1.35rem"
            style={{ marginTop: 13 }}
          />
          <Skeleton
            width="54%"
            height="0.625rem"
            style={{ marginTop: 9 }}
          />
        </div>
      ))}
    </div>
  );
}

export function TableSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div className="table-skeleton" aria-hidden="true">
      {Array.from({ length: rows }, (_, index) => (
        <div className="table-skeleton-row" key={index}>
          <Skeleton width="26%" height="0.85rem" />
          <Skeleton width="14%" height="0.85rem" />
          <Skeleton width="18%" height="0.85rem" />
          <Skeleton width="13%" height="0.85rem" />
        </div>
      ))}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="spinner-wrap" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      {label ? <span className="spinner-label">{label}</span> : null}
    </span>
  );
}
