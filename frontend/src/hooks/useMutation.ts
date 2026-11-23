import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";

export interface MutationResult<TVariables, TData> {
  pending: boolean;
  error: ApiError | null;
  data: TData | null;
  run: (variables: TVariables) => Promise<TData | undefined>;
  reset: () => void;
}

interface MutationState<TData> {
  pending: boolean;
  error: ApiError | null;
  data: TData | null;
}

/** Imperative mutation hook with abort-on-unmount and typed errors. */
export function useMutation<TVariables, TData>(
  fn: (variables: TVariables, signal: AbortSignal) => Promise<TData>,
): MutationResult<TVariables, TData> {
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const controllerRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);
  const [state, setState] = useState<MutationState<TData>>({
    pending: false,
    error: null,
    data: null,
  });

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      controllerRef.current?.abort();
    };
  }, []);

  const run = useCallback(async (variables: TVariables): Promise<TData | undefined> => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setState({ pending: true, error: null, data: null });

    try {
      const data = await fnRef.current(variables, controller.signal);
      if (!mountedRef.current || controllerRef.current !== controller) {
        return undefined;
      }
      setState({ pending: false, error: null, data });
      return data;
    } catch (error) {
      if (!mountedRef.current || controllerRef.current !== controller) {
        return undefined;
      }
      const apiError =
        error instanceof ApiError
          ? error
          : new ApiError({
              code: "unknown",
              status: 0,
              message: error instanceof Error ? error.message : String(error),
            });
      if (apiError.code === "aborted") {
        setState((previous) => ({ ...previous, pending: false }));
        return undefined;
      }
      setState({ pending: false, error: apiError, data: null });
      return undefined;
    }
  }, []);

  const reset = useCallback(() => {
    controllerRef.current?.abort();
    setState({ pending: false, error: null, data: null });
  }, []);

  return { ...state, run, reset };
}
