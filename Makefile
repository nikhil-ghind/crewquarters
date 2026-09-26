# Crewquarters developer commands. Requires: uv, Docker with Compose, Node (npx) for contracts.
SHELL := /bin/bash
COMPOSE := docker compose -f infra/compose/compose.yaml
FAKE_COMPOSE := $(COMPOSE) --profile fake
OPENAPI_PY_CLIENT := openapi-python-client==0.29.1
OPENAPI_TS := openapi-typescript@7.4.4

# Every Python package with a src/ tree that `make lint` type-checks.
MYPY_PATHS := packages/shared_python/src services/control_api/src services/scheduler/src \
	services/model_gateway/src services/runtime_daemon/src \
	packages/secret_store/src services/capability_broker/src services/knowledge/src \
	packages/python_sdk/src packages/fake_platform/src packages/crewctl/src \
	agents/contract_probe/src agents/gmail_digest/src agents/caller/src agents/personal_space/src
# Suites that need no PostgreSQL (SDK, fake platform, crewctl, agents, fake-platform integration).
SDK_TESTS := packages/python_sdk packages/fake_platform packages/crewctl agents tests/integration \
	tests/contract/test_broker_contract_files.py tests/contract/test_fake_route_parity.py \
	tests/contract/test_fake_traffic_conformance.py

AGENTS := contract_probe gmail_digest caller personal_space
REGISTRY ?= localhost:5001
FAKE_URL ?= http://127.0.0.1:8090
DEMO := tests/fixtures/scenarios/demo
# Lazy (=) so it is only evaluated by the targets that need it.
HOST_PLATFORM = $(shell uv run python -c "from crewctl.build import host_platform; print(host_platform())")

.PHONY: help sync db-up db-down migrate dev-up dev-down dev-bootstrap dev-logs coverage \
        test test-platform test-contract test-sdk contracts contracts-check lint fmt image image-arm64 \
        fake-up fake-down agent-images e2e-images e2e \
        demo-seed demo-reset demo-run demo-pending demo-approve evidence \
        integration-up integration-down demo-up demo-down realstack-images realstack-up realstack-test realstack-down

help:
	@grep -E '^[a-z0-9-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-16s %s\n", $$1, $$2}'

sync: ## Install the Python workspace (uv), including the SDK, crewctl, fake platform, and agents
	uv sync --frozen --all-packages

db-up: ## Start PostgreSQL + pgvector on 127.0.0.1:55432
	$(COMPOSE) up -d --wait postgres

db-down: ## Stop PostgreSQL (keeps the volume)
	$(COMPOSE) stop postgres

migrate: db-up ## Apply database migrations to the dev database
	uv run alembic -c services/control_api/alembic.ini upgrade head

dev-up: ## Build and start the core stack (proxy/UI on http://localhost:8080)
	$(COMPOSE) up -d --build --wait
	@echo "UI: http://localhost:8080/   Control API: http://localhost:8080/api/v1/docs"
	@echo "Create the owner with: make dev-bootstrap"

integration-up: ## Dev stack plus the runtime daemon in a container (real agent/model containers)
	mkdir -p $${CQ_DATA_DIR:-/tmp/crewquarters-data}
	docker build -f infra/docker/python.Dockerfile -t crewquarters/platform:dev .
	docker build -f infra/docker/proxy.Dockerfile -t crewquarters/proxy:dev .
	# The daemon creates the internal cq-models and cq-agents networks the gateway and
	# broker join.
	$(COMPOSE) -f infra/compose/compose.runtime.yaml up -d --wait runtime-daemon
	$(COMPOSE) -f infra/compose/compose.runtime.yaml up -d --wait
	@echo "UI/API: http://localhost:8080/ (runtime daemon: containerized, dev only)"

# --- Local demo: the full platform on this machine with the three demo agents -------------
# docs/runbooks/local-demo.md. LIVE_ENV=~/crewquarters-live.env switches the broker to real
# Google/Twilio (CQ_PROVIDER_MODE=live plus the OAuth client and allowed numbers).
LIVE_ENV ?=
DEMO_COMPOSE = $(COMPOSE) $(if $(LIVE_ENV),--env-file $(LIVE_ENV)) -f infra/compose/compose.runtime.yaml \
	-f infra/compose/compose.demo.yaml

demo-up: ## Full local demo on http://localhost:8080: runtime daemon + Gmail digest, caller, probe agents
	mkdir -p $${CQ_DATA_DIR:-/tmp/crewquarters-data}
	docker build -f infra/docker/python.Dockerfile -t crewquarters/platform:dev .
	docker build -f infra/docker/proxy.Dockerfile -t crewquarters/proxy:dev .
	$(COMPOSE) --profile fake up -d --wait registry
	uv run python tests/realstack/prepare.py --registry $(REGISTRY) --out .demo --no-test-variants
	$(DEMO_COMPOSE) up -d --wait runtime-daemon
	$(DEMO_COMPOSE) up -d --wait
	@echo "UI: http://localhost:8080/   First time: make dev-bootstrap for the owner setup code"

demo-down: ## Stop the local demo (keeps data; `make demo-down V=1` also deletes volumes and run data)
	# Agent and model containers belong to the runtime daemon, not to Compose; only this
	# stack's (on its agent and model networks) are removed.
	-docker ps -aq --filter label=io.crewquarters.kind --filter network=$${CQ_RUNTIME_AGENT_NETWORK:-cq-agents} | xargs -r docker rm -f
	-docker ps -aq --filter label=io.crewquarters.kind --filter network=$${CQ_RUNTIME_MODEL_NETWORK:-cq-models} | xargs -r docker rm -f
	$(if $(V),-$(DEMO_COMPOSE) run --rm --no-deps --entrypoint sh runtime-daemon \
		-c 'rm -rf "$$CQ_RUNTIME_DATA_DIR/runs" "$$CQ_RUNTIME_DATA_DIR/models"')
	# The fake profile holds the registry; including it lets `down` remove the network too.
	$(DEMO_COMPOSE) --profile fake down $(if $(V),-v)

integration-down: ## Stop the integration stack
	# Agent and model containers belong to the runtime daemon, not to Compose; only this
	# stack's (on its agent and model networks) are removed.
	-docker ps -aq --filter label=io.crewquarters.kind --filter network=$${CQ_RUNTIME_AGENT_NETWORK:-cq-agents} | xargs -r docker rm -f
	-docker ps -aq --filter label=io.crewquarters.kind --filter network=$${CQ_RUNTIME_MODEL_NETWORK:-cq-models} | xargs -r docker rm -f
	$(COMPOSE) -f infra/compose/compose.runtime.yaml down

dev-bootstrap: ## Print a one-time owner setup code for the running stack
	$(COMPOSE) exec control-api cq-admin bootstrap-token

dev-logs: ## Follow API and scheduler logs
	$(COMPOSE) logs -f control-api scheduler

dev-down: ## Stop the stack (keeps data)
	$(COMPOSE) down

test: test-platform ## Alias for test-platform

test-platform: db-up ## All unit, integration, and contract tests (control plane, SDK, agents)
	uv run pytest -q

test-sdk: ## SDK, fake platform, crewctl, and agent tests (no database needed)
	uv run pytest -q -m "not e2e and not live" $(SDK_TESTS)

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
	uv run mypy $(MYPY_PATHS)

fmt: ## Apply ruff fixes and formatting
	uv run ruff check --fix .
	uv run ruff format .

image: ## Build the platform and proxy images for this machine
	docker build -f infra/docker/python.Dockerfile -t crewquarters/platform:dev .
	docker build -f infra/docker/proxy.Dockerfile -t crewquarters/proxy:dev .

image-arm64: ## Build the linux/arm64 platform image (needs buildx + QEMU off-device)
	docker buildx build --platform linux/arm64 -f infra/docker/python.Dockerfile -t crewquarters/platform:arm64 --load .

# --- Person 5: agent development stack, images, E2E, demo ---------------------------------

fake-up: ## Start the fake platform (http://127.0.0.1:8090) and local registry (127.0.0.1:5001)
	$(FAKE_COMPOSE) up -d --build --wait fake-platform registry

fake-down: ## Stop the fake platform and registry
	$(FAKE_COMPOSE) stop fake-platform registry

agent-images: ## Build agent images for amd64+arm64, push to the local registry, pin into .e2e/
	@for agent in $(AGENTS); do \
		uv run crewctl build agents/$$agent --push --registry $(REGISTRY) \
			--platform linux/amd64,linux/arm64 --output-manifest .e2e/manifests/$$agent.yaml || exit 1; \
	done

e2e-images: ## Build host-architecture agent images, push, and pin into .e2e/ (needs make fake-up)
	@for agent in $(AGENTS); do \
		uv run crewctl build agents/$$agent --push --registry $(REGISTRY) \
			--platform $(HOST_PLATFORM) --output-manifest .e2e/manifests/$$agent.yaml || exit 1; \
	done

e2e: e2e-images ## Agents in hardened containers against the fake platform (needs make fake-up)
	CREWQ_E2E_PLATFORM_URL=$(FAKE_URL) uv run pytest -q -m e2e tests/e2e

demo-seed: ## Reset the fake platform and load the demo scenario
	CREWQ_FAKE_URL=$(FAKE_URL) infra/scripts/demo-seed.sh

demo-reset: ## Between rehearsals: clear runs/calls/sheets and reseed
	CREWQ_FAKE_URL=$(FAKE_URL) infra/scripts/demo-reset.sh

demo-run: ## make demo-run AGENT=gmail_digest|caller|contract_probe [RUN_ARGS=...]
	@test -n "$(AGENT)" || { echo "usage: make demo-run AGENT=gmail_digest|caller|contract_probe"; exit 2; }
	uv run crewq-fake run agents/$(AGENT) --url $(FAKE_URL) --launcher docker \
		--manifest .e2e/manifests/$(AGENT).yaml --config $(DEMO)/configs/$(AGENT).yaml $(RUN_ARGS)

demo-pending: ## List pending operator input requests on the fake platform
	uv run crewq-fake pending --url $(FAKE_URL)

demo-approve: ## Approve the pending input request on the fake platform
	uv run crewq-fake answer --url $(FAKE_URL) --choice approve

evidence: ## Run every suite and write evidence/<UTC>/report.md
	CREWQ_FAKE_URL=$(FAKE_URL) infra/scripts/collect-evidence.sh

# --- Performance harness (PLAN.md section 22; docs/benchmarks/laptop.md) --------------------
PERF_TAG ?= perf
.PHONY: perf
perf: ## Measure PLAN §22 targets in a separate stack (project cqperf, port 18094); writes tmp/perf/results.json
	docker build -f infra/docker/python.Dockerfile -t crewquarters/platform:$(PERF_TAG) .
	docker build -f infra/docker/proxy.Dockerfile -t crewquarters/proxy:$(PERF_TAG) .
	PERF_TAG=$(PERF_TAG) uv run python infra/scripts/perf/perf.py all $(PERF_ARGS)

# --- Real-stack E2E: real agent images through the real platform (docs/testing-realstack.md) ---
REALSTACK_ENV := infra/compose/realstack.env
REALSTACK_COMPOSE := docker compose -p cqreal --env-file $(REALSTACK_ENV) -f infra/compose/compose.yaml \
	-f infra/compose/compose.runtime.yaml -f infra/compose/compose.realstack.yaml
REALSTACK_DATA := /tmp/cqreal-data

realstack-images: ## Build the platform and proxy images tagged :cqreal
	docker build --load -f infra/docker/python.Dockerfile -t crewquarters/platform:cqreal .
	docker build --load -f infra/docker/proxy.Dockerfile -t crewquarters/proxy:cqreal .

realstack-up: realstack-images ## Real stack (project cqreal, http://localhost:18083) with pinned agent images
	mkdir -p $(REALSTACK_DATA)
	$(REALSTACK_COMPOSE) up -d --wait realstack-registry
	uv run python tests/realstack/prepare.py --registry localhost:15001
	# The daemon creates the cqreal-agents and cqreal-models networks the others join.
	$(REALSTACK_COMPOSE) up -d --wait runtime-daemon
	$(REALSTACK_COMPOSE) up -d --wait
	@echo "UI/API: http://localhost:18083/  (make realstack-test; make realstack-down)"

realstack-test: ## Run the real-stack E2E suite against a running `make realstack-up`
	uv run pytest -q -p no:cacheprovider -m realstack tests/realstack

realstack-down: ## Remove the cqreal stack: containers, volumes, agent networks, data
	-$(REALSTACK_COMPOSE) run --rm --no-deps --entrypoint sh runtime-daemon \
		-c 'rm -rf $(REALSTACK_DATA)/runs $(REALSTACK_DATA)/models'
	-docker ps -aq --filter network=cqreal-agents | xargs -r docker rm -f
	$(REALSTACK_COMPOSE) down -v --remove-orphans
	-docker network rm cqreal-agents cqreal-models
	-rmdir $(REALSTACK_DATA)
