import { useEffect, useState } from "react";
import { api, API_LINKS } from "../api/client";
import type { EngineStatus, PlatformSettings, SystemInfo } from "../api/types";
import { useApiKey } from "../hooks/useApiKey";
import { useApiQuery } from "../hooks/useApiQuery";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useMutation } from "../hooks/useMutation";
import { formatDateTime, formatValue, humanizeKey, isRecord } from "../lib/format";
import { AsyncState } from "../components/ui/AsyncState";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { CheckboxField, TextField } from "../components/ui/FormField";
import { Icon } from "../components/ui/Icon";
import { InlineAlert } from "../components/ui/InlineAlert";
import { JsonView, KeyValueGrid } from "../components/ui/JsonView";
import { PageHeader, Panel } from "../components/ui/Panel";
import { PanelSkeleton, TableSkeleton } from "../components/ui/Skeleton";

function readBoolean(
  data: PlatformSettings | null,
  ...keys: string[]
): boolean | null {
  if (!data) return null;
  for (const key of keys) {
    const value = data[key];
    if (typeof value === "boolean") return value;
  }
  return null;
}

function SettingsGroups({ data }: { data: PlatformSettings }) {
  const primitives: Record<string, unknown> = {};
  const groups: Array<[string, Record<string, unknown>]> = [];
  const arrays: Array<[string, unknown[]]> = [];

  for (const [key, value] of Object.entries(data)) {
    if (isRecord(value)) {
      groups.push([key, value]);
    } else if (Array.isArray(value)) {
      arrays.push([key, value]);
    } else {
      primitives[key] = value;
    }
  }

  return (
    <>
      {Object.keys(primitives).length > 0 ? (
        <Panel title="Core settings" subtitle="Scalar values after server-side redaction">
          <KeyValueGrid columns={2} data={primitives} />
        </Panel>
      ) : null}

      {arrays.map(([key, values]) => (
        <Panel key={key} title={humanizeKey(key)} subtitle="List values">
          {values.length === 0 ? (
            <p className="muted small">Empty list.</p>
          ) : (
            <div className="badge-row">
              {values.map((value, index) => (
                <Badge tone="neutral" mono key={`${key}-${index}`}>
                  {formatValue(value)}
                </Badge>
              ))}
            </div>
          )}
        </Panel>
      ))}

      {groups.map(([key, group]) => (
        <Panel key={key} title={humanizeKey(key)} subtitle="Nested configuration group">
          <KeyValueGrid columns={2} data={group} />
        </Panel>
      ))}

      <Panel title="Raw settings" subtitle="Exact redacted payload from the API">
        <details className="details-block">
          <summary>Show JSON</summary>
          <JsonView value={data} maxHeight={420} />
        </details>
      </Panel>
    </>
  );
}

function EngineSettingsPanel({ engine }: { engine: EngineStatus }) {
  return (
    <Panel
      title="Modification engine"
      subtitle="Heretic runtime availability"
      actions={
        <Badge tone={engine.available ? "success" : "warning"}>
          {engine.available ? "available" : "unavailable"}
        </Badge>
      }
    >
      <KeyValueGrid
        data={{
          heretic_version: engine.heretic_version,
          torch_version: engine.torch_version,
          transformers_version: engine.transformers_version,
          missing: engine.missing.length > 0 ? engine.missing.join(", ") : "none",
        }}
      />
      {!engine.available ? (
        <InlineAlert tone="warning" title={engine.message}>
          {engine.hint ? <p className="mono small">{engine.hint}</p> : null}
          <p className="small">
            The engine is optional for the dashboard and the API. Jobs that require it
            will be rejected by the backend rather than simulated here.
          </p>
        </InlineAlert>
      ) : (
        <p className="muted small">{engine.message}</p>
      )}
    </Panel>
  );
}

function VersionPanel({ version }: { version: SystemInfo }) {
  return (
    <Panel title="Platform" subtitle="Build identity reported by /version">
      <KeyValueGrid
        data={{
          platform_name: version.platform_name,
          version: version.version,
          api_version: version.api_version,
          plugin_api_version: version.plugin_api_version,
          python_version: version.python_version,
          engine_available: version.engine_available,
          engine_version: version.engine_version,
          workspace: version.workspace,
          started_at: formatDateTime(version.started_at),
        }}
      />
    </Panel>
  );
}

export function SettingsPage() {
  useDocumentTitle("Settings");
  const { apiKey, configured, save, clear } = useApiKey();
  const [draft, setDraft] = useState(apiKey);
  const [showKey, setShowKey] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const settings = useApiQuery(
    (signal) => api.getSettings(signal),
    [apiKey],
    { keepPreviousData: false },
  );
  const engine = useApiQuery((signal) => api.getEngine(signal), [apiKey]);
  const version = useApiQuery((signal) => api.getVersion(signal), [apiKey]);
  const test = useMutation((_input: null, signal) => api.testConnection(signal));

  useEffect(() => {
    setDraft(apiKey);
  }, [apiKey]);

  const saveAndTest = async (): Promise<void> => {
    setNotice(null);
    save(draft);
    const result = await test.run(null);
    if (result) {
      setNotice("Authenticated. The redacted settings were loaded with this key.");
    }
  };

  const clearKey = (): void => {
    clear();
    setDraft("");
    setShowKey(false);
    setNotice("Session key removed. Requests are now unauthenticated.");
    test.reset();
  };

  const serverKeyConfigured = readBoolean(
    settings.data,
    "api_key_configured",
    "apiKeyConfigured",
  );

  return (
    <>
      <PageHeader
        eyebrow="Configuration"
        title="Settings"
        description="Read-only, server-redacted configuration plus the operator API key for this browser tab."
        actions={
          <Button
            icon="refresh"
            onClick={() => {
              settings.reload();
              engine.reload();
              version.reload();
            }}
            loading={settings.refreshing}
          >
            Refresh
          </Button>
        }
      />

      <InlineAlert tone="info" title="Secrets are never exposed">
        <p>
          The API returns a redacted configuration. API keys, tokens and passwords are
          never displayed. To change server configuration, edit the platform config
          file or use the CLI; this dashboard cannot write settings.
        </p>
      </InlineAlert>

      <Panel
        id="api-key"
        title="API access"
        subtitle="Used as the X-API-Key header when the backend requires authentication"
        actions={
          <Badge tone={configured ? "success" : "neutral"}>
            {configured ? "session key set" : "no session key"}
          </Badge>
        }
      >
        <div className="form-grid form-grid--3">
          <TextField
            label="API key"
            type={showKey ? "text" : "password"}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="paste the configured API key"
            autoComplete="off"
            spellCheck={false}
            hint="Stored in sessionStorage only. Closing the tab forgets it."
            className="field--span-2"
          />
          <div className="field-align-end">
            <CheckboxField
              label="Show key"
              checked={showKey}
              onChange={setShowKey}
            />
          </div>
        </div>

        <div className="form-actions">
          <Button
            variant="primary"
            icon="security"
            loading={test.pending}
            onClick={() => void saveAndTest()}
          >
            Save &amp; test connection
          </Button>
          <Button
            variant="ghost"
            icon="trash"
            onClick={clearKey}
            disabled={!configured && !draft}
          >
            Clear key
          </Button>
          {serverKeyConfigured !== null ? (
            <Badge tone={serverKeyConfigured ? "success" : "warning"}>
              server key {serverKeyConfigured ? "configured" : "not configured"}
            </Badge>
          ) : null}
        </div>

        {notice ? (
          <InlineAlert tone="success" title="Connection">
            {notice}
          </InlineAlert>
        ) : null}

        {test.error ? (
          <InlineAlert tone="danger" title={`Connection test failed · ${test.error.code}`}>
            <p>{test.error.message}</p>
            {test.error.hint ? <p className="small">{test.error.hint}</p> : null}
          </InlineAlert>
        ) : null}

        <KeyValueGrid
          data={{
            api_base: API_LINKS.base,
            key_source: "sessionStorage",
            authorization_header: getApiKeyHeader(),
          }}
        />
      </Panel>

      <div className="grid grid-2">
        <AsyncState
          query={engine}
          skeleton={<PanelSkeleton rows={3} />}
          context="Engine status"
        >
          {(data) => <EngineSettingsPanel engine={data} />}
        </AsyncState>
        <AsyncState
          query={version}
          skeleton={<PanelSkeleton rows={4} />}
          context="Version"
        >
          {(data) => <VersionPanel version={data} />}
        </AsyncState>
      </div>

      <AsyncState
        query={settings}
        skeleton={<TableSkeleton rows={6} />}
        context="Redacted settings"
      >
        {(data) => <SettingsGroups data={data} />}
      </AsyncState>

      <Panel title="Documentation & probes" subtitle="Direct links to backend surfaces">
        <ul className="link-list">
          <li>
            <a href={API_LINKS.openapi} target="_blank" rel="noreferrer">
              <span>OpenAPI reference</span>
              <Icon name="external" size={14} />
            </a>
            <span className="muted small">
              Interactive FastAPI docs (availability depends on the deployment).
            </span>
          </li>
          <li>
            <a href={API_LINKS.health} target="_blank" rel="noreferrer">
              <span>Health probe</span>
              <Icon name="external" size={14} />
            </a>
            <span className="muted small">Raw JSON status used by the sidebar.</span>
          </li>
          <li>
            <a
              href="https://github.com/murdok1982/HereticFsociety"
              target="_blank"
              rel="noreferrer"
            >
              <span>Repository documentation</span>
              <Icon name="external" size={14} />
            </a>
            <span className="muted small">Setup, CLI and architecture notes.</span>
          </li>
        </ul>
        <p className="small muted">
          Live uptime and health checks are shown on the Overview page. This panel only
          links to documented backend surfaces.
        </p>
      </Panel>
    </>
  );
}

function getApiKeyHeader(): string {
  return "X-API-Key";
}
