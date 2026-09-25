# Crewquarters edge proxy (nginx, non-root uid 101, listens on 8080 and 8081, plus 8443 in
# LAN HTTPS mode) with the web UI built in. Build from the repository root:
#   docker build -f infra/docker/proxy.Dockerfile -t crewquarters/proxy:dev .
#
# The web UI (apps/web) is built in the first stage, so every proxy image carries the UI that
# matches its source tree. The build output is static and architecture-independent, so it runs
# on the build platform. Multi-arch (linux/amd64, linux/arm64): both bases are multi-platform
# index digests.
FROM --platform=$BUILDPLATFORM node:22.18-bookworm-slim@sha256:752ea8a2f758c34002a0461bd9f1cee4f9a3c36d48494586f60ffce1fc708e0e AS web
WORKDIR /src/apps/web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund --ignore-scripts
COPY packages/contracts/clients/typescript /src/packages/contracts/clients/typescript
COPY apps/web ./
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.28.3-alpine@sha256:6a23acdfca2b9cfbcec61419e3f1426bcbedb91362f2f19306a8567423bb4612
ENV NGINX_ENTRYPOINT_QUIET_LOGS=1
# nginx.conf is the default (HTTP) mode; LAN HTTPS mode runs
# `nginx -c /etc/nginx/nginx-lan-https.conf` (infra/compose/compose.lan-https.yaml).
COPY infra/proxy/nginx.conf infra/proxy/nginx-lan-https.conf /etc/nginx/
COPY infra/proxy/common.conf infra/proxy/main-site.conf infra/proxy/callbacks-site.conf \
    infra/proxy/tls.conf infra/proxy/ca-download.conf infra/proxy/security-headers.conf \
    infra/proxy/ui-headers.conf infra/proxy/json-errors.conf /etc/nginx/cq/
COPY --from=web /src/apps/web/dist/ /usr/share/crewquarters/ui/
EXPOSE 8080 8081 8443
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/index.html || exit 1
