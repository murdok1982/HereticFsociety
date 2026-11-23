import { humanizeToken } from "./format";

export type Tone =
  | "neutral"
  | "success"
  | "warning"
  | "danger"
  | "info"
  | "accent";

const TERMINAL_SUCCESS = new Set([
  "succeeded",
  "success",
  "completed",
  "complete",
  "ok",
  "valid",
  "ready",
  "available",
  "measured",
  "verified",
  "accepted",
  "resolved",
  "improved",
  "equivalent",
]);

const RUNNING = new Set(["running", "in_progress", "processing", "active"]);
const QUEUED = new Set(["queued", "pending", "waiting", "scheduled"]);
const FAILED = new Set(["failed", "failure", "error", "invalid", "unavailable", "blocked"]);
const CANCELLED = new Set(["cancelled", "canceled", "stopped", "aborted", "skipped"]);
const WARNING = new Set([
  "degraded",
  "warning",
  "warn",
  "partial",
  "queued",
  "pending",
  "mixed",
  "unknown",
  "estimated",
  "reported",
  "declared",
]);

export function normalizeStatus(status: string | null | undefined): string {
  return (status ?? "").trim().toLowerCase();
}

export function toneForStatus(status: string | null | undefined): Tone {
  const value = normalizeStatus(status);
  if (!value) return "neutral";
  if (TERMINAL_SUCCESS.has(value)) return "success";
  if (RUNNING.has(value)) return "info";
  if (QUEUED.has(value)) return "warning";
  if (FAILED.has(value)) return "danger";
  if (CANCELLED.has(value)) return "neutral";
  if (value === "degraded") return "warning";
  if (WARNING.has(value)) return "warning";
  return "neutral";
}

export function toneForSeverity(severity: string | null | undefined): Tone {
  const value = normalizeStatus(severity);
  if (value === "critical" || value === "high" || value === "error") return "danger";
  if (value === "medium" || value === "warning" || value === "warn") return "warning";
  if (value === "low" || value === "info") return "info";
  if (value === "ok" || value === "resolved") return "success";
  return "neutral";
}

export function toneForLevel(level: string | null | undefined): Tone {
  const value = normalizeStatus(level);
  if (value === "audit") return "accent";
  if (value === "error") return "danger";
  if (value === "warning") return "warning";
  if (value === "debug") return "neutral";
  return "info";
}

export function toneForHealth(status: string | null | undefined): Tone {
  const value = normalizeStatus(status);
  if (value === "ok") return "success";
  if (value === "degraded") return "warning";
  return "danger";
}

export function statusLabel(status: string | null | undefined): string {
  if (!status) return "Unknown";
  return humanizeToken(status).replace(/\b\w/g, (char) => char.toUpperCase());
}
