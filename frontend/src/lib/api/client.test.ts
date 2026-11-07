import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiGet } from "./client";

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("apiGet", () => {
  it("returns the parsed body on success and calls the same-origin /api path", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { status: "ok" }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiGet("/api/health/live")).resolves.toEqual({ status: "ok" });
    expect(fetchMock).toHaveBeenCalledWith("/api/health/live", expect.any(Object));
  });

  it("maps the backend error envelope to ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse(
            409,
            { error: { code: "incident_locked", message: "busy", request_id: "r1" } },
            { "X-Request-ID": "r1" },
          ),
        ),
    );

    const err = await apiGet("/api/x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 409, code: "incident_locked", requestId: "r1" });
  });

  it("accepts explicitly allowed non-2xx statuses", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(503, { status: "degraded" })));
    await expect(apiGet("/api/health", { acceptStatuses: [503] })).resolves.toEqual({
      status: "degraded",
    });
  });

  it("reports network failures as ApiError network_error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(apiGet("/api/health")).rejects.toMatchObject({ status: 0, code: "network_error" });
  });
});
