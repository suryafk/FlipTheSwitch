# BLACKBOX

An autonomous incident commander for the **Flip the Switch** hackathon (WeMakeDevs × Switch). Specialized AI agents collaborate in Switch rooms to investigate, patch and recover from production incidents, and a human approves every consequential action through a genuine Switch decision.

> **Status:** the foundation, the Switch integration proof, the incident simulator and the three agents are built. A full incident (faulty deploy, Sentinel, RootCause with a real Claude Code patch, Recovery, a Switch approval card, an executor-verified deploy and measured recovery) has run end to end; see [`docs/11-AGENTS-AND-SIMULATION.md`](docs/11-AGENTS-AND-SIMULATION.md). The frontend is still a shell. The design is in [`docs/`](docs/); start with [`docs/08-GO-NO-GO.md`](docs/08-GO-NO-GO.md) and [`docs/09-FOUNDATION-IMPLEMENTATION.md`](docs/09-FOUNDATION-IMPLEMENTATION.md).

## Repository layout

```
backend/     Python 3.12+ · FastAPI · SQLAlchemy · Alembic · SQLite   (the control-plane API)
frontend/    React 19 · TypeScript · Vite · Tailwind CSS 4            (the operator UI)
simulator/   payment-service simulation: demo app + git history, launcher, load, monitor
scripts/     stack.sh: start/stop the local demo stack
infra/       Docker Compose for the local stack
docs/        architecture, security, simulation, UX, feasibility and review documents
Blackbox.md  original product brief
Makefile     every developer command (run `make help`)
```

## Prerequisites

| Tool | Version used | Needed for |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | 0.13+ | Backend dependencies and Python (it can download Python 3.12+ itself) |
| Node.js and npm | Node 22+ (tested on 26.5), npm 10+ | Frontend |
| Docker with Compose v2 | Docker 29, Compose 5 | The Docker stack (optional for local development) |
| GNU make | 3.81+ | Convenience commands |

## Quick start (local development)

```bash
make setup
```

This creates `.env` from `.env.example` (if it is missing), runs `uv sync` in `backend/` and `npm ci` in `frontend/`.

Start the API in one terminal. It serves http://localhost:8010 and runs database migrations automatically at startup:

```bash
make dev-backend
```

Start the UI in a second terminal. It serves http://localhost:5180 and proxies `/api` to the backend:

```bash
make dev-frontend
```

Open http://localhost:5180. The dashboard shows the live result of `GET /api/health`.

Call the health endpoint directly:

```bash
curl http://localhost:8010/api/health
```

Expected response:

```json
{"status":"ok","service":"blackbox-backend","version":"0.1.0","environment":"development",
 "checks":{"database":{"status":"ok","detail":null},"migrations":{"status":"ok","detail":"at 0001_audit_events"}}}
```

`GET /api/health/live` is a dependency-free liveness probe. `GET /api/health` returns **503** with `"status":"degraded"` when the database or migrations are not OK.

The backend and frontend run independently. The frontend shows "API unreachable" when the backend is down, and the backend needs nothing from the frontend.

## Docker

Build and start the backend and frontend containers:

```bash
make up
```

| Service | URL | Notes |
|---|---|---|
| Web UI (nginx) | http://localhost:8088 | nginx proxies `/api` to the backend container |
| API | http://localhost:8010/api/health | Data in the `blackbox_backend-data` volume |

Other commands:

```bash
make ps
```

```bash
make logs
```

```bash
make down
```

`make down` stops the containers and keeps the data volume. `make clean` also deletes the data volume and local build outputs.

Ports bind to `127.0.0.1` only. Change them in `.env` (`BLACKBOX_BACKEND_PORT`, `BLACKBOX_FRONTEND_PORT`, `BLACKBOX_WEB_PORT`).

## Tests, lint and format

Run all tests (backend pytest and frontend vitest):

```bash
make test
```

Run the checks: ruff and format check for the backend; ESLint, Prettier check and `tsc` for the frontend.

```bash
make lint
```

Auto-format both projects:

```bash
make format
```

Individual suites: `make test-backend`, `make test-frontend`.

## Configuration

All configuration is through environment variables. They are read from the repository-root `.env`, which the backend, the Vite dev server and Docker Compose all use. Every variable is documented in [`.env.example`](.env.example).

- **Secrets:** Phase 1 requires none. `.env` and `.env.*` are git-ignored (except `.env.example`). Later phases add Switch agent keys, a model API key and webhook secrets; each will be documented in `.env.example` when it is introduced, never with real values.
- **Database:** SQLite at `backend/data/blackbox.db` locally, and `/data/blackbox.db` in Docker. Run migrations with `make migrate`; they also run at startup when `BLACKBOX_AUTO_MIGRATE=true`.
- **Logging:** `BLACKBOX_LOG_FORMAT=json` gives one JSON object per line. `console` is human-readable. Every request is logged once, with its `X-Request-ID`.

## Switch integration proof (Task 10)

These commands demonstrate a real integration with a running Switch server: agent registration, room creation, an agent message, a received event, a Switch approval card, and **independent verification** of the human's decision. Results and limitations are in [`docs/10-SWITCH-INTEGRATION-PROOF.md`](docs/10-SWITCH-INTEGRATION-PROOF.md).

The Switch stack must be running, and the `SWITCH_*` settings must be set in `.env` (see `.env.example`).

Run the proof with a person acting in Mattermost when prompted:

```bash
make switch-proof
```

Run it unattended; the TEST HARNESS acts as the IC through Mattermost's API:

```bash
make switch-proof-auto
```

Re-verify the latest stored approval in a fresh process:

```bash
make switch-verify
```

Approving in this phase executes nothing.

## Run the incident demo

Full details are in [`docs/11-AGENTS-AND-SIMULATION.md`](docs/11-AGENTS-AND-SIMULATION.md). Prerequisites: a running Switch stack, the Switch and simulator settings in `.env`, and the `claude` CLI logged in.

Register the agents (one time):

```bash
make agents-bootstrap
```

Reset the simulation to HEALTHY:

```bash
make sim-reset
```

Start the launcher, service, backend, load, monitor and agents:

```bash
make stack-up
```

Deploy the faulty commit:

```bash
make sim-trigger
```

Then approve on the card in the Mattermost incident channel. Stop everything with:

```bash
make stack-down
```

## API conventions (Phase 1)

- Errors always use the shape `{"error": {"code", "message", "request_id", "details?"}}`.
- Unexpected errors return a generic 500 and are logged with a traceback. Internal details are never returned.
- `X-Request-ID` is accepted (if it contains only safe characters, ≤ 64 of them) or generated, then echoed back and included in logs.
