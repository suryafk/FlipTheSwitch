"""Filesystem layout of the simulation runtime (simulator/runtime/, git-ignored)."""

from pathlib import Path

SIM_DIR = Path(__file__).resolve().parents[1]
SEED_DIR = SIM_DIR / "seed" / "payment-service"
RUNTIME = SIM_DIR / "runtime"
DEMO_REPO = RUNTIME / "demo-repo"
RELEASES = RUNTIME / "releases"
LOG_DIR = RUNTIME / "logs"
APP_LOG = LOG_DIR / "app.jsonl"
DEPLOYMENTS = RUNTIME / "deployments.jsonl"
STATE = RUNTIME / "launcher-state.json"
SHAS = RUNTIME / "shas.json"
PAYMENTS_DB = RUNTIME / "payments.db"

SERVICE_NAME = "payment-service"
SERVICE_HOST = "127.0.0.1"
SERVICE_PORT = 8090
LAUNCHER_PORT = 9001
