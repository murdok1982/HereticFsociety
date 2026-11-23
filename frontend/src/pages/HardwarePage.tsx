import { useState } from "react";
import { api } from "../api/client";
import type { GpuInfo, HardwareProfile } from "../api/types";
import { useApiQuery } from "../hooks/useApiQuery";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useNow } from "../hooks/useNow";
import {
  formatDateTime,
  formatGiB,
  formatNumber,
  formatRelativeTime,
  pluralize,
} from "../lib/format";
import type { Tone } from "../lib/status";
import { AsyncState } from "../components/ui/AsyncState";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { EmptyState } from "../components/ui/EmptyState";
import { InlineAlert } from "../components/ui/InlineAlert";
import { JsonView, KeyValueGrid } from "../components/ui/JsonView";
import { PageHeader, Panel } from "../components/ui/Panel";
import { StatsSkeleton } from "../components/ui/Skeleton";
import { StatCard } from "../components/ui/StatCard";

function provenanceTone(source: string): Tone {
  if (source === "measured") return "success";
  if (source === "reported") return "info";
  if (source === "estimated") return "warning";
  return "neutral";
}

function memoryUsage(total: number | null, available: number | null): number | null {
  if (total === null || available === null || total <= 0) return null;
  return Math.min(100, Math.max(0, ((total - available) / total) * 100));
}

function MemoryBar({ total, free }: { total: number | null; free: number | null }) {
  const used = memoryUsage(total, free);
  return (
    <div className="memory-bar">
      <div className="progress-meta">
        <span>{free === null ? "Availability unknown" : `${formatGiB(free)} free`}</span>
        <span className="mono">{used === null ? "—" : `${used.toFixed(1)}% used`}</span>
      </div>
      <div className="bar-track" aria-hidden="true">
        <span
          className={`bar-fill ${used !== null && used > 90 ? "bar-fill--overhead" : "bar-fill--weights"}`}
          style={{ width: used === null ? "0%" : `${used}%` }}
        />
      </div>
    </div>
  );
}

function GpuCard({ gpu }: { gpu: GpuInfo }) {
  return (
    <article className="gpu-card">
      <header className="gpu-card-head">
        <div>
          <p className="gpu-name">{gpu.name}</p>
          <p className="mono small muted">device {gpu.index} · {gpu.backend}</p>
        </div>
        <div className="badge-row">
          <Badge tone={provenanceTone(gpu.source)}>{gpu.source}</Badge>
          <Badge tone={gpu.confidence === "measured" ? "success" : "warning"}>
            {gpu.confidence}
          </Badge>
        </div>
      </header>
      <MemoryBar total={gpu.total_memory_bytes} free={gpu.free_memory_bytes} />
      <dl className="gpu-meta">
        <div>
          <dt>Total</dt>
          <dd className="mono">{formatGiB(gpu.total_memory_bytes)}</dd>
        </div>
        <div>
          <dt>Free</dt>
          <dd className="mono">{formatGiB(gpu.free_memory_bytes)}</dd>
        </div>
        <div>
          <dt>Compute</dt>
          <dd className="mono">{gpu.compute_capability ?? "—"}</dd>
        </div>
        <div>
          <dt>Driver</dt>
          <dd className="mono">{gpu.driver_version ?? "—"}</dd>
        </div>
      </dl>
    </article>
  );
}

function HardwareContent({ profile }: { profile: HardwareProfile }) {
  const now = useNow(15_000);
  const acceleratorMemory = profile.gpus.reduce(
    (sum, gpu) => sum + (gpu.total_memory_bytes ?? 0),
    0,
  );

  return (
    <>
      <div className="grid grid-4 stat-grid">
        <StatCard
          label="CPU"
          value={profile.cpu.model ?? "Unknown CPU"}
          hint={`${formatNumber(profile.cpu.physical_cores)} physical · ${formatNumber(
            profile.cpu.logical_cores,
          )} logical`}
          icon="hardware"
          mono={false}
        />
        <StatCard
          label="System memory"
          value={formatGiB(profile.ram_total_bytes)}
          hint={`${formatGiB(profile.ram_available_bytes)} available`}
          icon="overview"
          mono
        />
        <StatCard
          label="Accelerators"
          value={profile.gpus.length === 0 ? "None" : String(profile.gpus.length)}
          hint={
            profile.gpus.length === 0
              ? "CPU-only profile"
              : `${formatGiB(acceleratorMemory)} aggregate VRAM`
          }
          icon="hardware"
          tone={profile.gpus.length > 0 ? "success" : "default"}
        />
        <StatCard
          label="Detection"
          value={profile.detection_mode}
          hint={
            <>
              captured{" "}
              <span title={formatDateTime(profile.captured_at)}>
                {formatRelativeTime(profile.captured_at, now)}
              </span>
            </>
          }
          icon="clock"
          mono
          tone={profile.detection_mode === "measured" ? "success" : "warning"}
        />
      </div>

      <div className="grid grid-2">
        <Panel
          title="CPU"
          subtitle="Provenance reported by the backend"
          actions={<Badge tone={provenanceTone(profile.cpu.source)}>{profile.cpu.source}</Badge>}
        >
          <KeyValueGrid
            data={{
              model: profile.cpu.model,
              physical_cores: profile.cpu.physical_cores,
              logical_cores: profile.cpu.logical_cores,
              frequency_mhz:
                profile.cpu.frequency_mhz !== null
                  ? `${formatNumber(profile.cpu.frequency_mhz)} MHz`
                  : null,
              architecture: profile.cpu.architecture,
            }}
          />
        </Panel>

        <Panel
          title="Memory"
          subtitle="Live-available RAM reported at detection time"
          actions={
            <span className="mono small muted">
              {formatGiB(profile.ram_total_bytes)} total
            </span>
          }
        >
          <MemoryBar
            total={profile.ram_total_bytes}
            free={profile.ram_available_bytes}
          />
          <KeyValueGrid
            data={{
              total: formatGiB(profile.ram_total_bytes),
              available: formatGiB(profile.ram_available_bytes),
              swap: formatGiB(profile.swap_total_bytes),
            }}
          />
        </Panel>
      </div>

      <Panel
        title="Accelerators"
        subtitle="Per-device memory and driver provenance"
        actions={<Badge tone="neutral">{pluralize(profile.gpus.length, "device")}</Badge>}
      >
        {profile.gpus.length === 0 ? (
          <EmptyState
            title="No accelerators detected"
            description="The API reported no CUDA, ROCm or Apple MPS devices. Resource estimates will only consider system RAM; install a supported runtime and refresh detection to populate this view."
            icon="hardware"
            compact
          />
        ) : (
          <div className="gpu-grid">
            {profile.gpus.map((gpu) => (
              <GpuCard key={`${gpu.index}-${gpu.name}`} gpu={gpu} />
            ))}
          </div>
        )}
      </Panel>

      <div className="grid grid-2">
        <Panel title="Runtime backends" subtitle="Reported availability probes">
          {Object.keys(profile.backends).length === 0 ? (
            <p className="muted small">No backend probes reported.</p>
          ) : (
            <ul className="backend-list">
              {Object.entries(profile.backends).map(([name, available]) => (
                <li key={name}>
                  <span className="backend-name mono">{name}</span>
                  <Badge tone={available ? "success" : "neutral"}>
                    {available ? "available" : "unavailable"}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel
          title="PyTorch"
          subtitle="Optional runtime used by model backends"
          actions={
            <Badge tone={profile.torch.installed ? "success" : "warning"}>
              {profile.torch.installed ? "installed" : "not installed"}
            </Badge>
          }
        >
          <KeyValueGrid
            data={{
              version: profile.torch.version,
              cuda_available: profile.torch.cuda_available,
              cuda_version: profile.torch.cuda_version,
              mps_available: profile.torch.mps_available,
              device_count: profile.torch.device_count,
            }}
          />
          <div className="checks-block">
            <h3 className="subheading">Supported dtypes</h3>
            {profile.torch.dtype_support.length === 0 ? (
              <p className="muted small">No dtype report available.</p>
            ) : (
              <div className="badge-row">
                {profile.torch.dtype_support.map((dtype) => (
                  <Badge tone="neutral" mono key={dtype}>
                    {dtype}
                  </Badge>
                ))}
              </div>
            )}
          </div>
          {profile.torch.error ? (
            <InlineAlert tone="warning" title="Torch probe reported an error">
              {profile.torch.error}
            </InlineAlert>
          ) : null}
        </Panel>
      </div>

      <Panel title="Detection provenance" subtitle="Identity of this hardware snapshot">
        <KeyValueGrid
          columns={3}
          data={{
            profile_id: profile.profile_id,
            hostname: profile.hostname,
            os: `${profile.os} ${profile.os_release}`,
            architecture: profile.architecture,
            python_version: profile.python_version,
            detection_mode: profile.detection_mode,
            fingerprint: profile.fingerprint,
            captured_at: formatDateTime(profile.captured_at),
          }}
        />
        <details className="details-block">
          <summary>Raw hardware profile</summary>
          <JsonView value={profile} maxHeight={360} />
        </details>
      </Panel>

      {profile.warnings.length > 0 ? (
        <InlineAlert tone="warning" title={pluralize(profile.warnings.length, "detection warning")}>
          <ul className="plain-list">
            {profile.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </InlineAlert>
      ) : null}
    </>
  );
}

export function HardwarePage() {
  useDocumentTitle("Hardware Monitor");
  const [refreshRequested, setRefreshRequested] = useState(false);
  const [refreshToken, setRefreshToken] = useState(0);

  const hardware = useApiQuery(
    (signal) => api.getHardware(refreshRequested, signal),
    [refreshToken],
    { pollMs: 60_000 },
  );

  const refresh = (): void => {
    setRefreshRequested(true);
    setRefreshToken((value) => value + 1);
  };

  return (
    <>
      <PageHeader
        eyebrow="Environment"
        title="Hardware Monitor"
        description="Measured and inferred hardware as detected by the API. Profile values include per-field provenance; nothing is inferred from the operating system alone."
        actions={
          <Button
            icon="refresh"
            onClick={refresh}
            loading={hardware.refreshing}
            title="Re-run detection with /hardware?refresh=true"
          >
            Refresh profile
          </Button>
        }
      />

      <AsyncState
        query={hardware}
        skeleton={<StatsSkeleton />}
        context="Hardware profile"
      >
        {(profile) => <HardwareContent profile={profile} />}
      </AsyncState>
    </>
  );
}
