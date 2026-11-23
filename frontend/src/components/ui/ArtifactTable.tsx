import { Link } from "react-router-dom";
import type { ArtifactRecord } from "../../api/types";
import {
  formatBytes,
  formatDateTime,
  formatRelativeTime,
  shortHash,
} from "../../lib/format";
import { useNow } from "../../hooks/useNow";
import { Badge } from "./Badge";
import { JsonView } from "./JsonView";
import { TableWrap } from "./TableWrap";

function fileName(path: string): string {
  const segments = path.split(/[\\/]/).filter(Boolean);
  return segments.length > 0 ? segments[segments.length - 1] : path;
}

interface ArtifactTableProps {
  items: ArtifactRecord[];
  showExperiment?: boolean;
}

export function ArtifactTable({ items, showExperiment = false }: ArtifactTableProps) {
  const now = useNow(30_000);
  return (
    <TableWrap label="Stored artifacts">
      <table className="data-table data-table--stack">
        <caption className="sr-only">Stored artifacts</caption>
        <thead>
          <tr>
            <th scope="col">Artifact</th>
            <th scope="col">Kind</th>
            {showExperiment ? <th scope="col">Experiment</th> : null}
            <th scope="col">Size</th>
            <th scope="col">SHA-256</th>
            <th scope="col">Created</th>
            <th scope="col">Details</th>
          </tr>
        </thead>
        <tbody>
          {items.map((artifact) => {
            const metadataKeys = Object.keys(artifact.metadata ?? {});
            return (
              <tr key={artifact.artifact_id}>
                <td data-label="Artifact">
                  <div className="cell-primary mono">{fileName(artifact.path)}</div>
                  <div className="cell-sub mono" title={artifact.path}>
                    {artifact.path}
                  </div>
                </td>
                <td data-label="Kind">
                  <Badge tone="neutral">{artifact.kind}</Badge>
                </td>
                {showExperiment ? (
                  <td data-label="Experiment">
                    {artifact.experiment_id ? (
                      <Link
                        to={`/experiments/${encodeURIComponent(artifact.experiment_id)}`}
                        className="mono small"
                      >
                        {artifact.experiment_id}
                      </Link>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                ) : null}
                <td className="mono" data-label="Size">{formatBytes(artifact.size_bytes)}</td>
                <td data-label="SHA-256">
                  <code className="mono" title={artifact.sha256}>
                    {shortHash(artifact.sha256, 16)}
                  </code>
                </td>
                <td data-label="Created" title={formatDateTime(artifact.created_at)}>
                  {formatRelativeTime(artifact.created_at, now)}
                </td>
                <td data-label="Details">
                  {metadataKeys.length > 0 ? (
                    <details className="details-block details-block--inline">
                      <summary>Metadata</summary>
                      <JsonView value={artifact.metadata} maxHeight={200} />
                    </details>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </TableWrap>
  );
}
