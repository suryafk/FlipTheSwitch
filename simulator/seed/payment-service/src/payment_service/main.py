"""payment-service HTTP API.

Each payment checks out one connection from the SQLAlchemy pool and holds it while the
fraud-score step runs. The pool size comes from config/database.yml; when demand exceeds the
pool, requests wait up to pool_timeout_seconds and then fail with 503 DB_POOL_TIMEOUT.
"""

import asyncio
import itertools
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import exc as sa_exc
from sqlalchemy import text

from payment_service.config import load_app_settings, load_database_settings
from payment_service.db import build_engine
from payment_service.metrics import Window, health_status

# Runtime facts injected by the deployment environment, not by this repository.
GIT_SHA = os.environ.get("SIM_GIT_SHA", "unknown")
DEPLOY_ID = os.environ.get("SIM_DEPLOY_ID", "unknown")
DB_URL = os.environ.get("SIM_DB_URL", "sqlite+aiosqlite:///./payments.db")
# Time the fraud-score step holds its connection (set by the environment; simulated work).
SCORING_SECONDS = float(os.environ.get("SIM_SCORING_SECONDS", "0.1"))
LOG_FILE = os.environ.get("SIM_LOG_FILE")

app_settings = load_app_settings()
db_settings = load_database_settings()


class JsonLog(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "git_sha": GIT_SHA,
            "deploy_id": DEPLOY_ID,
        }
        payload.update(getattr(record, "fields", {}))
        return json.dumps(payload)


log = logging.getLogger("payment_service")
log.setLevel(app_settings.log_level)
for handler in [logging.StreamHandler(sys.stdout)] + (
    [logging.FileHandler(LOG_FILE)] if LOG_FILE else []
):
    handler.setFormatter(JsonLog())
    log.addHandler(handler)
log.propagate = False

window = Window(seconds=15.0)
counter = itertools.count(1)


async def _sample_pool(engine) -> None:
    while True:
        pool = engine.pool
        window.record_pool(100.0 * pool.checkedout() / max(1, pool.size()))
        await asyncio.sleep(0.25)


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = build_engine(DB_URL, db_settings)
    app.state.engine = engine
    sampler = asyncio.create_task(_sample_pool(engine))
    log.warning(
        "startup",
        extra={"fields": {"pool_size": db_settings.pool_size,
                          "max_overflow": db_settings.max_overflow,
                          "pool_timeout_seconds": db_settings.pool_timeout_seconds}},
    )
    yield
    sampler.cancel()
    await engine.dispose()


app = FastAPI(title="payment-service", lifespan=lifespan)


class PaymentIn(BaseModel):
    amount_cents: int = Field(gt=0, le=10_000_000)
    currency: str = Field(pattern="^(USD|EUR)$")
    card_token: str = Field(min_length=4, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=128)


@app.post("/payments")
async def create_payment(body: PaymentIn, request: Request):
    started = time.perf_counter()
    status, content = 500, {"error": "INTERNAL"}
    n = next(counter)
    try:
        async with request.app.state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            await asyncio.sleep(SCORING_SECONDS)  # fraud-score step, connection held
        if body.card_token.endswith("0002"):
            status, content = 402, {"status": "declined"}
            log.info("payment_declined", extra={"fields": {"n": n}})
        elif n % 500 == 0:  # the (simulated) card processor's periodic timeout
            status, content = 502, {"error": "PROCESSOR_UNAVAILABLE"}
            log.error("processor_unavailable", extra={"fields": {"n": n}})
        else:
            status, content = 201, {"payment_id": f"pay_{n}", "status": "captured"}
            if n % 100 == 0:
                log.info("payment_captured", extra={"fields": {"n": n}})
    except sa_exc.TimeoutError as exc:
        window.record_pool_timeout()
        status, content = 503, {"error": "DB_POOL_TIMEOUT"}
        log.error(
            "payment_failed",
            extra={"fields": {"status": 503, "error": "DB_POOL_TIMEOUT",
                              "exception": "sqlalchemy.exc.TimeoutError",
                              "message": str(exc).splitlines()[0]}},
        )
    finally:
        window.record(status, (time.perf_counter() - started) * 1000)
    return JSONResponse(status_code=status, content=content)


@app.get("/health/live")
async def live():
    return {"status": "ok"}


@app.get("/health")
async def health(request: Request):
    summary = window.summary()
    status, reasons = health_status(summary)
    pool = request.app.state.engine.pool
    return {
        "status": status,
        "reasons": reasons,
        "git_sha": GIT_SHA,
        "deploy_id": DEPLOY_ID,
        "db": {"pool_size": pool.size(), "checked_out": pool.checkedout()},
        "window": summary,
    }


@app.get("/metrics/summary")
async def metrics_summary():
    return {**window.summary(), "git_sha": GIT_SHA, "deploy_id": DEPLOY_ID,
            "sampled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
