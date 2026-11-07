"""FastAPI application factory."""

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.health import router as health_router
from app.api.incidents import router as incidents_router
from app.api.preflight import router as preflight_router
from app.api.simulation import router as simulation_router
from app.api.warroom import router as warroom_router
from app.api.webhooks import router as webhooks_router
from app.core.config import Settings, get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.db.migrate import upgrade_to_head
from app.db.models import AuditEvent
from app.db.session import get_sessionmaker
from app.slack.config import get_slack_settings
from app.slack.routes import router as slack_router

logger = logging.getLogger("app")


def _record_startup(settings: Settings) -> None:
    with get_sessionmaker()() as session:
        session.add(
            AuditEvent(
                kind="system.started",
                actor="blackbox-backend",
                payload_json=json.dumps({"version": __version__, "env": settings.app_env}),
            )
        )
        session.commit()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if settings.auto_migrate:
            upgrade_to_head(settings.database_url)
            # Alembic configures logging from alembic.ini; restore ours.
            configure_logging(settings.log_level, settings.log_format)
        _record_startup(settings)
        logger.info(
            "startup complete",
            extra={"version": __version__, "env": settings.app_env},
        )
        stop, bridge_task = asyncio.Event(), None
        if get_slack_settings().bridge_in_api:
            from app.slack.api import SlackClient
            from app.slack.bridge import SlackBridge

            slack_client = SlackClient(get_slack_settings().api_base)
            bridge_task = asyncio.create_task(SlackBridge(slack_client).run_forever(stop))
        yield
        if bridge_task:
            stop.set()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(bridge_task, 10)
            await slack_client.aclose()
        logger.info("shutdown")

    app = FastAPI(title="BLACKBOX API", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(webhooks_router)
    app.include_router(incidents_router)
    app.include_router(warroom_router)
    app.include_router(simulation_router)
    app.include_router(preflight_router)
    app.include_router(slack_router)
    return app


app = create_app()
