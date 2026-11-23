import { api } from "../api/client";
import type { PluginInfo } from "../api/types";
import { useApiQuery } from "../hooks/useApiQuery";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { formatNumber, pluralize } from "../lib/format";
import { AsyncState } from "../components/ui/AsyncState";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { EmptyState } from "../components/ui/EmptyState";
import { PageHeader, Panel } from "../components/ui/Panel";
import { StatsSkeleton, TableSkeleton } from "../components/ui/Skeleton";
import { StatCard } from "../components/ui/StatCard";
import { TableWrap } from "../components/ui/TableWrap";
import { JsonView } from "../components/ui/JsonView";

function PluginRow({ plugin }: { plugin: PluginInfo }) {
  return (
    <tr>
      <td data-label="Plugin">
        <div className="cell-primary">
          {plugin.name}
          {plugin.builtin ? (
            <Badge tone="neutral" className="badge--spaced">
              builtin
            </Badge>
          ) : null}
        </div>
        <div className="cell-sub">{plugin.description || "No description provided."}</div>
        <div className="cell-sub mono">{plugin.module}</div>
      </td>
      <td className="mono" data-label="Version">v{plugin.version}</td>
      <td className="mono" data-label="API">api {plugin.api_version}</td>
      <td data-label="Kind">
        <Badge tone="info">{plugin.kind}</Badge>
      </td>
      <td data-label="Status">
        <Badge tone={plugin.valid ? "success" : "danger"}>
          {plugin.valid ? "valid" : "invalid"}
        </Badge>
      </td>
      <td data-label="Permissions">
        {plugin.permissions.length === 0 ? (
          <span className="muted small">none declared</span>
        ) : (
          <div className="badge-row">
            {plugin.permissions.map((permission) => (
              <Badge tone="warning" key={permission}>
                {permission}
              </Badge>
            ))}
          </div>
        )}
      </td>
      <td data-label="Diagnostics">
        {plugin.errors.length === 0 && plugin.warnings.length === 0 ? (
          <span className="muted small">—</span>
        ) : (
          <details className="details-block details-block--inline">
            <summary>
              {pluralize(plugin.errors.length, "error")} · {pluralize(plugin.warnings.length, "warning")}
            </summary>
            {plugin.errors.length > 0 ? (
              <ul className="plain-list small danger-text">
                {plugin.errors.map((error) => (
                  <li key={error}>{error}</li>
                ))}
              </ul>
            ) : null}
            {plugin.warnings.length > 0 ? (
              <ul className="plain-list small warning-text">
                {plugin.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            ) : null}
          </details>
        )}
      </td>
      <td className="small mono cell-wrap" data-label="Path / metadata">
        {plugin.path ?? "—"}
        <details className="details-block details-block--inline">
          <summary>Metadata</summary>
          <JsonView value={plugin} maxHeight={220} />
        </details>
      </td>
    </tr>
  );
}

export function PluginsPage() {
  useDocumentTitle("Plugins");
  const plugins = useApiQuery((signal) => api.getPlugins(signal), []);

  return (
    <>
      <PageHeader
        eyebrow="Extensions"
        title="Plugin Manager"
        description="Discovered plugin entry points with API version, declared permissions and validation errors. Invalid plugins are reported, never silently loaded."
        actions={
          <Button icon="refresh" onClick={plugins.reload} loading={plugins.refreshing}>
            Refresh registry
          </Button>
        }
      />

      {plugins.data ? (
        <div className="grid grid-4 stat-grid">
          <StatCard
            label="Plugins"
            value={formatNumber(plugins.data.items.length)}
            hint="Discovered entry points"
            icon="plugins"
          />
          <StatCard
            label="Valid"
            value={formatNumber(plugins.data.items.filter((plugin) => plugin.valid).length)}
            hint="Passed validation"
            tone="success"
          />
          <StatCard
            label="Invalid"
            value={formatNumber(plugins.data.items.filter((plugin) => !plugin.valid).length)}
            hint="Rejected with errors"
            tone={
              plugins.data.items.some((plugin) => !plugin.valid) ? "danger" : "default"
            }
          />
          <StatCard
            label="With permissions"
            value={formatNumber(
              plugins.data.items.filter((plugin) => plugin.permissions.length > 0).length,
            )}
            hint="Declare host capabilities"
            tone="warning"
          />
        </div>
      ) : (
        <StatsSkeleton />
      )}

      <Panel title="Registry" subtitle="Live plugin discovery output" flush>
        <AsyncState
          query={plugins}
          skeleton={<TableSkeleton rows={5} />}
          context="Plugin registry"
          isEmpty={(data) => data.items.length === 0}
          empty={
            <EmptyState
              title="No plugins discovered"
              description="The plugin registry returned no entry points. Install a plugin package under the workspace plugins directory and restart or reload the API."
              icon="plugins"
              action={
                <Button variant="secondary" size="sm" icon="refresh" onClick={plugins.reload}>
                  Refresh registry
                </Button>
              }
            />
          }
        >
          {(data) => (
            <TableWrap label="Plugin registry">
              <table className="data-table data-table--stack">
                <caption className="sr-only">Discovered plugins</caption>
                <thead>
                  <tr>
                    <th scope="col">Plugin</th>
                    <th scope="col">Version</th>
                    <th scope="col">API</th>
                    <th scope="col">Kind</th>
                    <th scope="col">Status</th>
                    <th scope="col">Permissions</th>
                    <th scope="col">Diagnostics</th>
                    <th scope="col">Path / metadata</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((plugin) => (
                    <PluginRow key={`${plugin.module}:${plugin.name}`} plugin={plugin} />
                  ))}
                </tbody>
              </table>
            </TableWrap>
          )}
        </AsyncState>
      </Panel>
    </>
  );
}
