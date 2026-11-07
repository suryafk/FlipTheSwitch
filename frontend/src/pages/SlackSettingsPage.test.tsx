import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "../app/App";

const base = {
  status: "healthy",
  connected: true,
  workspace: { team_id: "T0123", team_name: "Acme Ops" },
  deactivated_reason: null,
  scopes: ["channels:read", "chat:write", "commands"],
  channel_id: "C0INCIDENTS",
  last_success_at: "2026-11-07T05:56:51Z",
  last_error: null,
  last_error_at: null,
  backlog: 0,
  deliveries: { delivered: 12 },
  oauth_configured: true,
  commands_enabled: true,
};

function stub(status: number, body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("Slack settings page", () => {
  it("shows connection, workspace, channel, last delivery and health", async () => {
    stub(200, base);
    renderAt("/settings/slack");
    expect(await screen.findByText("Healthy")).toBeInTheDocument();
    expect(screen.getByText("Acme Ops (T0123)")).toBeInTheDocument();
    expect(screen.getByText("C0INCIDENTS")).toBeInTheDocument();
    expect(screen.getByText(/12 delivered/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Reinstall" })).toHaveAttribute(
      "href",
      "/api/slack/install",
    );
  });

  it("shows a disconnected workspace with the reason", async () => {
    stub(200, {
      ...base,
      status: "disconnected",
      connected: false,
      deactivated_reason: "Slack said token_revoked",
      last_error: "token_revoked",
    });
    renderAt("/settings/slack");
    expect(await screen.findByText("Disconnected")).toBeInTheDocument();
    expect(screen.getByText(/Not connected \(Slack said token_revoked\)/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Connect to Slack" })).toBeInTheDocument();
  });

  it("shows a degraded channel without inventing data", async () => {
    stub(200, { ...base, status: "degraded", last_error: "not_in_channel for channel C0X" });
    renderAt("/settings/slack");
    expect(await screen.findByText("Degraded")).toBeInTheDocument();
    expect(screen.getByText(/not_in_channel for channel C0X/)).toBeInTheDocument();
  });

  it("reports an unreachable backend", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    renderAt("/settings/slack");
    expect(await screen.findByText(/Could not load Slack status/)).toBeInTheDocument();
  });
});

describe("Incident page", () => {
  it("renders the incident and its sanitized timeline", async () => {
    stub(200, {
      incident: {
        incident_id: "INC-1042",
        service: "payment-service",
        severity: "SEV-1",
        state: "PREVENTIVE_TASKS_CREATED",
        title: "5xx spike",
        room_name: "inc-1042",
        created_at: "2026-11-07T05:55:21Z",
        updated_at: "2026-11-07T05:57:00Z",
      },
      timeline: [
        {
          seq: 3,
          at: "2026-11-07T05:55:21Z",
          agent: "Sentinel",
          summary: "Incident detected from a verified monitoring alert",
          level: "warning",
          kind: "incident.created",
        },
      ],
    });
    renderAt("/incidents/INC-1042");
    expect(
      await screen.findByRole("heading", { name: /INC-1042 · payment-service · SEV-1/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Incident detected from a verified monitoring alert"),
    ).toBeInTheDocument();
  });
});
