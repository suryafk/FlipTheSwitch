from pathlib import Path

import pytest

from payment_service.config import (
    DatabaseSettings,
    load_app_settings,
    load_capacity_settings,
    load_database_settings,
)
from payment_service.db import engine_kwargs


def test_database_settings_load_with_types():
    s = load_database_settings()
    assert isinstance(s, DatabaseSettings)
    assert isinstance(s.pool_size, int)
    assert s.pool_timeout_seconds > 0


def test_capacity_settings_load():
    assert load_capacity_settings().peak_concurrent_db_sessions > 0


def test_app_settings_load():
    assert load_app_settings().service == "payment-service"


def test_engine_kwargs_map_pool_settings():
    s = load_database_settings()
    kw = engine_kwargs(s)
    assert kw["pool_size"] == s.pool_size
    assert kw["max_overflow"] == s.max_overflow
    assert kw["pool_timeout"] == s.pool_timeout_seconds


def test_missing_config_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_database_settings(tmp_path)
