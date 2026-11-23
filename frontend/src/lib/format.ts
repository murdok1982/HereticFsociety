/** Display formatting for real API values. Never invents fallback data. */

const DASH = "—";
const BYTE_UNITS = ["B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB"];

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function asNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

export function formatBytes(
  value: number | null | undefined,
  decimals = 1,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return DASH;
  }
  let size = Math.max(0, value);
  let unit = 0;
  while (size >= 1024 && unit < BYTE_UNITS.length - 1) {
    size /= 1024;
    unit += 1;
  }
  const digits = unit === 0 ? 0 : decimals;
  return `${size.toFixed(digits)} ${BYTE_UNITS[unit]}`;
}

export function formatGiB(
  value: number | null | undefined,
  decimals = 1,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return DASH;
  }
  return `${(value / 1024 ** 3).toFixed(decimals)} GiB`;
}

const numberFormatter = new Intl.NumberFormat("en-US");
const compactFormatter = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 2,
});

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return DASH;
  }
  return numberFormatter.format(value);
}

/** Counted noun without the "(s)" shorthand, e.g. `2 experiments`. */
export function pluralize(
  count: number,
  singular: string,
  plural = `${singular}s`,
): string {
  return `${formatNumber(count)} ${count === 1 ? singular : plural}`;
}

export function formatCompact(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return DASH;
  }
  return compactFormatter.format(value);
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) {
    return DASH;
  }
  const total = Math.max(0, seconds);
  if (total < 1) return `${Math.round(total * 1000)} ms`;
  if (total < 60) return `${total.toFixed(total < 10 ? 2 : 1)} s`;
  const whole = Math.floor(total);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const secs = whole % 60;
  if (hours > 0) return `${hours}h ${minutes}m ${secs}s`;
  return `${minutes}m ${secs}s`;
}

export function parseDate(value: string | number | null | undefined): Date | null {
  if (value === null || value === undefined) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDateTime(
  value: string | number | null | undefined,
): string {
  const date = parseDate(value);
  if (!date) return DASH;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(
    date.getDate(),
  )} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(
    date.getSeconds(),
  )}`;
}

export function formatRelativeTime(
  value: string | number | null | undefined,
  now: number = Date.now(),
): string {
  const date = parseDate(value);
  if (!date) return DASH;
  const deltaSeconds = Math.round((now - date.getTime()) / 1000);
  const future = deltaSeconds < 0;
  const seconds = Math.abs(deltaSeconds);
  if (seconds < 10) return "just now";
  let phrase: string;
  if (seconds < 60) phrase = `${seconds}s`;
  else if (seconds < 3600) phrase = `${Math.floor(seconds / 60)}m`;
  else if (seconds < 86_400) phrase = `${Math.floor(seconds / 3600)}h`;
  else if (seconds < 604_800) phrase = `${Math.floor(seconds / 86_400)}d`;
  else return formatDateTime(date.getTime());
  return future ? `in ${phrase}` : `${phrase} ago`;
}

/** Progress may be expressed as a 0–1 fraction or a 0–100 percentage. */
export function progressPercent(progress: number | null | undefined): number | null {
  if (progress === null || progress === undefined || !Number.isFinite(progress)) {
    return null;
  }
  const value = progress <= 1 ? progress * 100 : progress;
  return Math.min(100, Math.max(0, value));
}

export function formatProgress(progress: number | null | undefined): string {
  const value = progressPercent(progress);
  if (value === null) return DASH;
  return `${value.toFixed(value < 10 ? 1 : 0)}%`;
}

const ACRONYMS = new Set([
  "api",
  "cpu",
  "gpu",
  "ram",
  "os",
  "id",
  "url",
  "uri",
  "sha256",
  "sha",
  "gib",
  "mib",
  "kib",
  "hf",
  "cuda",
  "mps",
  "json",
  "yaml",
  "toml",
  "cli",
  "ui",
  "ux",
  "llm",
  "gguf",
  "dtype",
  "kv",
  "cors",
  "sbom",
  "tpe",
]);

export function humanizeKey(key: string): string {
  const words = key
    .replace(/[_-]+/g, " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (words.length === 0) return key;
  return words
    .map((word) => {
      const lower = word.toLowerCase();
      if (ACRONYMS.has(lower)) return lower.toUpperCase();
      return lower.charAt(0).toUpperCase() + lower.slice(1);
    })
    .join(" ");
}

export function humanizeToken(value: string | null | undefined): string {
  if (!value) return DASH;
  return value.replace(/[_-]+/g, " ");
}

export function formatValue(value: unknown): string {
  if (value === null || value === undefined) return DASH;
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") {
    return Number.isInteger(value) ? formatNumber(value) : String(value);
  }
  if (typeof value === "string") return value === "" ? DASH : value;
  if (Array.isArray(value)) {
    if (value.length === 0) return DASH;
    return value.map((entry) => formatValue(entry)).join(", ");
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

export function truncate(value: string, max = 48): string {
  if (value.length <= max) return value;
  return `${value.slice(0, Math.max(0, max - 1))}…`;
}

export function shortHash(value: string | null | undefined, length = 12): string {
  if (!value) return DASH;
  return value.length <= length ? value : value.slice(0, length);
}

export function formatDelta(delta: number | null | undefined): string {
  if (delta === null || delta === undefined || !Number.isFinite(delta)) {
    return DASH;
  }
  const sign = delta > 0 ? "+" : "";
  return `${sign}${delta.toFixed(Math.abs(delta) < 1 ? 4 : 2)}`;
}

export function formatRelativeDelta(
  delta: number | null | undefined,
): string {
  if (delta === null || delta === undefined || !Number.isFinite(delta)) {
    return DASH;
  }
  const sign = delta > 0 ? "+" : "";
  return `${sign}${(delta * 100).toFixed(2)}%`;
}

export { DASH };
