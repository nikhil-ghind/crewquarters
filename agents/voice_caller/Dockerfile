# syntax=docker/dockerfile:1
# Build context: repository root (crewctl build uses it automatically).
FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9 AS build
WORKDIR /src
COPY packages/python_sdk packages/python_sdk
COPY agents/caller agents/caller
COPY agents/voice_caller agents/voice_caller
RUN pip install --no-cache-dir --target /opt/app \
    ./packages/python_sdk ./agents/caller ./agents/voice_caller
# The agent has no internet at run time: bake in the VAD and end-of-turn model weights.
ENV HF_HOME=/opt/models/hf
RUN PYTHONPATH=/opt/app python -c "\
from livekit.plugins import silero; silero.VAD.load();\
from livekit.plugins.turn_detector.multilingual import _EUORunnerMultilingual as R;\
R._download_files(); print('VAD and end-of-turn weights cached')"

FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9
# onnxruntime (VAD, end-of-turn model) links libgomp on Linux.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHONPATH=/opt/app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/models/hf HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HOME=/tmp
COPY --from=build /opt/app /opt/app
COPY --from=build /opt/models /opt/models
USER 10001:10001
ENTRYPOINT ["python", "-m", "voice_caller"]
