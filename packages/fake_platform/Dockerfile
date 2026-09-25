# syntax=docker/dockerfile:1
# Build context: repository root. Dev/test only; never part of a release.
FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9 AS build
WORKDIR /src
COPY packages/contracts packages/contracts
COPY packages/python_sdk packages/python_sdk
COPY packages/fake_platform packages/fake_platform
RUN pip install --no-cache-dir --target /opt/app ./packages/contracts ./packages/python_sdk ./packages/fake_platform

FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9
ENV PYTHONPATH=/opt/app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY --from=build /opt/app /opt/app
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["python", "-m", "crewquarters_fake"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
