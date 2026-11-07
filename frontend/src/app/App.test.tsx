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

describe("App shell", () => {
  it("redirects / to the dashboard and shows real backend health", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(200, {
          status: "ok",
          service: "blackbox-backend",
          version: "0.1.0",
          environment: "test",
          checks: { database: { status: "ok", detail: null } },
        }),
      ),
    );

    renderAt("/");
    expect(screen.getByRole("heading", { name: "Incident Command Dashboard" })).toBeInTheDocument();
    expect(await screen.findByText("Healthy")).toBeInTheDocument();
    expect(screen.getByText("database")).toBeInTheDocument();
    expect(screen.getByText("API ok")).toBeInTheDocument();
    expect(screen.getByText("No incident data yet.")).toBeInTheDocument();
  });

  it("shows degraded status from a 503 health response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(503, {
          status: "degraded",
          service: "blackbox-backend",
          version: "0.1.0",
          environment: "test",
          checks: { database: { status: "error", detail: "database unreachable" } },
        }),
      ),
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
