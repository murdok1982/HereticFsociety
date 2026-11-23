import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/inter/index.css";
import "@fontsource-variable/jetbrains-mono/index.css";
import "./styles/tokens.css";
import "./styles/app.css";
import { App } from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";

const container = document.getElementById("root");
if (!container) {
  throw new Error("Root element #root is missing from index.html.");
}

createRoot(container).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
);
