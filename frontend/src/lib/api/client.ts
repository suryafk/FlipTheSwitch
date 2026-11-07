/**
 * Minimal typed HTTP client for the BLACKBOX API.
 *
 * Base URL: VITE_API_BASE_URL when set (e.g. a separately hosted API). By default it is empty, so
 * requests go to the same origin under /api — proxied to FastAPI by Vite in development and by
 * nginx in the Docker build.
 */

export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

/** Error envelope returned by the backend (see backend/app/core/errors.py). */
export interface ApiErrorBody {
  error: { code: string; message: string; request_id: string | null; details?: unknown };
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly body: unknown;

  constructor(
    status: number,
    code: string,
    message: string,
    requestId: string | null,
    body: unknown,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.body = body;
  }
}

function isErrorEnvelope(value: unknown): value is ApiErrorBody {
  return (
    typeof value === "object" &&
    value !== null &&
    "error" in value &&
    typeof (value as ApiErrorBody).error?.code === "string"
  );
}

export interface RequestOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
  /** Treat these non-2xx statuses as successful responses with a body (e.g. 503 from /health). */
  acceptStatuses?: number[];
}

export async function apiGet<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { timeoutMs = 8000, acceptStatuses = [] } = options;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const onAbort = () => controller.abort();
  options.signal?.addEventListener("abort", onAbort);

  try {
    let response: Response;
    try {
      response = await fetch(`${API_BASE_URL}${path}`, {
        headers: { Accept: "application/json" },
        signal: controller.signal,
      });
    } catch (cause) {
      const aborted = controller.signal.aborted;
      throw new ApiError(
        0,
        aborted ? "timeout_or_aborted" : "network_error",
        aborted ? "Request timed out or was cancelled." : "Backend unreachable.",
        null,
        cause,
      );
    }

    const requestId = response.headers.get("X-Request-ID");
    const body: unknown = await response.json().catch(() => null);

    if (response.ok || acceptStatuses.includes(response.status)) {
      return body as T;
    }
    if (isErrorEnvelope(body)) {
      throw new ApiError(response.status, body.error.code, body.error.message, requestId, body);
    }
    throw new ApiError(response.status, "http_error", `HTTP ${response.status}`, requestId, body);
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener("abort", onAbort);
  }
}
