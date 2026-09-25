# Crewquarters developer commands. Requires: uv, Docker with Compose, Node (npx) for contracts.
SHELL := /bin/bash
COMPOSE := docker compose -f infra/compose/compose.yaml
OPENAPI_PY_CLIENT := openapi-python-client==0.29.1
OPENAPI_TS := openapi-typescript@7.4.4

.PHONY: help sync db-up db-down migrate dev-up dev-down dev-bootstrap dev-logs coverage integration-up integration-down deb bundle \
        test test-platform test-contract contracts contracts-check lint fmt image image-arm64

help:
	@grep -E '^[a-z0-9-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-16s %s\n", $$1, $$2}'

sync: ## Install the Python workspace (uv)
	uv sync --frozen

db-up: ## Start PostgreSQL + pgvector on 127.0.0.1:55432
	$(COMPOSE) up -d --wait postgres

db-down: ## Stop PostgreSQL (keeps the volume)
	$(COMPOSE) stop postgres

migrate: db-up ## Apply database migrations to the dev database
	uv run alembic -c services/control_api/alembic.ini upgrade head

dev-up: ## Build and start the core stack (API on http://127.0.0.1:8080)
	$(COMPOSE) up -d --build --wait
	@echo "Control API: http://127.0.0.1:8080/api/v1/docs"
	@echo "Create the owner with: make dev-bootstrap"

integration-up: ## Dev stack plus the runtime daemon in a container (real agent/model containers)
	mkdir -p $${CQ_DATA_DIR:-/tmp/crewquarters-data}
	docker build -f infra/docker/python.Dockerfile -t crewquarters/platform:dev .
	# The daemon creates the internal cq-models network the gateway joins.
	$(COMPOSE) -f infra/compose/compose.runtime.yaml up -d --wait runtime-daemon
	$(COMPOSE) -f infra/compose/compose.runtime.yaml up -d --wait
	@echo "Control API: http://127.0.0.1:8080/api/v1/docs (runtime daemon: containerized, dev only)"

integration-down: ## Stop the integration stack
	$(COMPOSE) -f infra/compose/compose.runtime.yaml down

dev-bootstrap: ## Print a one-time owner setup code for the running stack
	$(COMPOSE) exec control-api cq-admin bootstrap-token

dev-logs: ## Follow API and scheduler logs
	$(COMPOSE) logs -f control-api scheduler

dev-down: ## Stop the stack (keeps data)
	$(COMPOSE) down

test: test-platform ## Alias for test-platform

test-platform: db-up ## Control-plane unit, integration, and contract tests
	uv run pytest -q

coverage: db-up ## Tests with coverage; enforces thresholds on security/state modules
	uv run pytest -q --cov --cov-report=term --cov-report=json
	uv run python infra/scripts/check_coverage.py coverage.json

test-contract: db-up contracts-check ## Contract tests plus generated-artifact drift check
	uv run pytest -q tests/contract

contracts: ## Regenerate openapi.yaml and the TypeScript/Python clients
	uv run cq-admin export-openapi packages/contracts/openapi.yaml
	npx -y $(OPENAPI_TS) packages/contracts/openapi.yaml -o packages/contracts/clients/typescript/schema.d.ts
	rm -rf packages/contracts/clients/python/crewquarters_client
	uvx --from $(OPENAPI_PY_CLIENT) openapi-python-client generate \
		--path packages/contracts/openapi.yaml \
		--output-path packages/contracts/clients/python/crewquarters_client --meta none

contracts-check: contracts ## Fail if generated contracts differ from what is committed
	@git diff --exit-code --stat -- packages/contracts || \
		(echo "Generated contracts are stale: run 'make contracts' and commit." && exit 1)
	@test -z "$$(git ls-files --others --exclude-standard packages/contracts)" || \
		(echo "Untracked generated files in packages/contracts." && exit 1)

lint: ## Ruff lint + format check, mypy
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy packages/shared_python/src services/control_api/src services/scheduler/src

fmt: ## Apply ruff fixes and formatting
	uv run ruff check --fix .
	uv run ruff format .

image: ## Build the platform image for this machine
	docker build -f infra/docker/python.Dockerfile -t crewquarters/platform:dev .

image-arm64: ## Build the linux/arm64 platform image (needs buildx + QEMU off-device)
	docker buildx build --platform linux/arm64 -f infra/docker/python.Dockerfile -t crewquarters/platform:arm64 --load .
