"""The payments database engine and its connection pool, built from config/database.yml."""

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

from payment_service.config import DatabaseSettings


def engine_kwargs(settings: DatabaseSettings) -> dict:
    return {
        "poolclass": AsyncAdaptedQueuePool,
        "pool_size": settings.pool_size,
        "max_overflow": settings.max_overflow,
        "pool_timeout": settings.pool_timeout_seconds,
        "pool_recycle": settings.pool_recycle_seconds,
    }


def build_engine(url: str, settings: DatabaseSettings) -> AsyncEngine:
    return create_async_engine(url, **engine_kwargs(settings))
