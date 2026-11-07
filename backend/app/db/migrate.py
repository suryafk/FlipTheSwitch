"""Run and inspect Alembic migrations programmatically."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

from app.core.config import BACKEND_DIR
from app.db.session import ensure_sqlite_directory


def alembic_config(database_url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(BACKEND_DIR) / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def upgrade_to_head(database_url: str) -> None:
    # Alembic opens its own connection, possibly before the app engine has created the directory.
    ensure_sqlite_directory(database_url)
    command.upgrade(alembic_config(database_url), "head")


def migration_status(engine: Engine, database_url: str) -> dict[str, str | None | bool]:
    head = ScriptDirectory.from_config(alembic_config(database_url)).get_current_head()
    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    return {"current": current, "head": head, "up_to_date": current == head}
