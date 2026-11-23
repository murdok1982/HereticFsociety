import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type DependencyList,
} from "react";
import { ApiError } from "../api/client";

export interface ApiQueryResult<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  refreshing: boolean;
  lastUpdated: number | null;
  reload: () => void;
}

interface QueryState<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  refreshing: boolean;
  lastUpdated: number | null;
}

interface QueryOptions {
  enabled?: boolean;
  /** Poll interval in ms. Polling pauses while the tab is hidden. */
  pollMs?: number;
  /**
   * Keep the previous payload while a dependency-triggered refetch is running.
   * Disable for entity routes so an old record never appears under a new id.
   */
  keepPreviousData?: boolean;
}

/**
 * Fetch-on-mount + polling hook with cancellation.
 *
 * - Aborts in-flight requests on unmount and dependency changes.
 * - Never calls setState after unmount (checked via a disposed flag).
 * - Optional polling pauses while document.visibilityState === "hidden" and
 *   refreshes immediately when the tab becomes visible again.
 */
export function useApiQuery<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  deps: DependencyList = [],
  options: QueryOptions = {},
): ApiQueryResult<T> {
  const { enabled = true, pollMs = 0, keepPreviousData = true } = options;
  const [state, setState] = useState<QueryState<T>>(() => ({
    data: null,
    error: null,
    loading: enabled,
    refreshing: false,
    lastUpdated: null,
  }));
  const [nonce, setNonce] = useState(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const reload = useCallback(() => {
    setNonce((value) => value + 1);
  }, []);

  useEffect(() => {
    if (!enabled) return;

    let disposed = false;
    let controller: AbortController | null = null;
    let pollTimer: number | undefined;

    const load = async (): Promise<void> => {
      controller?.abort();
      controller = new AbortController();
      const active = controller;

      if (!disposed) {
        if (keepPreviousData) {
          setState((previous) => ({
            ...previous,
            loading: previous.data === null,
            refreshing: previous.data !== null,
            error: null,
          }));
        } else {
          setState({
            data: null,
            error: null,
            loading: true,
            refreshing: false,
            lastUpdated: null,
          });
        }
      }

      try {
        const data = await fetcherRef.current(active.signal);
        if (disposed) return;
        setState({
          data,
          error: null,
          loading: false,
          refreshing: false,
          lastUpdated: Date.now(),
        });
      } catch (error) {
        if (disposed) return;
        const apiError =
          error instanceof ApiError
            ? error
            : new ApiError({
                code: "unknown",
                status: 0,
                message:
                  error instanceof Error ? error.message : String(error),
              });
        if (apiError.code === "aborted") return;
        setState((previous) => ({
          ...previous,
          error: apiError,
          loading: false,
          refreshing: false,
        }));
      }
    };

    const schedule = (): void => {
      if (!pollMs || disposed) return;
      pollTimer = window.setTimeout(() => {
        if (disposed) return;
        const tick = async (): Promise<void> => {
          if (document.visibilityState === "visible") {
            await load();
          }
          schedule();
        };
        void tick();
      }, pollMs);
    };

    const onVisibilityChange = (): void => {
      if (document.visibilityState === "visible" && !disposed) {
        void load();
      }
    };

    void load();
    schedule();
    document.addEventListener("visibilitychange", onVisibilityChange);

    return () => {
      disposed = true;
      controller?.abort();
      if (pollTimer !== undefined) window.clearTimeout(pollTimer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
    // The caller owns the dependency list; spread it to retrigger the query.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nonce, enabled, pollMs, keepPreviousData, ...deps]);

  // A dependency change can render once before the effect starts; treat that
  // frame as loading instead of flashing an empty state.
  const pendingFirstLoad =
    enabled && state.data === null && state.error === null;

  return { ...state, loading: state.loading || pendingFirstLoad, reload };
}
