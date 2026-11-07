# 09 — Foundation Implementation (Phase 1)

| | |
|---|---|
| **Scope** | Project foundation only: backend skeleton, frontend shell, SQLite with migrations, local infrastructure, developer experience |
| **Out of scope (not started)** | Switch integration, agents, simulator, incident workflow. Per the instruction to stop after this phase pending review. |
| **Date** | 2026-11-07 |

## 1. Inputs and discrepancies

The phase brief asked me to read `context.md`, `docs/02-SYSTEM-ARCHITECTURE.md` and `docs/02-ARCHITECTURE-DECISIONS.md`. **None of these files exist in the repository.** I used the closest approved equivalents:

- `Blackbox.md`: the product brief (the "context").
- `docs/03-AGENT-ARCHITECTURE.md`: the system architecture.
- `docs/04-SECURITY-ARCHITECTURE.md`.
- `docs/08-ARCHITECTURE-REVIEW.md`, `docs/08-IMPLEMENTATION-BACKLOG.md`, `docs/08-GO-NO-GO.md`: the decisions and the backlog.
- `docs/06-DESIGN-SYSTEM.md`: the UI tokens.

Before this phase, the repository contained only `Blackbox.md` and `docs/`. **No existing code was overwritten.**

## 2. Final project structure and rationale

```
FlipTheSwitch/
├── backend/                 FastAPI control plane (blackbox-api in docs/03)
│   ├── app/
│   │   ├── api/             HTTP routers (health only in Phase 1)
│   │   ├── core/            config, structured logging, error envelope, request-id middleware
│   │   ├── db/              engine/session, ORM models, migration helpers
│   │   └── main.py          app factory + lifespan (migrate → record startup event)
│   ├── migrations/          Alembic (0001_audit_events)
│   ├── tests/               pytest
│   ├── alembic.ini, pyproject.toml, uv.lock, Dockerfile
├── frontend/                React UI
│   ├── src/app/             router and top-level App
│   ├── src/components/      layout/ (AppShell, TopBar, SideNav), ui/ (Panel, StatusBadge)
│   ├── src/pages/           DashboardPage, NotFoundPage
│   ├── src/lib/api/         typed fetch client + endpoint modules
│   ├── src/hooks/           data hooks (useBackendHealth)
│   ├── src/styles/          Tailwind entry + design tokens
│   ├── Dockerfile, nginx.conf, vite.config.ts, eslint.config.js
├── simulator/               reserved (README only)
├── infra/docker-compose.yml local stack (backend + frontend)
├── docs/                    design docs 00–09
├── .env.example, .gitignore, Makefile, README.md, Blackbox.md
```

| Decision | Rationale |
|---|---|
| **The monorepo has five top-level areas**, exactly as requested | One clone, one `make` entry point. The areas map to separately deployable units (backend, frontend, simulator) plus shared docs and infra. |
| **Agents will live in `backend/` as separate entry points** (planned, not built) | `docs/08` keeps agents as *separate processes* with separate keys, but they share models, the Switch client and the DB schema with the API. A single Python project avoids duplicating them. Separation is enforced at runtime (processes, keys, tool allowlists), not by directory. *This is an open decision (§6), confirmed at the agent phase.* |
| **The Dockerfiles sit next to their code; Compose sits in `infra/`** | The Docker build context equals the component directory, which keeps the Dockerfiles simple and cache-friendly. `infra/` holds the orchestration. |
| **The root `.env` is shared** by the backend, Vite and Compose | One place to configure the stack. Each tool reads only its own prefixed variables. |
| **Sync SQLAlchemy with SQLite (WAL)** | Demo-scale, single writer (`docs/08` review). Async adds complexity with no benefit yet. WAL plus `busy_timeout` handle the agent processes added later. |
| **Alembic from day 1, with auto-migrate at startup** (switchable) | The schema will grow phase by phase. Migrations are reproducible. A fresh checkout needs no manual step. |
| **First table: the append-only `audit_events`**, with UPDATE and DELETE blocked by triggers | The append-only event log is a P0 requirement (`docs/04` §11, backlog P0-06). It is the one table every later phase writes to. It gives migrations and DB tests something real to verify. Domain tables are deliberately not created yet. |
| **Stdlib logging with a JSON formatter** (no structlog) | One fewer dependency. JSON lines, request-id correlation, and `extra=` fields cover the requirement. |
| **Uniform error envelope plus `X-Request-ID`** | The frontend and the future agents get one error shape. Request ids correlate UI, nginx and backend logs. That was verified: nginx's id appears in the backend logs. |
| **Health = liveness (`/live`) plus readiness (`/api/health`: DB and migrations, 503 when degraded)** | Only real checks. Switch and simulator checks are added when those integrations exist. No placeholder "ok" for systems we don't talk to yet. |
| **Vite + React 19 + TypeScript (strict) + Tailwind 4 (`@tailwindcss/vite`)**, React Router | Mainstream and fast. Tailwind 4 puts the design-system tokens (`docs/06`) in CSS `@theme` with no config file. |
| **Same-origin `/api`** (the Vite proxy in dev, nginx in Docker); `VITE_API_BASE_URL` is optional | No CORS problems in the default setups, and it matches the planned SSE streaming (nginx buffering is already off). |
| **Ports 8010 / 5180 / 8088, bound to `127.0.0.1`** | On this machine 8000 (an ssh forward), 5173, 3000 and 8100 (the Switch stack) are taken. Binding to localhost avoids exposing an unauthenticated API. |
| **System font stacks** (no Inter or JetBrains Mono download) | Keeps the app fully local with no external requests. The font names are listed first, so they're used if installed. |
| **No mocks** | The dashboard shows only the real `/api/health` result. The incidents panel states plainly that the workflow isn't implemented. |

## 3. What was built

### Backend (`backend/`)

- `app/core/config.py`: `pydantic-settings`, env prefix `BLACKBOX_*`; reads the root `.env` and then `backend/.env`; invalid values fail at startup.
- `app/core/logging.py`: JSON and console formatters; `request_id` context var; uvicorn loggers routed through it; noisy loggers quieted.
- `app/core/middleware.py`: `X-Request-ID` accept-or-generate (safe characters only), echoed, one log line per request with duration.
- `app/core/errors.py`: `ApiError` plus handlers for HTTP errors, validation (422 with details) and unhandled exceptions (generic 500, traceback logged).
- `app/db/`: engine with SQLite pragmas (WAL, foreign keys, busy timeout); session dependency; `AuditEvent` model; migration helpers (`upgrade_to_head`, `migration_status`); creates the SQLite directory if missing.
- `migrations/versions/0001_audit_events.py`: the table, an index, and the two append-only triggers.
- `app/api/health.py`: `/api/health/live` and `/api/health`.
- `app/main.py`: app factory; lifespan = migrate → write a `system.started` audit event; CORS for the dev origins.

### Frontend (`frontend/`)

- App shell: top bar with the `BLACKBOX` wordmark, a `SANDBOX` pill and the live API status badge; side nav; routed main area.
- `DashboardPage`: a Backend panel (status, version, environment, checks table, last-checked time; loading, degraded and unreachable states) and an Active incidents panel (honest empty state).
- `lib/api/client.ts`: a typed `apiGet` with timeout and abort, `ApiError` mapping of the backend envelope, and an explicit accept-list for status codes like 503 on health.
- `hooks/useBackendHealth.ts`: polls `/api/health` every 10 s.
- Design tokens from `docs/06` in `styles/index.css` (`@theme`); `StatusBadge` pairs color with a glyph (color is never the only signal).

### Infrastructure and developer experience

- `backend/Dockerfile` (python:3.13-slim, uv, non-root, healthcheck, `/data` volume); `frontend/Dockerfile` (node build → nginx); `frontend/nginx.conf` (SPA fallback, `/api` proxy, unbuffered).
- `infra/docker-compose.yml`: backend and frontend; the frontend waits for the backend to be healthy; ports on localhost.
- `Makefile`: `help`, `env`, `setup`, `dev-backend`, `dev-frontend`, `migrate`, `test`, `test-backend`, `test-frontend`, `lint`, `format`, `up`, `down`, `logs`, `ps`, `clean`.
- `.env.example` (no secrets; comments on separate lines because make and Compose parse inline comments inconsistently); `.gitignore` (env files, keys, SQLite files, venv, `node_modules`, `dist`).
- Linting and formatting: ruff (lint and format) for the backend; ESLint (typescript-eslint, react-hooks), Prettier and `tsc` for the frontend.

## 4. Verification actually executed (2026-11-07)

| Check | Command | Result |
|---|---|---|
| Backend tests | `make test-backend` | **14 passed**, 1 warning: Starlette says the `httpx`-based TestClient is deprecated in favour of `httpx2` |
| Frontend tests | `make test-frontend` | **8 passed** (2 files) |
| Lint and format and types | `make lint` | ruff: all checks passed; ruff format: clean; ESLint: no findings; Prettier: clean; `tsc -b`: no errors |
| Production frontend build | `npm run build` | Built: JS 227 kB (73 kB gzip), CSS 11.5 kB |
| Local backend, clean checkout (no `backend/data/`) | `make dev-backend` + curl | `/api/health` → 200 with database ok and migrations at `0001_audit_events`; unknown route → 404 envelope with the `X-Request-ID` echoed; `backend/data/blackbox.db` created |
| Local frontend | `make dev-frontend` + browser | Dashboard rendered with live health (database ok, migrations ok, "API ok" badge); no console errors; `/api/health` through the Vite proxy → 200 |
| Docker stack | `make up` / `make ps` / curl / `make down` | backend `healthy`; `http://localhost:8088/dashboard` → 200; `http://localhost:8088/api/health` → 200 through nginx; backend logs show nginx's request ids; stack stopped afterwards |

**A defect found and fixed during verification.** On a fresh checkout, startup failed with `sqlite3.OperationalError: unable to open database file`. Alembic opened its own connection before the app engine had created `backend/data/`. The tests had missed it because pytest's `tmp_path` always exists.

- **Fix:** `ensure_sqlite_directory()` now runs before migrations.
- **Regression test:** `test_startup_creates_missing_database_directory`.

**Not verified:**

- A completely fresh `make setup` on another machine (dependencies were installed incrementally here).
- Windows.
- Behavior with the repository under git (it is not a git repository yet; see §6).

## 5. Files created (no existing files changed)

- **Root:** `README.md`, `.env.example`, `.gitignore`, `Makefile`. `.env` was created locally from the example; it is git-ignored and contains no secrets.
- **`backend/`:** `pyproject.toml`, `uv.lock`, `alembic.ini`, `Dockerfile`, `app/__init__.py`, `app/main.py`, `app/api/{__init__,health}.py`, `app/core/{__init__,config,logging,errors,middleware}.py`, `app/db/{__init__,session,models,migrate}.py`, `migrations/{env.py,script.py.mako,versions/0001_audit_events.py}`, `tests/{__init__,conftest,test_health,test_errors_and_middleware,test_database,test_config_and_logging}.py`
- **`frontend/`:** `package.json`, `package-lock.json`, `index.html`, `vite.config.ts`, `tsconfig{,.app,.node}.json`, `eslint.config.js`, `.prettierrc.json`, `.prettierignore`, `Dockerfile`, `nginx.conf`, `src/{main.tsx,vite-env.d.ts}`, `src/app/{App.tsx,App.test.tsx}`, `src/components/layout/{AppShell,TopBar,SideNav}.tsx`, `src/components/ui/{Panel,StatusBadge}.tsx`, `src/pages/{DashboardPage,NotFoundPage}.tsx`, `src/lib/api/{client.ts,client.test.ts,health.ts}`, `src/hooks/useBackendHealth.ts`, `src/styles/index.css`, `src/test/setup.ts`
- **`infra/docker-compose.yml`**
- **`simulator/README.md`**
- **`docs/09-FOUNDATION-IMPLEMENTATION.md`** (this file)

## 6. Unresolved technical decisions

| # | Decision | Options / recommendation | Needed by |
|---|---|---|---|
| 1 | **Missing referenced docs** (`context.md`, `docs/02-*`) | Confirm that `Blackbox.md` and the 03/08 docs are the intended sources, or provide the files | Now |
| 2 | **Git repository** | The folder isn't a git repo, so "secrets never committed" relies on `.gitignore` alone. Recommend `git init`, plus a pre-commit secret scan (gitleaks) when the first secret appears. I didn't initialize it because it wasn't requested. | Before the first commit |
| 3 | **Where agents live** | (a) Entry points inside `backend/` (recommended: shared models and Switch client) or (b) a separate `agents/` project | Agent phase |
| 4 | **Switch stack in `infra/`** | The verified Switch 0.31.0 stack currently runs from a scratch clone. Recommend `infra/switch/` with the pinned standalone compose and the bootstrap script (backlog P0-03). | Switch phase |
| 5 | **LLM runtime and coding-agent mode** (backlog P0-02) | Anthropic SDK tool loop for agents; Claude Code CLI for coding. Pending gate G1 (SP-1). | Before agents |
| 6 | **Open gates G1–G3** (`docs/08-GO-NO-GO.md`) | SP-1 (Claude Code), SP-7 (host stability; the Docker VM is still 2 CPU / 2 GB), SP-5 (calibration). Not run. | Before the agent and simulator phases |
| 7 | **The test client's `httpx` deprecation** | Starlette recommends `httpx2`. Harmless now; revisit when bumping FastAPI/Starlette. | Low |
| 8 | **Node versions** | Local Node 26.5 prints a `DEP0205` deprecation warning from Vite tooling (harmless); the Docker build uses Node 22.16 LTS. Recommend standardising on Node 22 LTS (add `.nvmrc`). | Low |
| 9 | **UI authentication** | None. Ports are bound to localhost. The IC role and revoke action (`docs/06`, `docs/04`) need at least a local login in a later phase. | UI phase |
| 10 | **The `audit_events` schema** | A minimal schema. The full event model (`incident_id`, provenance, hash-chain columns per `docs/04` §11) is added by migration in the API phase. | API phase |
