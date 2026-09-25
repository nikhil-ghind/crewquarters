# Crewquarters edge proxy (nginx, non-root uid 101, listens on 8080 and 8081) with the
# web UI baked in. Build from the repository root:
#   docker build -f infra/docker/proxy.Dockerfile -t crewquarters/proxy:dev .
#
# UI: the static build in apps/web/dist is served when it exists at build time; otherwise
# a small placeholder page (infra/proxy/placeholder) is. The glob `app[s]/we[b]/dis[t]`
# matches nothing when the directory is absent, and COPY does not fail as long as one
# source matches; when it exists, its files replace the placeholder.
# Build the UI first (apps/web: npm run build), then this image.
# Multi-arch (linux/amd64, linux/arm64): the base is a multi-platform index digest.
FROM nginxinc/nginx-unprivileged:1.28-alpine@sha256:7377697a821c131a924a7105fafbe7414db4e9fcc77a6f08f776f33f141ec3f8
ENV NGINX_ENTRYPOINT_QUIET_LOGS=1
COPY infra/proxy/nginx.conf /etc/nginx/nginx.conf
COPY infra/proxy/security-headers.conf infra/proxy/ui-headers.conf infra/proxy/json-errors.conf \
    /etc/nginx/cq/
COPY infra/proxy/placeholder/ app[s]/we[b]/dis[t] /usr/share/crewquarters/ui/
EXPOSE 8080 8081
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/index.html || exit 1
