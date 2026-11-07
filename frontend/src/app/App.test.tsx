import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => vi.unstubAllGlobals());

const HEALTHY = {
  status: "ok",
  service: "blackbox-backend",
  version: "0.1.0",
  environment: "test",
  checks: { database: { status: "ok", detail: null } },
};

/** Answers each API path with its own response. */
function routeFetch(routes: Record<string, () => Response>) {
  return vi.fn((input: RequestInfo | URL) => {
    const path = new URL(String(input), "http://localhost").pathname;
    const handler = routes[path];
    return handler
      ? Promise.resolve(handler())
      : Promise.resolve(jsonResponse(404, { error: { code: "not_found", message: "nope" } }));
  });
}

describe("App shell", () => {
  it("redirects / to the dashboard and shows real backend health", async () => {
    vi.stubGlobal(
      "fetch",
      routeFetch({
        "/api/health": () => jsonResponse(200, HEALTHY),
        "/api/incidents": () => jsonResponse(200, { incidents: [] }),
      }),
    );

    renderAt("/");
    expect(screen.getByRole("heading", { name: "Incident Command Dashboard" })).toBeInTheDocument();
    expect(await screen.findByText("Healthy")).toBeInTheDocument();
    expect(screen.getByText("database")).toBeInTheDocument();
    expect(screen.getByText("API ok")).toBeInTheDocument();
    expect(await screen.findByText("No incidents recorded.")).toBeInTheDocument();
  });

  it("lists incidents with links to their pages", async () => {
    vi.stubGlobal(
      "fetch",
      routeFetch({
        "/api/health": () => jsonResponse(200, HEALTHY),
        "/api/incidents": () =>
          jsonResponse(200, {
            incidents: [
              {
                incident_id: "INC-1042",
                service: "payment-service",
                severity: "SEV-1",
                state: "PREVENTIVE_TASKS_CREATED",
                title: "payment-service: PaymentServiceHighLatency",
                room_name: "incident-payment-service-1042",
                created_at: "2026-11-07 06:42:50",
                updated_at: "2026-11-07 06:44:07",
              },
            ],
          }),
      }),
    );

    renderAt("/dashboard");
    const link = await screen.findByRole("link", { name: /INC-1042/ });
    expect(link).toHaveAttribute("href", "/incidents/INC-1042");
    expect(screen.getByText("PREVENTIVE_TASKS_CREATED")).toBeInTheDocument();
    expect(screen.getByText("payment-service: PaymentServiceHighLatency")).toBeInTheDocument();
  });

  it("reports when incidents cannot be loaded", async () => {
    vi.stubGlobal(
      "fetch",
      routeFetch({
        "/api/health": () => jsonResponse(200, HEALTHY),
        "/api/incidents": () =>
          jsonResponse(500, { error: { code: "internal_error", message: "boom" } }),
      }),
    );

    renderAt("/dashboard");
    expect(await screen.findByText("Could not load incidents: boom")).toBeInTheDocument();
  });

  it("shows degraded status from a 503 health response", async () => {
    vi.stubGlobal(
      "fetch",
      routeFetch({
        "/api/health": () =>
          jsonResponse(503, {
            status: "degraded",
            service: "blackbox-backend",
            version: "0.1.0",
            environment: "test",
            checks: { database: { status: "error", detail: "database unreachable" } },
          }),
        "/api/incidents": () => jsonResponse(200, { incidents: [] }),
      }),
    );

    renderAt("/dashboard");
    expect(await screen.findByText("Degraded")).toBeInTheDocument();
    expect(screen.getByText("database unreachable")).toBeInTheDocument();
  });

  it("shows an unreachable backend without inventing data", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    renderAt("/dashboard");
    expect(await screen.findByText("Unreachable")).toBeInTheDocument();
    expect(screen.getByText("API unreachable")).toBeInTheDocument();
  });

  it("renders a not-found page for unknown routes", () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})));
    renderAt("/nope");
    expect(screen.getByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  });
});
