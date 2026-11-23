import { humanizeKey } from "../../lib/format";
import { TableWrap } from "./TableWrap";

interface DynamicTableProps {
  rows: Array<Record<string, unknown>>;
  columns?: string[];
  columnLabels?: Record<string, string>;
  caption?: string;
  emptyLabel?: string;
  maxRows?: number;
}

function cellText(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

/**
 * Renders an array of arbitrary records (artifact metadata, per-category
 * metrics, comparison deltas) without assuming a fixed schema.
 */
export function DynamicTable({
  rows,
  columns,
  columnLabels,
  caption,
  emptyLabel = "No rows.",
  maxRows,
}: DynamicTableProps) {
  if (rows.length === 0) {
    return <p className="muted small">{emptyLabel}</p>;
  }

  const inferred = columns ?? inferColumns(rows);
  const visibleRows = maxRows ? rows.slice(0, maxRows) : rows;

  return (
    <TableWrap label={caption ?? "Data table"}>
      <table className="data-table data-table--compact data-table--stack">
        {caption ? <caption className="table-caption">{caption}</caption> : null}
        <thead>
          <tr>
            <th scope="col" className="cell-index">
              #
            </th>
            {inferred.map((column) => (
              <th scope="col" key={column}>
                {columnLabels?.[column] ?? humanizeKey(column)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {visibleRows.map((row, index) => (
            <tr key={index}>
              <td className="cell-index mono" data-label="#">{index + 1}</td>
              {inferred.map((column) => (
                <td
                  key={column}
                  className="cell-wrap"
                  data-label={columnLabels?.[column] ?? humanizeKey(column)}
                >
                  {cellText(row[column])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {maxRows && rows.length > maxRows ? (
        <p className="table-note">
          Showing {maxRows} of {rows.length} rows.
        </p>
      ) : null}
    </TableWrap>
  );
}

function inferColumns(rows: Array<Record<string, unknown>>): string[] {
  const seen: string[] = [];
  for (const row of rows.slice(0, 25)) {
    for (const key of Object.keys(row)) {
      if (!seen.includes(key)) seen.push(key);
    }
    if (seen.length >= 12) break;
  }
  return seen;
}
