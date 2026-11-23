import { Component, type ErrorInfo, type ReactNode } from "react";

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

export class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Dashboard render error:", error, info.componentStack);
  }

  private handleReset = (): void => {
    this.setState({ error: null });
  };

  private handleReload = (): void => {
    window.location.reload();
  };

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="fatal-error" role="alert">
        <div className="fatal-error-card">
          <p className="eyebrow">Dashboard error</p>
          <h1>This view crashed</h1>
          <p>
            The React tree threw an unhandled error. No data was fabricated — reload
            or reset the view to continue.
          </p>
          <pre className="code-block">{error.message}</pre>
          <div className="form-actions">
            <button type="button" className="btn btn--primary btn--md" onClick={this.handleReset}>
              Reset view
            </button>
            <button type="button" className="btn btn--secondary btn--md" onClick={this.handleReload}>
              Reload dashboard
            </button>
          </div>
        </div>
      </div>
    );
  }
}
