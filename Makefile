# BLACKBOX developer commands. Run `make help`.
.DEFAULT_GOAL := help
SHELL := /bin/bash

# Ports can be overridden in .env (see .env.example).
-include .env
BLACKBOX_BACKEND_PORT ?= 8010

COMPOSE := docker compose -f infra/docker-compose.yml --project-directory . -p blackbox

.PHONY: help env setup dev-backend dev-frontend migrate test test-backend test-frontend \
        lint format up down logs ps clean slack-key slack-bridge slack-live-test switch-proof switch-proof-auto switch-verify \
        agents-bootstrap sim-reset sim-trigger sim-status stack-up stack-down stack-status

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' Makefile | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-16s %s\n", $$1, $$2}'

env: ## Create .env from .env.example if it does not exist
	@test -f .env && echo ".env already exists (left unchanged)" || (cp .env.example .env && echo "created .env")

setup: env ## Install backend (uv) and frontend (npm) dependencies
	cd backend && uv sync
	cd frontend && npm ci --no-audit --no-fund

dev-backend: ## Run the API with auto-reload on http://localhost:$(BLACKBOX_BACKEND_PORT)
	cd backend && uv run uvicorn app.main:app --reload --host 127.0.0.1 --port $(BLACKBOX_BACKEND_PORT) --no-access-log

dev-frontend: ## Run the Vite dev server (proxies /api to the backend)
	cd frontend && npm run dev

switch-proof: ## Prove the Switch integration; a human acts in Mattermost when prompted
	cd backend && uv run python -m app.switch.proof run

switch-proof-auto: ## Same, with the TEST HARNESS acting as the IC via Mattermost's API
	cd backend && uv run python -m app.switch.proof run --as-ic

switch-verify: ## Independently re-verify the latest stored approval against Switch
	cd backend && uv run python -m app.switch.proof verify

agents-bootstrap: ## Register the 4 agents, pin identities, create intake (add ARGS=--as-ic for the harness)
	cd backend && uv run python -m app.agents.bootstrap $(ARGS)

sim-reset: ## Reset the simulation to HEALTHY (deploy #841); stop the stack first
	set -a && . ./.env && set +a && cd simulator && uv run python -m simulator.ctl reset

sim-trigger: ## Deploy the faulty commit C5 as deploy #842
	set -a && . ./.env && set +a && cd simulator && uv run python -m simulator.ctl trigger

sim-status: ## Show launcher state and live service metrics
	cd simulator && uv run python -m simulator.ctl status

stack-up: ## Start launcher, service, backend, load, monitor and the three agents
	scripts/stack.sh up

stack-down: ## Stop everything started by stack-up
	scripts/stack.sh down

stack-status: ## Show which stack components are running
	scripts/stack.sh status

slack-key: ## Print a new BLACKBOX_SECRET_KEY (Fernet) for encrypting Slack tokens
	cd backend && uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

slack-bridge: ## Run the Slack event bridge as its own process (set SLACK_BRIDGE_IN_API=false)
	cd backend && uv run python -m app.slack.bridge

slack-live-test: ## Genuine Slack test (needs SLACK_LIVE_TEST=1, SLACK_LIVE_BOT_TOKEN, SLACK_LIVE_CHANNEL_ID)
	cd backend && uv run pytest -p no:cacheprovider -rs tests/integration/test_slack_live.py

migrate: ## Apply database migrations (also run automatically at API startup)
	cd backend && uv run alembic upgrade head

test: test-backend test-frontend ## Run all tests

test-backend: ## Run backend tests
	cd backend && uv run pytest

test-frontend: ## Run frontend tests
	cd frontend && npm test

lint: ## Lint and check formatting (backend + frontend)
	cd backend && uv run ruff check . && uv run ruff format --check .
	cd frontend && npm run lint && npm run format:check && npm run typecheck

format: ## Auto-format backend and frontend
	cd backend && uv run ruff format . && uv run ruff check --fix .
	cd frontend && npm run format

up: env ## Build and start the Docker stack (backend + frontend)
	$(COMPOSE) up -d --build

down: ## Stop the Docker stack (data volume kept)
	$(COMPOSE) down

logs: ## Follow Docker stack logs
	$(COMPOSE) logs -f

ps: ## Show Docker stack status
	$(COMPOSE) ps

clean: ## Stop the stack and delete its data volume and local build outputs
	$(COMPOSE) down -v
	rm -rf backend/data frontend/dist
