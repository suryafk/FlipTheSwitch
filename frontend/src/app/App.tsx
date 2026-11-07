import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "../components/layout/AppShell";
import { DashboardPage } from "../pages/DashboardPage";
import { IncidentPage } from "../pages/IncidentPage";
import { NotFoundPage } from "../pages/NotFoundPage";
import { SimulationPage } from "../pages/SimulationPage";
import { SlackSettingsPage } from "../pages/SlackSettingsPage";

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/incidents/:incidentId" element={<IncidentPage />} />
        <Route path="/simulation" element={<SimulationPage />} />
        <Route path="/settings/slack" element={<SlackSettingsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
