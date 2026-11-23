import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { JobRecord } from "../api/types";
import { isTerminalJobStatus } from "../api/types";
import { useApiQuery, type ApiQueryResult } from "./useApiQuery";

const JOB_POLL_MS = 2_000;

/**
 * Polls a job while it is active. Polling stops at a terminal status and
 * restarts cleanly when a different job id is supplied.
 */
export function useJobPoll(jobId: string | null): ApiQueryResult<JobRecord> {
  const [terminal, setTerminal] = useState(false);

  useEffect(() => {
    setTerminal(false);
  }, [jobId]);

  const query = useApiQuery(
    (signal) => api.getJob(jobId as string, signal),
    [jobId],
    {
      enabled: Boolean(jobId),
      pollMs: jobId && !terminal ? JOB_POLL_MS : 0,
    },
  );

  useEffect(() => {
    if (query.data && isTerminalJobStatus(query.data.status) && !terminal) {
      setTerminal(true);
    }
  }, [query.data, terminal]);

  return {
    ...query,
    data:
      query.data && jobId && query.data.job_id === jobId ? query.data : null,
  };
}
