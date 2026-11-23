import type { ReactNode } from "react";
import { formatValue, humanizeKey, isRecord } from "../../lib/format";

export function JsonView({
  value,
  maxHeight = 360,
}: {
  value: unknown;
  maxHeight?: number;
}) {
  let text: string;
  try {
    text = JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    text = String(value);
  }
  return (
    <pre className="code-block" style={{ maxHeight }} tabIndex={0}>
      {text}
    </pre>
  );
}

interface KeyValueGridProps {
  data: Record<string, unknown>;
  columns?: number;
  emptyLabel?: string;
  renderValue?: (value: unknown, key: string) => ReactNode;
}

export function KeyValueGrid({
  data,
  columns = 2,
  emptyLabel = "No values reported.",
  renderValue,
}: KeyValueGridProps) {
  const entries = Object.entries(data);
  if (entries.length === 0) {
    return <p className="muted small">{emptyLabel}</p>;
  }
  const safeColumns = Math.min(3, Math.max(1, columns));
  return (
    <dl className={`kv-grid kv-grid--${safeColumns}`}>
      {entries.map(([key, value]) => (
        <div className="kv-item" key={key}>
          <dt>{humanizeKey(key)}</dt>
          <dd>
            {renderValue ? (
              renderValue(value, key)
            ) : isRecord(value) || Array.isArray(value) ? (
              <code className="inline-code">{formatValue(value)}</code>
            ) : (
              formatValue(value)
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}
