import { Link } from "react-router-dom";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { EmptyState } from "../components/ui/EmptyState";

export function NotFoundPage() {
  useDocumentTitle("Not found");
  return (
    <EmptyState
      title="404 — Section not found"
      description="The dashboard route does not exist. Use the navigation to reach one of the ten operator sections."
      icon="alert"
      action={
        <div className="form-actions">
          <Link className="btn btn--primary btn--md" to="/">
            <span>Back to overview</span>
          </Link>
          <Link className="btn btn--secondary btn--md" to="/models">
            <span>Open model manager</span>
          </Link>
        </div>
      }
    />
  );
}
