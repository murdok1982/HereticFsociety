import { useEffect, useState } from "react";

/**
 * A coarse clock used for relative timestamps ("12s ago"). Updates pause while
 * the tab is hidden to avoid needless renders.
 */
export function useNow(intervalMs = 15_000): number {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") {
        setNow(Date.now());
      }
    }, intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);

  return now;
}
