import type { ReactNode } from "react";
import type { ApiQueryResult } from "../../hooks/useApiQuery";
import { EmptyState } from "./EmptyState";
import { ErrorState } from "./ErrorState";
import { InlineAlert } from "./InlineAlert";
import { Button } from "./Button";
import { PanelSkeleton } from "./Skeleton";

interface AsyncStateProps<T> {
  query: ApiQueryResult<T>;
  children: (data: T) => ReactNode;
  skeleton?: ReactNode;
  empty?: ReactNode;
  isEmpty?: (data: T) => boolean;
  context?: string;
}

/**
 * Single source of truth for loading / error / empty / data rendering so no
 * view can accidentally display placeholder values.
 */
export function AsyncState<T>({
  query,
  children,
  skeleton,
  empty,
  isEmpty,
  context,
}: AsyncStateProps<T>) {
  if (query.data === null) {
    if (query.error) {
      return (
        <ErrorState
          error={query.error}
          onRetry={query.reload}
          context={context}
        />
      );
    }
    if (query.loading) {
      return <>{skeleton ?? <PanelSkeleton />}</>;
    }
    return (
      <EmptyState
        title="No data returned"
        description="The API responded without a payload. Retry the request."
        icon="alert"
        action={
          <Button variant="secondary" size="sm" icon="refresh" onClick={query.reload}>
            Retry
          </Button>
        }
      />
    );
  }

  const data = query.data;
  const emptyContent =
    empty ??
    (
      <EmptyState
        title="Nothing to show yet"
        description="The API returned an empty collection."
        icon="models"
      />
    );

  return (
    <>
      {query.error ? (
        <InlineAlert
          tone="warning"
          title="Showing previous data"
          action={
            <Button variant="ghost" size="sm" icon="refresh" onClick={query.reload}>
              Retry
            </Button>
          }
        >
          Refresh failed: {query.error.message}
        </InlineAlert>
      ) : null}
      {isEmpty && isEmpty(data) ? emptyContent : children(data)}
    </>
  );
}
