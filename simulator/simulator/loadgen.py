"""Open-loop load generator: a fixed arrival rate that does not slow down when the service does.

This is the simulated part of the scenario (customers). Payloads are seeded and deterministic.

The rate is split across several worker processes (SIM_LOAD_WORKERS, default 4): one Python
event loop running httpx saturates a CPU core at ~120 rps once the service slows down and
~500 requests are in flight, and it then sheds load, which would make the outage look smaller
than it is. Each worker reports its own stats; together they send the configured rate.
"""

import argparse
import asyncio
import multiprocessing as mp
import os
import random
import signal
import time

import httpx

from simulator.paths import SERVICE_HOST, SERVICE_PORT

MAX_IN_FLIGHT_PER_WORKER = 800


async def worker(worker_id: int, rate: float, seed: int) -> None:
    rng = random.Random(seed * 1000 + worker_id)
    url = f"http://{SERVICE_HOST}:{SERVICE_PORT}/payments"
    limits = httpx.Limits(max_connections=1000, max_keepalive_connections=400)
    stats = {"sent": 0, "ok": 0, "errors": 0, "unreachable": 0, "shed": 0, "skipped": 0}
    # Bounded in-flight requests: the cap only bites if the service stops answering entirely.
    inflight = asyncio.Semaphore(MAX_IN_FLIGHT_PER_WORKER)
    async with httpx.AsyncClient(limits=limits, timeout=8) as http:

        async def one(i: int) -> None:
            async with inflight:
                card = (
                    "4242424242420002" if i % 50 == 0 else f"42424242424{rng.randint(10000, 99999)}"
                )
                body = {
                    "amount_cents": rng.randint(100, 50000),
                    "currency": "USD",
                    "card_token": card,
                    "idempotency_key": f"lg-{seed}-{worker_id}-{i}",
                }
                try:
                    r = await http.post(url, json=body)
                    stats["ok" if r.status_code < 500 else "errors"] += 1
                except httpx.HTTPError:
                    stats["unreachable"] += 1

        interval = 1.0 / rate
        # Stagger workers so their arrivals interleave instead of bunching.
        start = time.monotonic() + worker_id * interval / max(1, mp.cpu_count())
        i = 0
        last_report = time.monotonic()
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
                print(f"loadgen[{worker_id}]: {stats} in_flight={len(tasks)}", flush=True)
                last_report = time.monotonic()
                # Orphaned (the parent was killed without stopping us): stop sending, otherwise a
                # restarted generator would double the load.
                if os.getppid() != parent_pid:
                    print(f"loadgen[{worker_id}]: parent gone, exiting", flush=True)
                    return


def _run_worker(worker_id: int, rate: float, seed: int, parent: int) -> None:
    global parent_pid
    parent_pid = parent
    asyncio.run(worker(worker_id, rate, seed))


parent_pid = 0


def main(rate: float, seed: int, workers: int) -> None:
    per_worker = rate / workers
    print(f"loadgen: {rate} rps across {workers} workers ({per_worker:.1f} rps each)", flush=True)
    procs = [
        mp.Process(target=_run_worker, args=(w, per_worker, seed, os.getpid()), daemon=True)
        for w in range(workers)
    ]

    def stop(signum: int, _frame: object) -> None:
        for p in procs:
            if p.is_alive():
                p.terminate()
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    for p in procs:
        p.start()
    for p in procs:
        p.join()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--rate", type=float, default=float(os.environ.get("SIM_LOAD_RPS", "120")))
    p.add_argument("--workers", type=int, default=int(os.environ.get("SIM_LOAD_WORKERS", "4")))
    p.add_argument("--seed", type=int, default=42)
    a = p.parse_args()
    main(a.rate, a.seed, a.workers)
