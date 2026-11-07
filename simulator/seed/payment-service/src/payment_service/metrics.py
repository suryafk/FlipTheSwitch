"""Rolling-window request metrics for /metrics/summary (payments routes only)."""

import time
from collections import deque


class Window:
    def __init__(self, seconds: float = 15.0) -> None:
        self.seconds = seconds
        self.requests: deque[tuple[float, int, float]] = deque()  # (t, status, duration_ms)
        self.pool: deque[tuple[float, float]] = deque()  # (t, utilization_pct)
        self.pool_timeouts: deque[float] = deque()  # t

    def _trim(self, now: float) -> None:
        cutoff = now - self.seconds
        while self.requests and self.requests[0][0] < cutoff:
            self.requests.popleft()
        while self.pool and self.pool[0][0] < cutoff:
            self.pool.popleft()
        while self.pool_timeouts and self.pool_timeouts[0] < cutoff:
            self.pool_timeouts.popleft()

    def record(self, status: int, duration_ms: float, now: float | None = None) -> None:
        self.requests.append((time.time() if now is None else now, status, duration_ms))

    def record_pool(self, utilization_pct: float, now: float | None = None) -> None:
        self.pool.append((time.time() if now is None else now, utilization_pct))

    def record_pool_timeout(self, now: float | None = None) -> None:
        self.pool_timeouts.append(time.time() if now is None else now)

    def summary(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        self._trim(now)
        n = len(self.requests)
        errors = sum(1 for _, status, _ in self.requests if status >= 500)
        durations = sorted(d for _, _, d in self.requests)

        def percentile(p: float) -> float | None:
            if not durations:
                return None
            return round(durations[min(len(durations) - 1, int(p * len(durations)))], 1)

        pool = [u for _, u in self.pool]
        return {
            "window_seconds": self.seconds,
            "requests": n,
            "rps": round(n / self.seconds, 1),
            "error_rate_pct": round(100.0 * errors / n, 2) if n else 0.0,
            "latency_ms_p50": percentile(0.50),
            "latency_ms_p95": percentile(0.95),
            "pool_utilization_pct": round(sum(pool) / len(pool), 1) if pool else 0.0,
            "pool_timeouts": len(self.pool_timeouts),
        }


def health_status(summary: dict) -> tuple[str, list[str]]:
    reasons = []
    if summary["error_rate_pct"] > 2:
        reasons.append("error_rate_pct>2")
    if (summary["latency_ms_p50"] or 0) > 1000:
        reasons.append("latency_ms_p50>1000")
    if summary["pool_utilization_pct"] > 90:
        reasons.append("pool_utilization_pct>90")
    return ("degraded" if reasons else "healthy"), reasons
