"""Typed settings loaded from the YAML files in config/."""

from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


@dataclass(frozen=True)
class DatabaseSettings:
    pool_size: int
    max_overflow: int
    pool_timeout_seconds: float
    pool_recycle_seconds: int


@dataclass(frozen=True)
class CapacitySettings:
    peak_concurrent_db_sessions: int


@dataclass(frozen=True)
class AppSettings:
    service: str
    log_level: str


def _load(name: str, config_dir: Path) -> dict:
    with open(config_dir / name, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{name} must contain a mapping")
    return data


def load_database_settings(config_dir: Path = CONFIG_DIR) -> DatabaseSettings:
    return DatabaseSettings(**_load("database.yml", config_dir))


def load_capacity_settings(config_dir: Path = CONFIG_DIR) -> CapacitySettings:
    return CapacitySettings(**_load("capacity.yml", config_dir))


def load_app_settings(config_dir: Path = CONFIG_DIR) -> AppSettings:
    return AppSettings(**_load("app.yml", config_dir))
