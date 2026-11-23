import { api } from "../api/client";
import { useApiQuery } from "../hooks/useApiQuery";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { formatBytes, pluralize } from "../lib/format";
import { ArtifactTable } from "../components/ui/ArtifactTable";
import { AsyncState } from "../components/ui/AsyncState";
import { Button } from "../components/ui/Button";
import { EmptyState } from "../components/ui/EmptyState";
import { Icon } from "../components/ui/Icon";
import { InlineAlert } from "../components/ui/InlineAlert";
import { PageHeader, Panel } from "../components/ui/Panel";
import { TableSkeleton } from "../components/ui/Skeleton";

export function ExportsPage() {
  useDocumentTitle("Export Center");
  const exportsQuery = useApiQuery((signal) => api.getExports(signal), []);
  const items = exportsQuery.data?.items ?? [];
  const totalBytes = items.reduce((sum, artifact) => sum + artifact.size_bytes, 0);

  return (
    <>
      <PageHeader
        eyebrow="Delivery"
        title="Export Center"
        description="Export bundles and generated artifacts recorded by the platform. The dashboard lists what exists; bundle creation is performed by the CLI."
        actions={
          <Button
            icon="refresh"
            onClick={exportsQuery.reload}
            loading={exportsQuery.refreshing}
          >
            Refresh
          </Button>
        }
      />

      <InlineAlert tone="info" title="Read-only view">
        <p>
          Create bundles from a terminal with{" "}
          <code className="inline-code">fsociety export --help</code>. The API records
          each bundle as an artifact with its SHA-256 and size; this page does not
          write to the workspace.
        </p>
      </InlineAlert>

      <Panel
        title="Exported artifacts"
        subtitle={
          exportsQuery.data
            ? `${pluralize(items.length, "bundle")} · ${formatBytes(totalBytes)} total`
            : "Loading export inventory"
        }
        flush
      >
        <AsyncState
          query={exportsQuery}
          skeleton={<TableSkeleton rows={4} />}
          context="Export inventory"
          isEmpty={(data) => data.items.length === 0}
          empty={
            <EmptyState
              title="No export bundles yet"
              description="Bundles are created from the CLI and registered through the API. Run the export command against the workspace, then return here to inspect hashes and paths."
              icon="exports"
              action={
                <div className="export-command">
                  <Icon name="terminal" size={15} />
                  <code>fsociety export --help</code>
                </div>
              }
            />
          }
        >
          {(data) => <ArtifactTable items={data.items} showExperiment />}
        </AsyncState>
      </Panel>

      <div className="page-footnote">
        <Icon name="exports" size={14} />
        <span>
          Downloads are served by the host filesystem, not the dashboard. Paths are
          displayed exactly as recorded by the backend.
        </span>
      </div>
    </>
  );
}
