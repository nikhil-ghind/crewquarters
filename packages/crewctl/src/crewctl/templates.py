"""File templates for `crewctl init` (string.Template placeholders: $agent_id, $module, ...)."""

from string import Template

PYTHON_BASE_DIGEST = "sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9"

MANIFEST = Template(
    """\
apiVersion: crewquarters/v1alpha1
kind: Agent
metadata:
  id: $agent_id
  name: $title
  version: 0.1.0
  summary: $title agent scaffolded by crewctl.
spec:
  # `crewctl build --push` replaces REQUIRED_DIGEST with the pushed image digest.
  image: localhost:5001/crewquarters/$agent_id@sha256:REQUIRED_DIGEST
  entrypoint: ["python", "-m", "$module"]
  architectures: ["linux/amd64", "linux/arm64"]
  triggers: ["manual"]
  # Every entry is required. The owner approves exactly this list at install time.
  permissions:
    llmProfiles: []
    knowledge: []
    connectors: {}
    cloudProviders: []
    userInput: true
  resources:
    cpu: 0.5
    memoryMb: 256
    activeTimeoutSeconds: 300
    maxInputWaitSeconds: 3600
  configurationSchema:
    type: object
    properties:
      greeting:
        type: string
        default: Hello
  resultSchema:
    type: object
    required: [greeting, confirmed]
    properties:
      greeting: {type: string}
      confirmed: {type: boolean}
"""
)

PYPROJECT = Template(
    """\
[project]
name = "crewquarters-agent-$agent_id"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["crewquarters-sdk"]

[build-system]
requires = ["hatchling>=1.25"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/$module"]

[tool.uv.sources]
crewquarters-sdk = { workspace = true }
"""
)

MAIN = Template(
    """\
from typing import Any

from crewquarters import Agent, RunContext

agent = Agent("$agent_id")


@agent.run
async def run(ctx: RunContext[Any]) -> dict[str, Any]:
    await ctx.events.progress(10, "Starting")
    answer = await ctx.input.ask(
        "confirm-v1",
        "Continue?",
        "The agent is ready. Should it continue?",
        choices=["yes", "no"],
        timeout_seconds=600,
    )
    await ctx.events.progress(100, "Done")
    return {"greeting": ctx.config.get("greeting", "Hello"), "confirmed": answer.value == "yes"}


if __name__ == "__main__":
    agent.serve()
"""
)

DOCKERFILE = Template(
    """\
# syntax=docker/dockerfile:1
FROM python:3.12-slim@$base_digest AS build
WORKDIR /src
COPY packages/python_sdk packages/python_sdk
COPY $agent_path $agent_path
RUN pip install --no-cache-dir --target /opt/app ./packages/python_sdk ./$agent_path

FROM python:3.12-slim@$base_digest
ENV PYTHONPATH=/opt/app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY --from=build /opt/app /opt/app
USER 10001:10001
ENTRYPOINT ["python", "-m", "$module"]
"""
)

TEST = Template(
    """\
from pathlib import Path

from crewctl.testing import run_scenario

AGENT_DIR = Path(__file__).resolve().parents[1]


def test_default_scenario_succeeds(tmp_path: Path) -> None:
    outcome = run_scenario(AGENT_DIR, "default", log_dir=tmp_path)
    assert outcome.state == "SUCCEEDED", outcome.log
    assert outcome.result["confirmed"] is True
"""
)

SCENARIO = """\
name: default
timezone: UTC
inputs:
  autoAnswers:
    - keyPattern: "confirm-*"
      value: {choice: "yes"}
"""

CONFIG = "greeting: Hello\n"

README = Template(
    """\
# $title

Scaffolded by `crewctl init`.

```bash
crewctl validate --allow-unbuilt    # manifest checks
crewctl test                        # run against the in-process fake platform
crewctl build --push                # multi-arch build, pins the digest in manifest.yaml
crewctl publish --target local --platform-url http://localhost:8080
```
"""
)
