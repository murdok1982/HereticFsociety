import { api } from "../api/client";
import type { SbomComponent, SbomDocument, SecurityPolicy } from "../api/types";
import { useApiQuery } from "../hooks/useApiQuery";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useNow } from "../hooks/useNow";
import { formatDateTime, formatNumber, formatRelativeTime, pluralize } from "../lib/format";
import { toneForLevel, toneForSeverity } from "../lib/status";
import { AsyncState } from "../components/ui/AsyncState";
import { Badge, StatusBadge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { EmptyState } from "../components/ui/EmptyState";
import { InlineAlert } from "../components/ui/InlineAlert";
import { JsonView, KeyValueGrid } from "../components/ui/JsonView";
import { PageHeader, Panel } from "../components/ui/Panel";
import { StatsSkeleton, TableSkeleton } from "../components/ui/Skeleton";
import { StatCard } from "../components/ui/StatCard";
import { TableWrap } from "../components/ui/TableWrap";

function trustTone(mode: string | null | undefined): "success" | "warning" | "danger" | "neutral" {
  if (mode === "always") return "danger";
  if (mode === "never" || mode === "deny") return "success";
  if (!mode) return "neutral";
  return "warning";
}

function safeArray<T>(value: T[] | null | undefined): T[] {
  return Array.isArray(value) ? value : [];
}

function sbomFileName(sbom: SbomDocument): string {
  const serial = (sbom.serialNumber || "cyclonedx").replace(/[^A-Za-z0-9._-]+/g, "_");
  return `fsociety-sbom-${serial}.json`;
}

function componentLicense(component: SbomComponent): string {
  if (!Array.isArray(component.licenses)) return "—";
  const names = component.licenses
    .map((entry) => entry?.license?.id ?? entry?.license?.name ?? "")
    .filter((value): value is string => Boolean(value));
  return names.length > 0 ? names.join(", ") : "—";
}

function PolicyPanel({ policy }: { policy: SecurityPolicy }) {
  const trustMode = policy.trust_remote_code ?? "unknown";
  const trustedRepositories = safeArray(policy.trusted_repositories);
  const corsOrigins = safeArray(policy.cors_origins);
  const warnings = safeArray(policy.warnings);
  const authKnown = typeof policy.api_auth_required === "boolean";
  const authRequired = policy.api_auth_required === true;
  const keyConfigured = policy.api_key_configured === true;
  const isolationKnown = typeof policy.plugin_isolation === "boolean";
  const isolationEnabled = policy.plugin_isolation === true;
  const networkKnown = typeof policy.plugin_allow_network === "boolean";
  const networkEnabled = policy.plugin_allow_network === true;

  return (
    <Panel
      title="Security policy"
      subtitle="Effective trust and isolation posture reported by the API"
      actions={
        <Badge tone={authKnown ? (authRequired ? "success" : "warning") : "neutral"}>
          {authKnown ? (authRequired ? "auth required" : "auth optional") : "auth unknown"}
        </Badge>
      }
    >
      {trustMode === "always" ? (
        <InlineAlert tone="danger" title="Remote code execution is globally enabled">
          Any model repository may execute Python in the API process. Restrict
          <code> trust_remote_code </code> to an allowlist before exposing this API.
        </InlineAlert>
      ) : null}
      {authKnown && !authRequired ? (
        <InlineAlert tone="warning" title="API authentication is not required">
          Bind the API to loopback or configure an API key before exposing it on a
          network interface.
        </InlineAlert>
      ) : null}
      {corsOrigins.includes("*") ? (
        <InlineAlert tone="danger" title="Wildcard CORS origin">
          Any browser origin can call the API. Replace <code>*</code> with explicit
          dashboard origins.
        </InlineAlert>
      ) : null}
      {networkKnown && networkEnabled ? (
        <InlineAlert tone="warning" title="Plugins may access the network">
          Isolated plugin subprocesses can reach external services. Keep
          <code> plugin_allow_network </code> disabled unless required.
        </InlineAlert>
      ) : null}

      <div className="badge-row">
        <Badge tone={trustTone(trustMode)}>trust_remote_code: {trustMode}</Badge>
        <Badge tone={isolationKnown ? (isolationEnabled ? "success" : "danger") : "neutral"}>
          plugin isolation: {isolationKnown ? (isolationEnabled ? "on" : "off") : "unknown"}
        </Badge>
        <Badge tone={keyConfigured ? "success" : "warning"}>
          API key {keyConfigured ? "configured" : "not configured"}
        </Badge>
        <Badge tone="neutral" mono>
          timeout{" "}
          {policy.plugin_timeout_seconds !== undefined
            ? `${policy.plugin_timeout_seconds}s`
            : "unknown"}
        </Badge>
      </div>

      <KeyValueGrid
        columns={2}
        data={{
          trust_remote_code: trustMode,
          trusted_repositories:
            trustedRepositories.length > 0 ? trustedRepositories.join(", ") : "none",
          plugin_isolation: policy.plugin_isolation,
          plugin_timeout_seconds: policy.plugin_timeout_seconds,
          plugin_allow_network: policy.plugin_allow_network,
          api_auth_required: policy.api_auth_required,
          api_key_configured: policy.api_key_configured,
          cors_origins:
            corsOrigins.length > 0 ? corsOrigins.join(", ") : "none",
        }}
      />

      {warnings.length > 0 ? (
        <InlineAlert tone="warning" title="Policy warnings">
          <ul className="plain-list">
            {warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </InlineAlert>
      ) : null}

      <details className="details-block">
        <summary>Raw policy payload</summary>
        <JsonView value={policy} maxHeight={240} />
      </details>
    </Panel>
  );
}

export function SecurityPage() {
  useDocumentTitle("Security");
  const now = useNow(15_000);
  const policy = useApiQuery((signal) => api.getSecurityPolicy(signal), []);
  const audit = useApiQuery((signal) => api.getAuditEvents(100, signal), []);
  const findings = useApiQuery((signal) => api.getFindings(signal), []);
  const sbom = useApiQuery((signal) => api.getSbom(signal), []);

  const reloadAll = (): void => {
    policy.reload();
    audit.reload();
    findings.reload();
    sbom.reload();
  };

  const downloadSbom = (): void => {
    if (!sbom.data) return;
    const blob = new Blob([JSON.stringify(sbom.data, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = sbomFileName(sbom.data);
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  };

  const policyData = policy.data;
  const trustedRepositories = safeArray(policyData?.trusted_repositories);
  const findingsItems = safeArray(findings.data?.items);
  const openFindings = findingsItems.filter(
    (finding) => finding.status !== "resolved",
  ).length;
  const sbomComponents = safeArray(sbom.data?.components);

  return (
    <>
      <PageHeader
        eyebrow="Assurance"
        title="Security"
        description="Policy, audit trail, configuration findings and the CycloneDX software bill of materials, all sourced from the backend."
        actions={
          <Button icon="refresh" onClick={reloadAll} loading={policy.refreshing}>
            Refresh all
          </Button>
        }
      />

      <div className="grid grid-4 stat-grid">
        {policyData ? (
          <>
            <StatCard
              label="Trust policy"
              value={policyData.trust_remote_code ?? "unknown"}
              hint={`${trustedRepositories.length} allowlisted repos`}
              tone={
                policyData.trust_remote_code === "always"
                  ? "danger"
                  : policyData.trust_remote_code === "never"
                    ? "success"
                    : "warning"
              }
              mono
              icon="security"
            />
            <StatCard
              label="API auth"
              value={
                policyData.api_auth_required === true
                  ? "required"
                  : policyData.api_auth_required === false
                    ? "optional"
                    : "unknown"
              }
              hint={policyData.api_key_configured ? "API key configured" : "no API key"}
              tone={
                policyData.api_auth_required === true
                  ? "success"
                  : policyData.api_auth_required === false
                    ? "warning"
                    : "default"
              }
              mono
            />
            <StatCard
              label="Open findings"
              value={findings.data ? formatNumber(openFindings) : "—"}
              hint={
                findings.data
                  ? `${pluralize(findingsItems.length, "total finding")}`
                  : "findings unavailable"
              }
              tone={openFindings > 0 ? "warning" : "success"}
              icon="alert"
            />
            <StatCard
              label="SBOM components"
              value={sbom.data ? formatNumber(sbomComponents.length) : "—"}
              hint={sbom.data ? `${sbom.data.bomFormat} ${sbom.data.specVersion}` : "SBOM unavailable"}
              icon="exports"
              mono
            />
          </>
        ) : (
          <div className="span-all">
            <StatsSkeleton />
          </div>
        )}
      </div>

      <AsyncState
        query={policy}
        skeleton={<TableSkeleton rows={5} />}
        context="Security policy"
      >
        {(data) => <PolicyPanel policy={data} />}
      </AsyncState>

      <div className="grid grid-2">
        <Panel
          title="Findings"
          subtitle="Live configuration checks — not a CVE scan"
          actions={<Badge tone={openFindings > 0 ? "warning" : "success"}>{openFindings} open</Badge>}
          flush
        >
          <AsyncState
            query={findings}
            skeleton={<TableSkeleton rows={4} />}
            isEmpty={(data) => data.items.length === 0}
            empty={
              <EmptyState
                title="No configuration findings"
                description="The backend evaluated live settings and produced no findings. Findings are derived from configuration, not dependency CVE scans."
                icon="security"
                compact
              />
            }
          >
            {(data) => (
              <TableWrap label="Security findings">
                <table className="data-table data-table--compact data-table--stack">
                  <caption className="sr-only">Security findings</caption>
                  <thead>
                    <tr>
                      <th scope="col">Severity</th>
                      <th scope="col">Finding</th>
                      <th scope="col">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((finding) => (
                      <tr key={`${finding.title}-${finding.status}`}>
                        <td data-label="Severity">
                          <Badge tone={toneForSeverity(finding.severity)}>
                            {finding.severity}
                          </Badge>
                        </td>
                        <td data-label="Finding">
                          <div className="cell-primary">{finding.title}</div>
                          <div className="cell-sub">{finding.detail}</div>
                        </td>
                        <td data-label="Status">
                          <StatusBadge status={finding.status} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            )}
          </AsyncState>
        </Panel>

        <Panel
          title="Software bill of materials"
          subtitle="CycloneDX document served by the API"
          actions={
            <>
              <Badge tone="neutral" mono>
                {sbom.data ? `${sbomComponents.length} components` : "—"}
              </Badge>
              <Button
                variant="secondary"
                size="sm"
                icon="download"
                onClick={downloadSbom}
                disabled={!sbom.data}
              >
                Download JSON
              </Button>
            </>
          }
          flush
        >
          <AsyncState
            query={sbom}
            skeleton={<TableSkeleton rows={4} />}
            context="SBOM"
          >
            {(data) => {
              const components = safeArray(data.components);
              return (
                <div className="sbom-panel">
                  <KeyValueGrid
                    data={{
                      bomFormat: data.bomFormat,
                      specVersion: data.specVersion,
                      serialNumber: data.serialNumber,
                      components: components.length,
                    }}
                  />
                  {components.length === 0 ? (
                    <EmptyState
                      title="No SBOM components"
                      description="The backend returned a document with an empty component list. Dependency metadata may be unavailable in this environment."
                      icon="exports"
                      compact
                    />
                  ) : (
                    <TableWrap label="SBOM components">
                      <table className="data-table data-table--compact data-table--stack">
                        <caption className="sr-only">SBOM components</caption>
                        <thead>
                          <tr>
                            <th scope="col">Name</th>
                            <th scope="col">Version</th>
                            <th scope="col">Type</th>
                            <th scope="col">License</th>
                          </tr>
                        </thead>
                        <tbody>
                          {components.slice(0, 100).map((component, index) => (
                            <tr key={component.purl ?? `${component.name}-${index}`}>
                              <td className="cell-wrap" data-label="Name">
                                <div className="cell-primary">
                                  {component.name ?? "unnamed"}
                                </div>
                                {component.purl ? (
                                  <div className="cell-sub mono">{component.purl}</div>
                                ) : null}
                              </td>
                              <td className="mono" data-label="Version">{component.version ?? "—"}</td>
                              <td data-label="Type">{component.type ?? "—"}</td>
                              <td className="small" data-label="License">{componentLicense(component)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </TableWrap>
                  )}
                  {components.length > 100 ? (
                    <p className="table-note">
                      Showing 100 of {components.length} components. Download the JSON
                      for the full document.
                    </p>
                  ) : null}
                  <details className="details-block">
                    <summary>Document metadata</summary>
                    <JsonView value={data.metadata} maxHeight={260} />
                  </details>
                </div>
              );
            }}
          </AsyncState>
        </Panel>
      </div>

      <Panel
        title="Audit log"
        subtitle="Latest 100 security and system events"
        actions={
          <Button
            variant="ghost"
            size="sm"
            icon="refresh"
            onClick={audit.reload}
            loading={audit.refreshing}
            aria-label="Refresh audit log"
          />
        }
        flush
      >
        <AsyncState
          query={audit}
          skeleton={<TableSkeleton rows={6} />}
          isEmpty={(data) => data.items.length === 0}
          empty={
            <EmptyState
              title="No audit events"
              description="Events are written when jobs, plugin runs, security checks and API actions occur. The log is empty for this workspace."
              icon="security"
              compact
            />
          }
        >
          {(data) => (
            <TableWrap label="Audit events">
              <table className="data-table data-table--compact data-table--stack">
                <caption className="sr-only">Audit events</caption>
                <thead>
                  <tr>
                    <th scope="col">Time</th>
                    <th scope="col">Level</th>
                    <th scope="col">Category</th>
                    <th scope="col">Message</th>
                    <th scope="col">Data</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((event) => (
                    <tr key={event.event_id ?? `${event.created_at}-${event.message}`}>
                      <td data-label="Time" title={formatDateTime(event.created_at)}>
                        {formatRelativeTime(event.created_at, now)}
                      </td>
                      <td data-label="Level">
                        <Badge tone={toneForLevel(event.level)}>{event.level}</Badge>
                      </td>
                      <td className="mono small" data-label="Category">{event.category}</td>
                      <td data-label="Message">{event.message}</td>
                      <td data-label="Data">
                        {Object.keys(event.data).length > 0 ? (
                          <details className="details-block details-block--inline">
                            <summary>View</summary>
                            <JsonView value={event.data} maxHeight={180} />
                          </details>
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          )}
        </AsyncState>
      </Panel>

      <div className="page-footnote">
        <span>
          Findings reflect configuration only. Dependency CVEs are reported by the
          SBOM and external audit tooling, not fabricated here.
        </span>
      </div>
    </>
  );
}
