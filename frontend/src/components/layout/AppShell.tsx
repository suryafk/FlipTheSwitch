import { Outlet } from "react-router-dom";

import { useBackendHealth } from "../../hooks/useBackendHealth";
import { SideNav } from "./SideNav";
import { TopBar } from "./TopBar";

export function AppShell() {
  const backend = useBackendHealth();
  return (
    <div className="flex h-full flex-col">
      <TopBar backend={backend} />
      <div className="flex min-h-0 flex-1">
        <SideNav />
        <main className="min-w-0 flex-1 overflow-y-auto p-4 md:p-6">
          <Outlet context={backend} />
        </main>
      </div>
    </div>
  );
}
