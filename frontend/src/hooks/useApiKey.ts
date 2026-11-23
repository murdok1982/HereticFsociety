import { useCallback, useEffect, useState } from "react";
import {
  clearApiKey,
  getApiKey,
  saveApiKey,
  subscribeApiKey,
} from "../lib/apiKey";

export interface ApiKeyState {
  apiKey: string;
  configured: boolean;
  save: (value: string) => void;
  clear: () => void;
}

/** Tracks the session-only API key and keeps subscribers in sync. */
export function useApiKey(): ApiKeyState {
  const [apiKey, setApiKeyState] = useState<string>(() => getApiKey());

  useEffect(() => {
    const unsubscribe = subscribeApiKey(() => setApiKeyState(getApiKey()));
    return unsubscribe;
  }, []);

  const save = useCallback((value: string) => {
    saveApiKey(value);
    setApiKeyState(getApiKey());
  }, []);

  const clear = useCallback(() => {
    clearApiKey();
    setApiKeyState("");
  }, []);

  return { apiKey, configured: apiKey.length > 0, save, clear };
}
