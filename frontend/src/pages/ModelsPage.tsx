import { useMemo, useState } from "react";
import { api } from "../api/client";
import type {
  EstimateParams,
  ModelInfo,
  ModelValidation,
  ResourceEstimate,
  ScanRequest,
  VerifyRequest,
} from "../api/types";
import { useApiQuery } from "../hooks/useApiQuery";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useMutation } from "../hooks/useMutation";
import { useNow } from "../hooks/useNow";
import {
  formatBytes,
  formatCompact,
  formatDateTime,
  formatNumber,
  formatRelativeTime,
  shortHash,
  truncate,
} from "../lib/format";
import { AsyncState } from "../components/ui/AsyncState";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { CheckboxField, SelectField, TextAreaField, TextField } from "../components/ui/FormField";
import { Drawer } from "../components/ui/Drawer";
import { EmptyState } from "../components/ui/EmptyState";
import { InlineAlert } from "../components/ui/InlineAlert";
import { JsonView, KeyValueGrid } from "../components/ui/JsonView";
import { PageHeader, Panel } from "../components/ui/Panel";
import { TableSkeleton } from "../components/ui/Skeleton";
import { TableWrap } from "../components/ui/TableWrap";

export function ModelsPage() {
  useDocumentTitle("Model Manager");
  const now = useNow(30_000);
  const [includeHfCache, setIncludeHfCache] = useState(true);
  const [rootsInput, setRootsInput] = useState("");
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<ModelInfo | null>(null);
  const [scanWarnings, setScanWarnings] = useState<string[]>([]);

  const models = useApiQuery(
    (signal) => api.getModels(includeHfCache, signal),
    [includeHfCache],
  );
  const scan = useMutation((body: ScanRequest, signal) =>
    api.scanModels(body, signal),
  );

  const scanNow = async (): Promise<void> => {
    const roots = rootsInput
      .split(/[\n,]/)
      .map((root) => root.trim())
      .filter(Boolean);
    const result = await scan.run({ roots, include_hf_cache: includeHfCache });
    if (result) {
      setScanWarnings(result.warnings ?? []);
      models.reload();
    }
  };

  const filtered = useMemo(() => {
    const items = models.data?.items ?? [];
    const needle = filter.trim().toLowerCase();
    if (!needle) return items;
    return items.filter((model) => {
      const haystack = [
        model.model_id,
        model.path ?? "",
        model.architecture ?? "",
        model.model_type ?? "",
        model.source,
        ...(model.formats ?? []),
        ...(model.compatibility ?? []),
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(needle);
    });
  }, [models.data, filter]);

  return (
    <>
      <PageHeader
        eyebrow="Assets"
        title="Model Manager"
        description="Discover local and Hugging Face cached models, inspect transformer metadata, verify SHA-256 manifests and estimate memory requirements."
        actions={
          <Button icon="refresh" onClick={models.reload} loading={models.refreshing}>
            Refresh inventory
          </Button>
        }
      />

      <Panel
        title="Scan"
        subtitle="Roots are resolved inside the API workspace. Leave empty to scan the configured models directory."
      >
        <div className="form-grid form-grid--scan">
          <TextAreaField
            label="Scan roots"
            placeholder={"models/llama-3-8b\n/data/models/mistral-7b"}
            rows={3}
            value={rootsInput}
            onChange={(event) => setRootsInput(event.target.value)}
            hint="One directory per line (or comma-separated). Relative paths resolve against the workspace."
          />
          <div className="scan-controls">
            <CheckboxField
              label="Include Hugging Face cache"
              checked={includeHfCache}
              onChange={setIncludeHfCache}
              hint="Reads the local HF hub cache without downloading anything."
            />
            <div className="form-actions">
              <Button
                variant="primary"
                icon="models"
                loading={scan.pending}
                onClick={() => void scanNow()}
              >
                Scan models
              </Button>
            </div>
          </div>
        </div>

        {scan.error ? (
          <InlineAlert tone="danger" title={`Scan failed · ${scan.error.code}`}>
            <p>{scan.error.message}</p>
            {scan.error.hint ? <p className="small">{scan.error.hint}</p> : null}
            {scan.error.details !== undefined ? (
              <details className="details-block">
                <summary>Server details</summary>
                <JsonView value={scan.error.details} maxHeight={220} />
              </details>
            ) : null}
          </InlineAlert>
        ) : null}

        {scanWarnings.length > 0 ? (
          <InlineAlert tone="warning" title="Scan completed with warnings">
            <ul className="plain-list">
              {scanWarnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </InlineAlert>
        ) : null}

        {scan.data && !scan.pending ? (
          <p className="small muted">
            Scan inspected {formatNumber(scan.data.items.length)} model
            {scan.data.items.length === 1 ? "" : "s"}.
          </p>
        ) : null}
      </Panel>

      <Panel
        title="Inventory"
        subtitle={
          models.data
            ? `${formatNumber(models.data.items.length)} model${
                models.data.items.length === 1 ? "" : "s"
              } discovered`
            : "Loading model metadata"
        }
        flush
        actions={
          <div className="toolbar-inline">
            <TextField
              label="Filter models"
              type="search"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              placeholder="model id, path, architecture…"
              className="field--inline"
            />
          </div>
        }
      >
        <AsyncState
          query={models}
          skeleton={<TableSkeleton rows={6} />}
          context="Model inventory"
          isEmpty={(data) => data.items.length === 0}
          empty={
            <EmptyState
              title="No models discovered"
              description="The API scanned the configured roots and found no model directories. Add a model under the workspace models directory, point a scan at a local path, or enable the Hugging Face cache."
              icon="models"
              action={
                <div className="form-actions">
                  <Button
                    variant="primary"
                    icon="models"
                    loading={scan.pending}
                    onClick={() => void scanNow()}
                  >
                    Run scan
                  </Button>
                  <Button variant="ghost" onClick={models.reload} icon="refresh">
                    Reload inventory
                  </Button>
                </div>
              }
            />
          }
        >
          {() =>
            filtered.length === 0 ? (
              <EmptyState
                title="No models match this filter"
                description={`Nothing in the current inventory matches “${filter}”.`}
                icon="models"
                compact
                action={
                  <Button variant="secondary" size="sm" onClick={() => setFilter("")}>
                    Clear filter
                  </Button>
                }
              />
            ) : (
              <TableWrap label="Model inventory">
                <table className="data-table data-table--stack">
                  <caption className="sr-only">Discovered models</caption>
                  <thead>
                    <tr>
                      <th scope="col" className="col-model">Model</th>
                      <th scope="col">Architecture</th>
                      <th scope="col">Parameters</th>
                      <th scope="col">Precision</th>
                      <th scope="col">Size</th>
                      <th scope="col">Compatibility</th>
                      <th scope="col">Inspected</th>
                      <th scope="col">
                        <span className="sr-only">Actions</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.map((model) => (
                      <tr key={`${model.model_id}-${model.path ?? ""}`}>
                        <td className="cell-model" data-label="Model">
                          <button
                            type="button"
                            className="link-button"
                            onClick={() => setSelected(model)}
                          >
                            {model.model_id}
                          </button>
                          <div className="cell-sub" title={model.path ?? undefined}>
                            {model.source} · {model.path ?? "no local path"}
                          </div>                        </td>
                        <td data-label="Architecture">
                          <span>{model.architecture ?? "—"}</span>
                          <div className="cell-sub">{model.model_type ?? "unknown type"}</div>
                        </td>
                        <td className="mono" data-label="Parameters">
                          {formatCompact(model.parameter_count)}
                          {model.is_mixture_of_experts && model.active_parameter_count ? (
                            <div className="cell-sub">
                              {formatCompact(model.active_parameter_count)} active
                            </div>
                          ) : null}
                        </td>
                        <td data-label="Precision">
                          <span className="mono">{model.torch_dtype ?? "—"}</span>
                          <div className="cell-sub">
                            {model.quantization && model.quantization !== "none"
                              ? model.quantization
                              : "no quantization"}
                          </div>
                        </td>
                        <td className="mono" data-label="Size">{formatBytes(model.size_bytes)}</td>
                        <td data-label="Compatibility">
                          {(model.compatibility ?? []).length === 0 ? (
                            <span className="muted">—</span>
                          ) : (
                            <div className="badge-row">
                              {(model.compatibility ?? []).slice(0, 2).map((entry) => (
                                <Badge
                                  key={entry}
                                  title={entry}
                                  tone={
                                    /incompatible|unsupported|missing/i.test(entry)
                                      ? "warning"
                                      : "success"
                                  }
                                >
                                  {truncate(entry, 44)}
                                </Badge>
                              ))}
                              {(model.compatibility ?? []).length > 2 ? (
                                <Badge tone="neutral">
                                  +{(model.compatibility ?? []).length - 2}
                                </Badge>
                              ) : null}
                            </div>
                          )}
                        </td>
                        <td data-label="Inspected" title={formatDateTime(model.inspected_at)}>
                          {formatRelativeTime(model.inspected_at, now)}
                        </td>
                        <td className="cell-actions" data-label="Actions">
                          <Button
                            variant="ghost"
                            size="sm"
                            icon="external"
                            onClick={() => setSelected(model)}
                            aria-label={`Inspect ${model.model_id}`}
                          >
                            Inspect
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            )
          }
        </AsyncState>
      </Panel>

      {selected ? (
        <ModelDrawer model={selected} onClose={() => setSelected(null)} />
      ) : null}
    </>
  );
}

function ModelDrawer({ model, onClose }: { model: ModelInfo; onClose: () => void }) {
  const inspect = useApiQuery(
    (signal) => api.inspectModel(model.model_id, undefined, signal),
    [model.model_id],
  );
  const active = inspect.data ?? model;
  const formats = active.formats ?? [];
  const warnings = active.warnings ?? [];
  const compatibility = active.compatibility ?? [];
  const files = active.files ?? [];
  const tags = active.tags ?? [];

  return (
    <Drawer
      open
      onClose={onClose}
      width="lg"
      title={active.model_id}
      subtitle={
        <span className="mono small">
          {active.source} · {active.path ?? "no local path"}
        </span>
      }
    >
      {inspect.loading ? (
        <p className="small muted">Refreshing metadata from the model files…</p>
      ) : null}
      {inspect.error ? (
        <InlineAlert
          tone="warning"
          title="Live inspection failed"
          action={
            <Button variant="ghost" size="sm" icon="refresh" onClick={inspect.reload}>
              Retry
            </Button>
          }
        >
          Showing the inventory payload. {inspect.error.message}
        </InlineAlert>
      ) : null}

      <div className="badge-row">
        <Badge tone="accent">{active.source}</Badge>
        {formats.map((format) => (
          <Badge tone="neutral" mono key={format}>
            {format}
          </Badge>
        ))}
        {active.has_chat_template ? <Badge tone="success">chat template</Badge> : null}
        {active.is_multimodal ? <Badge tone="info">multimodal</Badge> : null}
        {active.is_mixture_of_experts ? <Badge tone="info">mixture of experts</Badge> : null}
        {active.requires_remote_code ? (
          <Badge tone="warning">requires remote code</Badge>
        ) : null}
      </div>

      {warnings.length > 0 ? (
        <InlineAlert tone="warning" title="Model warnings">
          <ul className="plain-list">
            {warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </InlineAlert>
      ) : null}

      <section className="drawer-section">
        <h3 className="subheading">Compatibility</h3>
        {compatibility.length === 0 ? (
          <p className="muted small">No compatibility assessment reported.</p>
        ) : (
          <div className="badge-row">
            {compatibility.map((entry) => (
              <Badge
                key={entry}
                tone={/incompatible|unsupported|missing/i.test(entry) ? "warning" : "success"}
              >
                {entry}
              </Badge>
            ))}
          </div>
        )}
      </section>

      <section className="drawer-section">
        <h3 className="subheading">Identity</h3>
        <KeyValueGrid
          data={{
            source: active.source,
            path: active.path,
            revision: active.revision,
            commit: active.commit ? shortHash(active.commit, 16) : null,
            license: active.license,
            metadata_source: active.metadata_source,
            inspected_at: formatDateTime(active.inspected_at),
            tags: tags.length > 0 ? tags.join(", ") : null,
          }}
        />
      </section>

      <section className="drawer-section">
        <h3 className="subheading">Architecture</h3>
        <KeyValueGrid
          data={{
            architecture: active.architecture,
            model_type: active.model_type,
            parameter_count: active.parameter_count,
            active_parameter_count: active.active_parameter_count,
            hidden_size: active.hidden_size,
            num_hidden_layers: active.num_hidden_layers,
            num_attention_heads: active.num_attention_heads,
            num_key_value_heads: active.num_key_value_heads,
            head_dim: active.head_dim,
            intermediate_size: active.intermediate_size,
            vocab_size: active.vocab_size,
            max_position_embeddings: active.max_position_embeddings,
            torch_dtype: active.torch_dtype,
            quantization: active.quantization,
            size_bytes: formatBytes(active.size_bytes),
          }}
        />
      </section>

      <section className="drawer-section">
        <h3 className="subheading">Files</h3>
        {files.length === 0 ? (
          <p className="muted small">No individual weight files reported by inspection.</p>
        ) : (
          <TableWrap label="Model files">
            <table className="data-table data-table--compact data-table--stack">
              <caption className="sr-only">Model files</caption>
              <thead>
                <tr>
                  <th scope="col">Path</th>
                  <th scope="col">Format</th>
                  <th scope="col">Size</th>
                  <th scope="col">SHA-256</th>
                </tr>
              </thead>
              <tbody>
                {files.map((file) => (
                  <tr key={file.path}>
                    <td className="mono cell-wrap" data-label="Path">{file.path}</td>
                    <td data-label="Format">
                      <Badge tone="neutral" mono>
                        {file.format}
                      </Badge>
                    </td>
                    <td className="mono" data-label="Size">{formatBytes(file.size_bytes)}</td>
                    <td data-label="SHA-256">
                      {file.sha256 ? (
                        <code title={file.sha256}>{shortHash(file.sha256, 16)}</code>
                      ) : (
                        <span className="muted">not hashed</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </section>

      <VerifySection model={active} />
      <EstimateSection key={active.model_id} model={active} />
    </Drawer>
  );
}

function VerifySection({ model }: { model: ModelInfo }) {
  const [manifestPath, setManifestPath] = useState("");
  const verify = useMutation((body: VerifyRequest, signal) =>
    api.verifyModel(body, signal),
  );

  const runVerify = (): void => {
    const body: VerifyRequest = { reference: model.model_id };
    const trimmed = manifestPath.trim();
    if (trimmed) body.manifest_path = trimmed;
    void verify.run(body);
  };

  return (
    <section className="drawer-section">
      <h3 className="subheading">Integrity verification</h3>
      <p className="small muted">
        Recomputes SHA-256 for model files. With no manifest, the server reports
        which files were checked and hashed.
      </p>
      <div className="inline-form">
        <TextField
          label="Manifest path (optional)"
          placeholder="workspace/manifests/model.sha256"
          value={manifestPath}
          onChange={(event) => setManifestPath(event.target.value)}
          hint="Must be inside the API workspace."
        />
        <Button
          variant="secondary"
          icon="security"
          loading={verify.pending}
          onClick={runVerify}
        >
          Verify files
        </Button>
      </div>

      {verify.error ? (
        <InlineAlert tone="danger" title={`Verification failed · ${verify.error.code}`}>
          {verify.error.message}
        </InlineAlert>
      ) : null}

      {verify.data ? <VerifyResult result={verify.data} /> : null}
    </section>
  );
}

function VerifyResult({ result }: { result: ModelValidation }) {
  return (
    <div className="verify-result">
      <div className="badge-row">
        <Badge tone={result.ok ? "success" : "danger"}>
          {result.ok ? "integrity ok" : "integrity failed"}
        </Badge>
        <Badge tone="neutral" mono>
          {formatNumber(result.files_checked)} checked
        </Badge>
        <Badge tone="neutral" mono>
          {formatNumber(result.hashes_verified)} hashed
        </Badge>
        <span className="small muted">
          {formatDateTime(result.checked_at)}
        </span>
      </div>

      {result.mismatches.length > 0 ? (
        <InlineAlert tone="danger" title="Hash mismatches">
          <ul className="plain-list">
            {result.mismatches.map((entry) => (
              <li key={entry} className="mono">
                {entry}
              </li>
            ))}
          </ul>
        </InlineAlert>
      ) : null}

      {result.missing.length > 0 ? (
        <InlineAlert tone="warning" title="Missing files">
          <ul className="plain-list">
            {result.missing.map((entry) => (
              <li key={entry} className="mono">
                {entry}
              </li>
            ))}
          </ul>
        </InlineAlert>
      ) : null}

      {Object.keys(result.details).length > 0 ? (
        <details className="details-block">
          <summary>Verification details</summary>
          <JsonView value={result.details} maxHeight={260} />
        </details>
      ) : null}
    </div>
  );
}

interface EstimateFieldErrors {
  batch?: string;
  sequence?: string;
}

function EstimateSection({ model }: { model: ModelInfo }) {
  const [dtype, setDtype] = useState("bfloat16");
  const [quantization, setQuantization] = useState("none");
  const [batchSize, setBatchSize] = useState("1");
  const [sequenceLength, setSequenceLength] = useState("2048");
  const [fieldErrors, setFieldErrors] = useState<EstimateFieldErrors>({});
  const estimate = useMutation((params: EstimateParams, signal) =>
    api.estimateModel(params, signal),
  );

  const runEstimate = (): void => {
    const errors: EstimateFieldErrors = {};
    const batch = Number(batchSize);
    const sequence = Number(sequenceLength);
    if (!Number.isInteger(batch) || batch < 1 || batch > 4096) {
      errors.batch = "Batch size must be an integer between 1 and 4096.";
    }
    if (!Number.isInteger(sequence) || sequence < 16 || sequence > 1_048_576) {
      errors.sequence = "Sequence length must be an integer between 16 and 1,048,576.";
    }
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;
    void estimate.run({
      reference: model.model_id,
      batch_size: batch,
      sequence_length: sequence,
      dtype,
      quantization,
    });
  };

  return (
    <section className="drawer-section">
      <h3 className="subheading">Memory estimate</h3>
      <p className="small muted">
        Analytical estimate from model metadata and the detected hardware budget.
        Values are estimates, not measurements.
      </p>
      <div className="estimate-controls">
        <SelectField
          label="dtype"
          value={dtype}
          onChange={(event) => setDtype(event.target.value)}
        >
          <option value="bfloat16">bfloat16</option>
          <option value="float16">float16</option>
          <option value="float32">float32</option>
          <option value="int8">int8</option>
        </SelectField>
        <SelectField
          label="Quantization"
          value={quantization}
          onChange={(event) => setQuantization(event.target.value)}
        >
          <option value="none">none</option>
          <option value="bnb_4bit">bnb_4bit</option>
          <option value="int8">int8</option>
        </SelectField>
        <TextField
          label="Batch size"
          type="number"
          min={1}
          max={4096}
          value={batchSize}
          onChange={(event) => setBatchSize(event.target.value)}
          error={fieldErrors.batch}
        />
        <TextField
          label="Sequence length"
          type="number"
          min={16}
          max={1_048_576}
          value={sequenceLength}
          onChange={(event) => setSequenceLength(event.target.value)}
          error={fieldErrors.sequence}
        />
      </div>
      <div className="form-actions">
        <Button
          variant="primary"
          icon="hardware"
          loading={estimate.pending}
          onClick={runEstimate}
        >
          Estimate memory
        </Button>
      </div>

      {estimate.error ? (
        <InlineAlert tone="danger" title={`Estimate failed · ${estimate.error.code}`}>
          <p>{estimate.error.message}</p>
          {estimate.error.hint ? <p className="small">{estimate.error.hint}</p> : null}
          {estimate.error.details !== undefined ? (
            <details className="details-block">
              <summary>Server details</summary>
              <JsonView value={estimate.error.details} maxHeight={200} />
            </details>
          ) : null}
        </InlineAlert>
      ) : null}

      {estimate.data ? <EstimateResult estimate={estimate.data.estimate} /> : null}
    </section>
  );
}

function EstimateResult({ estimate }: { estimate: ResourceEstimate }) {
  const breakdown = estimate.breakdown;
  const total =
    breakdown.weights_bytes +
    breakdown.kv_cache_bytes +
    breakdown.activations_bytes +
    breakdown.overhead_bytes;
  const denominator = total > 0 ? total : 1;
  const parts = [
    { key: "weights", label: "Weights", value: breakdown.weights_bytes },
    { key: "kv_cache", label: "KV cache", value: breakdown.kv_cache_bytes },
    { key: "activations", label: "Activations", value: breakdown.activations_bytes },
    { key: "overhead", label: "Runtime overhead", value: breakdown.overhead_bytes },
  ];
  const fitTone =
    estimate.fits === true ? "success" : estimate.fits === false ? "danger" : "warning";
  const fitLabel =
    estimate.fits === true
      ? "Fits detected memory budget"
      : estimate.fits === false
        ? "Exceeds detected memory budget"
        : "Memory budget could not be determined";

  return (
    <div className="estimate-result">
      <div className="badge-row">
        <Badge tone={fitTone}>{fitLabel}</Badge>
        <Badge tone="neutral" mono>
          {formatBytes(total)} total
        </Badge>
        <Badge tone="neutral" mono>
          {estimate.confidence}
        </Badge>
        <span className="small muted">
          {estimate.dtype} · {estimate.quantization} · batch {estimate.batch_size} ·
          seq {formatNumber(estimate.sequence_length)}
        </span>
      </div>

      {estimate.headroom_bytes !== null ? (
        <p className={`small ${estimate.headroom_bytes < 0 ? "danger-text" : "muted"}`}>
          Headroom:{" "}
          <span className="mono">
            {estimate.headroom_bytes < 0 ? "−" : ""}
            {formatBytes(Math.abs(estimate.headroom_bytes))}
          </span>
        </p>
      ) : null}

      <div className="breakdown-bars">
        {parts.map((part) => (
          <div className="bar-row" key={part.key}>
            <div className="bar-meta">
              <span>{part.label}</span>
              <span className="mono">{formatBytes(part.value)}</span>
            </div>
            <div className="bar-track" aria-hidden="true">
              <span
                className={`bar-fill bar-fill--${part.key}`}
                style={{ width: `${Math.min(100, (part.value / denominator) * 100)}%` }}
              />
            </div>
            <span className="bar-percent mono">
              {((part.value / denominator) * 100).toFixed(1)}%
            </span>
          </div>
        ))}
      </div>

      <KeyValueGrid
        data={{
          recommended_dtype: estimate.recommended_dtype,
          recommended_quantization: estimate.recommended_quantization,
          recommended_batch_size: estimate.recommended_batch_size,
          recommended_device_map: estimate.recommended_device_map,
          hardware_profile_id: estimate.hardware_profile_id,
          created_at: formatDateTime(estimate.created_at),
        }}
      />

      {estimate.alternatives.length > 0 ? (
        <InlineAlert tone="info" title="Actionable alternatives">
          <ul className="plain-list">
            {estimate.alternatives.map((alternative) => (
              <li key={alternative}>{alternative}</li>
            ))}
          </ul>
        </InlineAlert>
      ) : null}

      {estimate.assumptions.length > 0 ? (
        <details className="details-block">
          <summary>Assumptions ({estimate.assumptions.length})</summary>
          <ul className="plain-list small">
            {estimate.assumptions.map((assumption) => (
              <li key={assumption}>{assumption}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
