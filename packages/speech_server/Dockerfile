# syntax=docker/dockerfile:1
# Crewquarters speech server (OpenAI-compatible STT and TTS on sherpa-onnx). Build context: repo root.
# Models are not baked in: `crewq-speech download` fills the /models volume once (make speech-models).
FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9 AS build
WORKDIR /src
COPY packages/speech_server packages/speech_server
RUN pip install --no-cache-dir --target /opt/app "./packages/speech_server[sherpa]"

FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9
# onnxruntime (inside sherpa-onnx-core) links libgomp on Linux.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHONPATH=/opt/app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    CREWQ_SPEECH_MODELS_DIR=/models CREWQ_SPEECH_ENGINE=sherpa
COPY --from=build /opt/app /opt/app
# A named volume mounted here inherits this ownership, so the non-root download job can write it.
RUN mkdir /models && chown 10001:10001 /models
USER 10001:10001
EXPOSE 8200
ENTRYPOINT ["python", "-m", "crewquarters_speech"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8200"]
