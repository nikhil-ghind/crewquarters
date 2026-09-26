# VoXtream streaming text-to-speech server (catalog profile local.tts.voxtream). Started by
# the runtime daemon as a model container; build from the repository root:
#   docker build -f infra/docker/voxtream.Dockerfile -t crewquarters/voxtream:<tag> .
# The daemon runs model images only by digest, so push the image to a registry and pin the
# pushed digest in catalog/models/dgx/local.tts.voxtream.json (docs/runbooks/dgx.md,
# "Model images built from source").
#
# linux/arm64 with CUDA (GB10). VoXtream 0.1.5 serves herimor/voxtream; it pins
# torch==2.4.0, which has no GB10 (sm_121) kernels, so it is installed on torch 2.8 (cu129)
# without that pin. moshi 0.2.2 pins bitsandbytes<0.46, which has no arm64 wheel; moshi
# imports it but VoXtream's Mimi codec never quantizes, so a newer arm64 build is used.
FROM python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS build
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential cmake pkg-config libopus-dev curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENV CMAKE_POLICY_VERSION_MINIMUM=3.5 PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
RUN pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu129
# sphn (moshi's audio I/O) has no arm64 wheel: built from source with Rust.
RUN curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain 1.90.0 \
    && PATH=/root/.cargo/bin:$PATH pip install \
       "numpy>=1.26,<2.3" "safetensors>=0.4,<0.6" "einops>=0.7,<0.9" sentencepiece==0.2.0 "sphn>=0.1.4,<0.2" \
       torchtune==0.4.0 torchao==0.9.0 transformers==4.50.0 huggingface_hub==0.28.1 \
       g2p-en==2.1.0 librosa==0.11.0 soundfile==0.13.1 inflect==7.5.0 nltk==3.9.1 pydantic==2.10.6 \
       bitsandbytes==0.48.2 fastapi==0.118.0 "uvicorn[standard]==0.37.0" \
    && pip install --no-deps moshi==0.2.2 voxtream==0.1.5
COPY services/tts_server/fetch_assets.py /tmp/fetch_assets.py
RUN python /tmp/fetch_assets.py /opt/cq-tts

FROM python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
RUN apt-get update \
    && apt-get install -y --no-install-recommends libopus0 libsndfile1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10002 cqtts \
    && useradd --system --uid 10002 --gid cqtts --home /tmp --no-create-home cqtts \
    && find / -xdev -perm /6000 -type f -exec chmod a-s {} +
COPY --from=build /opt/venv /opt/venv
COPY --from=build /opt/cq-tts /opt/cq-tts
COPY services/tts_server/cq_tts_server.py services/tts_server/generator.json /opt/cq-tts/app/
# Everything resolves offline from the image: the model network has no internet.
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 HOME=/tmp \
    CQ_TTS_ASSETS=/opt/cq-tts HF_HOME=/opt/cq-tts/hf HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
    TORCH_HOME=/opt/cq-tts/torch NLTK_DATA=/opt/cq-tts/nltk_data TORCHDYNAMO_DISABLE=1
USER 10002:10002
EXPOSE 8000
ENTRYPOINT ["python", "/opt/cq-tts/app/cq_tts_server.py"]
