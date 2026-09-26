# syntax=docker/dockerfile:1
# Build context: repository root (crewctl build uses it automatically).
FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9 AS build
WORKDIR /src
COPY packages/python_sdk packages/python_sdk
COPY agents/pr_reviewer agents/pr_reviewer
RUN pip install --no-cache-dir --target /opt/app ./packages/python_sdk ./agents/pr_reviewer

FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9
ENV PYTHONPATH=/opt/app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY --from=build /opt/app /opt/app
USER 10001:10001
ENTRYPOINT ["python", "-m", "pr_reviewer"]
