import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/layout/AppShell";
import { OverviewPage } from "./pages/OverviewPage";
import { ModelsPage } from "./pages/ModelsPage";
import { HardwarePage } from "./pages/HardwarePage";
import { OptimizerPage } from "./pages/OptimizerPage";
import { EvaluationPage } from "./pages/EvaluationPage";
import { ExperimentsPage } from "./pages/ExperimentsPage";
import { ExperimentDetailPage } from "./pages/ExperimentDetailPage";
import { PluginsPage } from "./pages/PluginsPage";
import { ExportsPage } from "./pages/ExportsPage";
import { SecurityPage } from "./pages/SecurityPage";
import { SettingsPage } from "./pages/SettingsPage";
import { NotFoundPage } from "./pages/NotFoundPage";

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<OverviewPage />} />
          <Route path="models" element={<ModelsPage />} />
          <Route path="hardware" element={<HardwarePage />} />
          <Route path="optimizer" element={<OptimizerPage />} />
          <Route path="evaluation" element={<EvaluationPage />} />
          <Route path="experiments" element={<ExperimentsPage />} />
          <Route path="experiments/:id" element={<ExperimentDetailPage />} />
          <Route path="plugins" element={<PluginsPage />} />
          <Route path="exports" element={<ExportsPage />} />
          <Route path="security" element={<SecurityPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
