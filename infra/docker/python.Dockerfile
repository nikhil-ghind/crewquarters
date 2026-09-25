# Crewquarters platform image: control API, scheduler/worker, model gateway, capability
# broker, knowledge service, admin CLI,
# and the runtime daemon package (the appliance runs the daemon on the host from the
# .deb; the laptop integration profile runs it in a container).
# One image, different commands (README "Services and ownership boundaries").
# Builds for linux/amd64 and linux/arm64.
FROM python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS build
COPY --from=ghcr.io/astral-sh/uv:0.5.11@sha256:0ac957607303916420297a4c9c213bb33fbd3c888f9cd7f4f7273596ebf42b85 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/shared_python/pyproject.toml packages/shared_python/
COPY services/control_api/pyproject.toml services/control_api/
COPY services/scheduler/pyproject.toml services/scheduler/
COPY services/runtime_daemon/pyproject.toml services/runtime_daemon/
COPY services/model_gateway/pyproject.toml services/model_gateway/
COPY packages/secret_store/pyproject.toml packages/secret_store/
COPY services/capability_broker/pyproject.toml services/capability_broker/
COPY services/knowledge/pyproject.toml services/knowledge/
# The other workspace members (SDK, dev tools, agents) are not installed in this image, but uv
# needs their manifests to read the frozen workspace lock.
COPY packages/python_sdk/pyproject.toml packages/python_sdk/
COPY packages/fake_platform/pyproject.toml packages/fake_platform/
COPY packages/crewctl/pyproject.toml packages/crewctl/
COPY agents/contract_probe/pyproject.toml agents/contract_probe/
COPY agents/gmail_digest/pyproject.toml agents/gmail_digest/
COPY agents/caller/pyproject.toml agents/caller/
RUN for pkg in packages/shared_python/src/crewquarters_shared services/control_api/src/crewquarters_api \
        services/scheduler/src/crewquarters_scheduler services/runtime_daemon/src/crewquarters_runtime \
        services/model_gateway/src/crewquarters_gateway packages/secret_store/src/crewquarters_secret_store \
        services/capability_broker/src/crewquarters_broker services/knowledge/src/crewquarters_knowledge; do mkdir -p "$pkg" && touch "$pkg/__init__.py"; done \
    && uv sync --frozen --no-dev --no-editable --no-install-workspace
COPY packages/shared_python packages/shared_python
COPY services/control_api services/control_api
COPY services/scheduler services/scheduler
COPY services/runtime_daemon services/runtime_daemon
COPY services/model_gateway services/model_gateway
COPY packages/secret_store packages/secret_store
COPY services/capability_broker services/capability_broker
COPY services/knowledge services/knowledge
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
# PostgreSQL 16 client tools (pg_dump/pg_restore must match the server's major version)
# for backups and restore (cq-admin backup; docs/runbooks/backup-restore.md). Debian
# bookworm ships 15, so they come from the PostgreSQL project's apt repository.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
        https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-16 \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*
# The knowledge service's data directories and the backup directory exist in the image,
# owned by the service user, so a fresh named volume mounted there (laptop Compose) starts
# out writable. The appliance bind-mounts host directories instead (group crewquarters,
# setgid).
RUN groupadd --system --gid 10001 crewquarters \
    && useradd --system --uid 10001 --gid crewquarters --no-create-home crewquarters \
    && install -d -o 10001 -g 10001 -m 0750 /var/lib/crewquarters/documents \
        /var/lib/crewquarters/embedding-models \
    && install -d -o 10001 -g 10001 -m 0700 /var/lib/crewquarters/backups \
    # No setuid/setgid binaries: the services never change user (su, passwd, mount, ...).
    && find / -xdev -perm /6000 -type f -exec chmod a-s {} +
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
