# Crewquarters platform image: control API, scheduler/worker, model gateway, admin CLI,
# and the runtime daemon package (the appliance runs the daemon on the host from the
# .deb; the laptop integration profile runs it in a container).
# One image, different commands (README "Services and ownership boundaries").
# Builds for linux/amd64 and linux/arm64.
FROM python:3.12-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/shared_python/pyproject.toml packages/shared_python/
COPY services/control_api/pyproject.toml services/control_api/
COPY services/scheduler/pyproject.toml services/scheduler/
COPY services/runtime_daemon/pyproject.toml services/runtime_daemon/
COPY services/model_gateway/pyproject.toml services/model_gateway/
RUN for pkg in packages/shared_python/src/crewquarters_shared services/control_api/src/crewquarters_api \
        services/scheduler/src/crewquarters_scheduler services/runtime_daemon/src/crewquarters_runtime \
        services/model_gateway/src/crewquarters_gateway; do mkdir -p "$pkg" && touch "$pkg/__init__.py"; done \
    && uv sync --frozen --no-dev --no-editable --no-install-workspace
COPY packages/shared_python packages/shared_python
COPY services/control_api services/control_api
COPY services/scheduler services/scheduler
COPY services/runtime_daemon services/runtime_daemon
COPY services/model_gateway services/model_gateway
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm
RUN groupadd --system --gid 10001 crewquarters \
    && useradd --system --uid 10001 --gid crewquarters --no-create-home crewquarters
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY packages/contracts packages/contracts
COPY services/control_api/alembic.ini services/control_api/alembic.ini
COPY services/control_api/migrations services/control_api/migrations
COPY catalog catalog
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 CQ_CONTRACTS_DIR=/app/packages/contracts \
    CQ_GATEWAY_CATALOG_DIR=/app/catalog/models/dev CQ_RUNTIME_MODEL_PROFILES=/app/catalog/models/dev
USER 10001:10001
EXPOSE 8080
CMD ["cq-api"]
