# syntax=docker/dockerfile:1.7
# =============================================================================
#  voice - wake phrase, speech-to-text (echo), Claude (Agent SDK), Piper TTS
# =============================================================================
#  Stage 1 builds echo-stream (C++) and downloads the speech models.
#  Stage 2 builds the Python environment and downloads the Piper voices.
#  Stage 3 is the slim runtime: no compilers, non-root, read-only friendly.
#
#  Build context: the repository root.
#    --build-arg WHISPER_MODEL=base.en   (tiny.en | base.en | base.en-q5_1 | small.en-q5_1)
#    --build-arg PIPER_VOICES="en_US-hfc_female-medium en_US-hfc_male-medium"
#        (any voices from rhasspy/piper-voices; the first is the default, and
#         INTEL_PIPER_VOICE=/opt/voices/<name>.onnx picks another at runtime)
# =============================================================================

# ---- 1. echo-stream ------------------------------------------------------------------
FROM debian:bookworm-slim AS echo-build
ARG DEBIAN_FRONTEND=noninteractive
ARG WHISPER_MODEL=base.en
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake ninja-build git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY third_party/echo third_party/echo
COPY server/echo_stream server/echo_stream
# CPU tuning for whisper.cpp (ggml). x86-64: tune for the machine building it
# (server_setup.sh builds on the VPS itself). ARM64: native detection is
# unreliable under emulation/VMs, so target ARMv8.2 + fp16 + dot product:
# exactly Oracle's Ampere A1 (Neoverse-N1); also Apple Silicon and the Pi 5.
ARG TARGETARCH
ARG GGML_ARM_ARCH=armv8.2-a+fp16+dotprod
RUN if [ "$TARGETARCH" = "arm64" ]; then \
        cpu="-DGGML_NATIVE=OFF -DGGML_CPU_ARM_ARCH=$GGML_ARM_ARCH"; \
    else cpu="-DGGML_NATIVE=ON"; fi \
    && cmake -S server/echo_stream -B build -G Ninja -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/echo -DCMAKE_INSTALL_RPATH='$ORIGIN/../lib' $cpu \
    && cmake --build build --target echo-stream \
    && cmake --install build \
    && mkdir -p /opt/echo/lib \
    && cp -a build/onnxruntime/lib/libonnxruntime.so* /opt/echo/lib/ \
    && ldd /opt/echo/bin/echo-stream
RUN cd third_party/echo && ./scripts/download-models.sh --whisper "$WHISPER_MODEL" \
    && mkdir -p /opt/models \
    && cp models/silero_vad.onnx /opt/models/ \
    && cp "models/ggml-${WHISPER_MODEL}.bin" /opt/models/ \
    && ln -s "ggml-${WHISPER_MODEL}.bin" /opt/models/whisper.bin \
    # whisper.cpp's sample recording, for the speech-to-text self-test
    && cp /src/build/_deps/whisper-src/samples/jfk.wav /opt/models/jfk.wav

# ---- 2. Python environment + Piper voices ------------------------------------------------------
FROM debian:bookworm-slim AS py-build
ARG DEBIAN_FRONTEND=noninteractive
ARG PIPER_VOICES="en_US-hfc_female-medium en_US-hfc_male-medium"
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY server/voice/pyproject.toml /opt/voice/src/pyproject.toml
COPY server/voice/intelligence /opt/voice/src/intelligence
RUN python3 -m venv /opt/voice/venv \
    && /opt/voice/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/voice/venv/bin/pip install --no-cache-dir "/opt/voice/src[dev]"
RUN mkdir -p /opt/voices \
    && /opt/voice/venv/bin/python -m piper.download_voices $PIPER_VOICES --download-dir /opt/voices \
    && set -- $PIPER_VOICES \
    && ln -s "$1.onnx" /opt/voices/voice.onnx \
    && ln -s "$1.onnx.json" /opt/voices/voice.onnx.json

# ---- 3. Runtime ----------------------------------------------------------------------------------
FROM debian:bookworm-slim
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 ca-certificates tini tzdata libgomp1 openssl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1001 --user-group --shell /usr/sbin/nologin intel \
    && mkdir -p /data && chown intel:intel /data

COPY --from=echo-build /opt/echo /opt/echo
COPY --from=echo-build /opt/models /opt/models
COPY --from=py-build /opt/voice /opt/voice
COPY --from=py-build /opt/voices /opt/voices
COPY server/voice/tests /opt/voice/tests
COPY server/voice/pyproject.toml /opt/voice/pyproject.toml
COPY --chmod=755 deploy/entrypoint.sh /opt/voice/entrypoint.sh

ENV PATH=/opt/voice/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    INTEL_ECHO_STREAM_BIN=/opt/echo/bin/echo-stream \
    INTEL_WHISPER_MODEL=/opt/models/whisper.bin \
    INTEL_VAD_MODEL=/opt/models/silero_vad.onnx \
    INTEL_PIPER_VOICE=/opt/voices/voice.onnx \
    INTEL_DATA_DIR=/data \
    DISABLE_TELEMETRY=1 \
    DISABLE_AUTOUPDATER=1 \
    DISABLE_ERROR_REPORTING=1 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    INTEL_TEST_ECHO_BIN=/opt/echo/bin/echo-stream \
    INTEL_TEST_MODELS=/opt/models \
    INTEL_TEST_WHISPER=/opt/models/whisper.bin \
    INTEL_TEST_WAV=/opt/models/jfk.wav \
    INTEL_TEST_PIPER_VOICE=/opt/voices/voice.onnx

USER intel
WORKDIR /opt/voice
VOLUME ["/data"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD python3 -c "import socket; socket.create_connection(('127.0.0.1', 8765), 3)"

ENTRYPOINT ["/usr/bin/tini", "--", "/opt/voice/entrypoint.sh"]
CMD ["/opt/voice/venv/bin/python", "-m", "intelligence"]
