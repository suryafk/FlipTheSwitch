#!/usr/bin/env bash
# Start/stop the local BLACKBOX demo stack (everything except Switch itself).
#
#   scripts/stack.sh up      # launcher (+ payment-service), backend API, load, monitor, 3 agents
#   scripts/stack.sh down
#   scripts/stack.sh status
#
# Logs: simulator/runtime/logs/<name>.log   PIDs: simulator/runtime/pids/<name>.pid
# Requires the Switch stack to be running and `make agents-bootstrap` to have been run once.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN="$ROOT/simulator/runtime"
LOGS="$RUN/logs"
PIDS="$RUN/pids"
mkdir -p "$LOGS" "$PIDS"
set -a; source "$ROOT/.env"; set +a

start() { # name dir command...
  local name=$1 dir=$2; shift 2
  if [ -f "$PIDS/$name.pid" ] && kill -0 "$(cat "$PIDS/$name.pid")" 2>/dev/null; then
    echo "$name already running"; return
  fi
  (cd "$dir" && exec "$@") >>"$LOGS/$name.log" 2>&1 &
  echo $! >"$PIDS/$name.pid"
  echo "started $name (pid $!)"
}

wait_http() { # url seconds
  for _ in $(seq 1 "$2"); do curl -s -m 2 -o /dev/null "$1" && return 0; sleep 1; done
  echo "timeout waiting for $1" >&2; return 1
}

up() {
  start launcher "$ROOT/simulator" uv run python -m simulator.launcher
  wait_http "http://127.0.0.1:8090/health/live" 40
  start backend "$ROOT/backend" uv run uvicorn app.main:app --host 127.0.0.1 --port "${BLACKBOX_BACKEND_PORT:-8010}" --no-access-log
  wait_http "http://127.0.0.1:${BLACKBOX_BACKEND_PORT:-8010}/api/health/live" 40
  start loadgen "$ROOT/simulator" uv run python -m simulator.loadgen
  start monitor "$ROOT/simulator" uv run python -m simulator.monitor
  start sentinel "$ROOT/backend" uv run python -m app.agents.sentinel
  start rootcause "$ROOT/backend" uv run python -m app.agents.rootcause
  start recovery "$ROOT/backend" uv run python -m app.agents.recovery
}

down() {
  for name in recovery rootcause sentinel monitor loadgen backend launcher; do
    if [ -f "$PIDS/$name.pid" ]; then
      pid=$(cat "$PIDS/$name.pid")
      # stop the process group started by uv (uv -> python)
      pkill -TERM -P "$pid" 2>/dev/null || true
      kill -TERM "$pid" 2>/dev/null || true
      rm -f "$PIDS/$name.pid"
      echo "stopped $name"
    fi
  done
  pkill -f "payment_service.main:app" 2>/dev/null || true
}

status() {
  for name in launcher backend loadgen monitor sentinel rootcause recovery; do
    if [ -f "$PIDS/$name.pid" ] && kill -0 "$(cat "$PIDS/$name.pid")" 2>/dev/null; then
      echo "$name: running"
    else
      echo "$name: stopped"
    fi
  done
}

"${1:-status}"
