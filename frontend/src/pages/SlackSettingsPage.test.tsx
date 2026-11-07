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

describe("War room (incident page)", () => {
  it("renders stages, feed, handoff, approval, recovery and the diff from real data", async () => {
    const warroom = {
      incident: {
        incident_id: "INC-1043",
        service: "payment-service",
        severity: "SEV-2",
        state: "PREVENTIVE_TASKS_CREATED",
        title: "payment-service: PaymentServiceHigh5xx",
        room_name: "incident-payment-service-1043-3bb1",
        created_at: "2026-11-07 11:40:31",
        updated_at: "2026-11-07 11:42:39",
      },
      room: { name: "incident-payment-service-1043-3bb1", id: "r1" },
      stages: [
        { state: "DETECTED", label: "Detected", status: "done" },
        { state: "RESOLVED", label: "Resolved", status: "current" },
      ],
      resolved: true,
      timeline: [
        {
          seq: 1,
          at: "2026-11-07T11:40:50Z",
          agent: "Sentinel",
          summary: "Handed investigation to RootCause through Switch (HND-1043-001)",
          level: "info",
          kind: "handoff.sent",
        },
      ],
      evidence: [{ kind: "metric", label: "http_5xx_rate_pct", value: "10.22", simulated: false }],
      hypotheses: [],
      root_cause: "deploy changed config/database.yml pool_size 50 -> 10",
      handoffs: [
        {
          handoff_id: "HND-1043-001",
          from: "sentinel",
          to: "rootcause",
          type: "investigation",
          status: "acked",
          switch_event_id: "sw_abc",
          sha256: "a".repeat(64),
          created_at: "2026-11-07 11:40:50",
        },
      ],
      remediation: {
        remediation_id: "REM-1043-1",
        status: "staged",
        attempts: 1,
        commit: "1e5032e2b9250ba7",
        base: "9d33906",
        patch_hash: "sha256:57750f3a",
        coding_mode: "claude_code",
        files: ["config/database.yml"],
        new_tests: ["test_pool_capacity_meets_peak_concurrency"],
        new_tests_fail_on_base: true,
        tests: { passed: 11, total: 11, failed: 0 },
        post_check_problems: [],
        agent: { cost_usd: 0.04, duration_s: 12, summary: "" },
        diff: "-pool_size: 10\n+pool_size: 50",
      },
      approval: {
        approval_id: "APR-1043-1",
        status: "approved",
        request_id: "req",
        action: "apply_remediation_commit",
        environment: "staging-sandbox",
        patch_hash: "sha256:57750f3a",
        expires_at: "2026-11-07T11:56:08Z",
        answered_by: "ic-user",
        answered_at: "2026-11-07 11:42:06",
        verification: {
          source: "outcomes",
          checks: [{ check: "not_revoked", ok: true, detail: "" }],
        },
      },
      execution: {
        execution_id: "EXR-1043-1",
        status: "succeeded",
        deploy_id: "843",
        commit: "1e5032e",
        before: { error_rate_pct: 21.31, latency_ms_p50: 4054, pool_utilization_pct: 100 },
        after: { error_rate_pct: 0.22, latency_ms_p50: 100.8, pool_utilization_pct: 24.2 },
        samples: [{}, {}, {}],
      },
      report: { generated_at: "2026-11-07 11:42:39" },
      preventive_tasks: [{ slug: "t", text: "Keep the capacity test in CI", owner: "owner" }],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const path = new URL(String(input), "http://localhost").pathname;
        const body =
          path === "/api/incidents/INC-1043/warroom"
            ? warroom
            : path === "/api/metrics/live"
              ? { error_rate_pct: 0.2, latency_ms_p50: 101, pool_utilization_pct: 24 }
              : { status: "ok", service: "x", version: "1", environment: "t", checks: {} };
        return Promise.resolve(
          new Response(JSON.stringify(body), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }),
    );
    renderAt("/incidents/INC-1043");
    expect(await screen.findByRole("heading", { name: /INC-1043/ })).toBeInTheDocument();
    expect(screen.getByText("⏱ 2m 08s to resolution")).toBeInTheDocument();
    expect(screen.getByText(/HND-1043-001 · sentinel → rootcause/)).toBeInTheDocument();
    expect(screen.getByText("Approved by ic-user at", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("Recovery verified over 3 measured samples.")).toBeInTheDocument();
    expect(screen.getByText("+pool_size: 50")).toBeInTheDocument();
    expect(screen.getByText("• Keep the capacity test in CI")).toBeInTheDocument();
    expect(await screen.findByText("0.2")).toBeInTheDocument(); // live 5xx from /api/metrics/live
  });
});
