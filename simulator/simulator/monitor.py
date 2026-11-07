"""Monitor: scrapes /metrics/summary, evaluates alert rules, sends HMAC-signed webhooks.

The *measurements* and *rules* are real; the monitoring vendor is simulated by this process.
"""

import asyncio
import hashlib
import hmac
import json
import os
import time
from datetime import UTC, datetime

import httpx

from simulator.paths import SERVICE_HOST, SERVICE_NAME, SERVICE_PORT

WEBHOOK_URL = os.environ.get("BLACKBOX_WEBHOOK_URL", "http://127.0.0.1:8010/api/webhooks/alert")
SECRET = os.environ.get("BLACKBOX_WEBHOOK_SECRET", "")
INTERVAL = 5.0
FOR_SAMPLES = 2
MIN_REQUESTS = 200
RULES = [
    ("PaymentServiceHigh5xx", "error_rate_pct", 5.0, "critical"),
    ("PaymentServiceHighLatency", "latency_ms_p50", 1000.0, "high"),
    ("PaymentDBPoolSaturated", "pool_utilization_pct", 90.0, "high"),
]


def sign(body: bytes, ts: str) -> str:
    return (
        "sha256=" + hmac.new(SECRET.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    )


async def send(http: httpx.AsyncClient, alert: dict) -> None:
    body = json.dumps(alert, sort_keys=True).encode()
    ts = str(int(time.time()))
    try:
        r = await http.post(
            WEBHOOK_URL,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Blackbox-Timestamp": ts,
                "X-Blackbox-Signature": sign(body, ts),
            },
        )
        print(f"monitor: sent {alert['status']} {alert['alert_id']} -> {r.status_code}", flush=True)
    except httpx.HTTPError as exc:
        print(f"monitor: webhook failed: {exc!r}", flush=True)


async def main() -> None:
    if not SECRET:
        raise SystemExit("BLACKBOX_WEBHOOK_SECRET must be set")
    breaches = 0
    clears = 0
    firing: dict | None = None
    seq = 1000 + int(time.time()) % 1000
    async with httpx.AsyncClient(timeout=4) as http:
        while True:
            try:
                s = (await http.get(f"http://{SERVICE_HOST}:{SERVICE_PORT}/metrics/summary")).json()
            except (httpx.HTTPError, ValueError):
                await asyncio.sleep(INTERVAL)
                continue
            fired = [
                (name, key, s.get(key) or 0, sev)
                for name, key, thr, sev in RULES
                if s.get("requests", 0) >= MIN_REQUESTS and (s.get(key) or 0) > thr
            ]
            if fired:
                breaches, clears = breaches + 1, 0
            else:
                breaches, clears = 0, clears + 1
            if fired and breaches >= FOR_SAMPLES and firing is None:
                seq += 1
                severity = "critical" if any(f[3] == "critical" for f in fired) else "high"
                firing = {
                    "schema": "blackbox.alert/v1",
                    "status": "firing",
                    "alert_id": f"ALT-{seq}",
                    "source": "synthetic-monitor",
                    "simulated": False,
                    "service": SERVICE_NAME,
                    "severity": severity,
                    "rules": [f[0] for f in fired],
                    "signals": {
                        "error_rate_pct": s.get("error_rate_pct"),
                        "latency_ms_p50": s.get("latency_ms_p50"),
                        "pool_utilization_pct": s.get("pool_utilization_pct"),
                        "requests_in_window": s.get("requests"),
                    },
                    "deploy_id": s.get("deploy_id"),
                    "git_sha": s.get("git_sha"),
                    "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "fingerprint": hashlib.sha256(
                        f"{SERVICE_NAME}|{'+'.join(sorted(f[0] for f in fired))}".encode()
                    ).hexdigest(),
                }
                await send(http, firing)
            elif firing is not None and clears >= FOR_SAMPLES:
                await send(
                    http,
                    {
                        **firing,
                        "status": "resolved",
                        "signals": {
                            "error_rate_pct": s.get("error_rate_pct"),
                            "latency_ms_p50": s.get("latency_ms_p50"),
                            "pool_utilization_pct": s.get("pool_utilization_pct"),
                            "requests_in_window": s.get("requests"),
                        },
                        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    },
                )
                firing = None
            await asyncio.sleep(INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
