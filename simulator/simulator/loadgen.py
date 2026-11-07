"""Open-loop load generator: a fixed arrival rate that does not slow down when the service does.

This is the simulated part of the scenario (customers). Payloads are seeded and deterministic.
"""

import argparse
import asyncio
import os
import random
import time

import httpx

from simulator.paths import SERVICE_HOST, SERVICE_PORT

MAX_IN_FLIGHT = 1500


async def main(rate: float, seed: int) -> None:
    rng = random.Random(seed)
    url = f"http://{SERVICE_HOST}:{SERVICE_PORT}/payments"
    limits = httpx.Limits(max_connections=2000, max_keepalive_connections=500)
    stats = {"sent": 0, "ok": 0, "errors": 0, "unreachable": 0, "shed": 0, "skipped": 0}
    # Bounded in-flight requests. At 120 rps with a 4 s pool timeout about 500 are in flight
    # when degraded; the cap only bites if the service stops answering entirely.
    inflight = asyncio.Semaphore(MAX_IN_FLIGHT)
    async with httpx.AsyncClient(limits=limits, timeout=8) as http:

        async def one(i: int) -> None:
            async with inflight:
                await _one(i)

        async def _one(i: int) -> None:
            card = "4242424242420002" if i % 50 == 0 else f"42424242424{rng.randint(10000, 99999)}"
            body = {
                "amount_cents": rng.randint(100, 50000),
                "currency": "USD",
                "card_token": card,
                "idempotency_key": f"lg-{seed}-{i}",
            }
            try:
                r = await http.post(url, json=body)
                stats["ok" if r.status_code < 500 else "errors"] += 1
            except httpx.HTTPError:
                stats["unreachable"] += 1

        interval = 1.0 / rate
        start = time.monotonic()
        i = 0
        last_report = start
        tasks: set[asyncio.Task] = set()
        while True:
            target = start + i * interval
            delay = target - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            elif delay < -1.0:
                # Fell behind (host sleep, stall): skip the missed arrivals instead of bursting
                # them all at once, which would wedge the generator.
                behind = int(-delay / interval)
                stats["skipped"] += behind
                i += behind
                continue
            if inflight.locked():
                stats["shed"] += 1
            else:
                task = asyncio.create_task(one(i))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
                stats["sent"] += 1
            i += 1
            if time.monotonic() - last_report >= 10:
                print(f"loadgen: {stats}", flush=True)
                last_report = time.monotonic()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--rate", type=float, default=float(os.environ.get("SIM_LOAD_RPS", "120")))
    p.add_argument("--seed", type=int, default=42)
    a = p.parse_args()
    asyncio.run(main(a.rate, a.seed))
