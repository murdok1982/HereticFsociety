import { useEffect } from "react";

const BASE_TITLE = "FSOCIETY AI — Operator Console";

export function useDocumentTitle(title: string): void {
  useEffect(() => {
    document.title = title ? `${title} — FSOCIETY AI` : BASE_TITLE;
    return () => {
      document.title = BASE_TITLE;
    };
  }, [title]);
}
