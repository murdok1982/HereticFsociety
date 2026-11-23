/**
 * API key storage.
 *
 * The key lives in sessionStorage only: it disappears when the tab closes and
 * is never persisted to disk by the dashboard. It is injected by the API
 * client at request time so rotating it takes effect immediately.
 */

const STORAGE_KEY = "fsociety.api-key";
const CHANGE_EVENT = "fsociety:api-key-change";

export function getApiKey(): string {
  try {
    return window.sessionStorage.getItem(STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

export function saveApiKey(value: string): void {
  const trimmed = value.trim();
  try {
    if (trimmed) {
      window.sessionStorage.setItem(STORAGE_KEY, trimmed);
    } else {
      window.sessionStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    // Storage can be unavailable (private mode, hardened browser). The request
    // will simply be unauthenticated; the UI surfaces the resulting 401.
  }
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

export function clearApiKey(): void {
  saveApiKey("");
}

export function subscribeApiKey(listener: () => void): () => void {
  const handler = () => listener();
  window.addEventListener(CHANGE_EVENT, handler);
  window.addEventListener("storage", handler);
  return () => {
    window.removeEventListener(CHANGE_EVENT, handler);
    window.removeEventListener("storage", handler);
  };
}
