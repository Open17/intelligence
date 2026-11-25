# syntax=docker/dockerfile:1.7
# =============================================================================
#  agent - the sandboxed "computer" where Sonnet and Opus do work
# =============================================================================
#  debian:bookworm-slim with a complete developer toolchain:
#    C/C++ (gcc, g++, clang, cmake, ninja, gdb), Python 3 (pip, venv),
#    Node.js LTS, Go, Rust, Java 17, git, sqlite3, ripgrep, jq, curl ...
#  Language runtimes come from official release archives, pinned and checked
#  against SHA-256 sums (no curl | sh).
#
#  Build context: the repository root.
# =============================================================================
FROM debian:bookworm-slim

ARG TARGETARCH
ARG DEBIAN_FRONTEND=noninteractive
ARG NODE_VERSION=26.13.1
ARG NODE_SHA256_ARM64=89e1853138db51e8b82d3e98ba9c0d3f2956a28e5e55ea1b7e12d25aae92d5dd
ARG NODE_SHA256_AMD64=5c063e46bab0fbc1c17cc445d7ff129cbdcd0471be6ed83b6ab864f8143bc864
ARG GO_VERSION=1.27.4
ARG GO_SHA256_ARM64=3392582988a273a87ab5043ee1b2c9c17a2c3c00580bf9326c47a2e0aaaeac92
ARG GO_SHA256_AMD64=7226736c2339719cda28fbf3a54d71aa63bcefe376e98cef00034268dab24840
ARG RUSTUP_VERSION=1.29.3
ARG RUSTUP_SHA256_ARM64=9f608c142e0e38a78957d0661ba1dfaf0b6fb37184cfaa4b9a0aa57af6184282
ARG RUSTUP_SHA256_AMD64=b1c8cb14e30889e7e2999e6b69e62d9e9b9ffc0f4df8180564d306d9680e6c55

# ---- System packages ----------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl wget gnupg git git-lfs openssh-client rsync \
        build-essential gcc g++ clang lld make cmake ninja-build pkg-config gdb autoconf automake libtool \
        python3 python3-pip python3-venv python3-dev pipx \
        openjdk-17-jdk-headless \
        sqlite3 ripgrep fd-find jq unzip zip xz-utils bzip2 less nano vim-tiny file tree \
        procps htop lsof iproute2 iputils-ping dnsutils netcat-openbsd \
        libssl-dev libffi-dev zlib1g-dev libsqlite3-dev libbz2-dev libreadline-dev \
        tini tzdata locales shellcheck \
    && sed -i 's/# en_US.UTF-8/en_US.UTF-8/' /etc/locale.gen && locale-gen \
    && ln -s /usr/bin/fdfind /usr/local/bin/fd \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8

# ---- Node.js (LTS) ------------------------------------------------------------------
RUN set -eux; \
    case "$TARGETARCH" in arm64) a=arm64; s=$NODE_SHA256_ARM64 ;; amd64) a=x64; s=$NODE_SHA256_AMD64 ;; *) exit 1 ;; esac; \
    curl -fsSLo /tmp/node.tar.xz "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${a}.tar.xz"; \
    echo "$s  /tmp/node.tar.xz" | sha256sum -c -; \
    tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 --exclude='*.md' --exclude=LICENSE; \
    rm /tmp/node.tar.xz; node --version; npm --version

# ---- Go ---------------------------------------------------------------------------------
RUN set -eux; \
    case "$TARGETARCH" in arm64) s=$GO_SHA256_ARM64 ;; amd64) s=$GO_SHA256_AMD64 ;; *) exit 1 ;; esac; \
    curl -fsSLo /tmp/go.tgz "https://go.dev/dl/go${GO_VERSION}.linux-${TARGETARCH}.tar.gz"; \
    echo "$s  /tmp/go.tgz" | sha256sum -c -; \
    tar -xzf /tmp/go.tgz -C /usr/local; rm /tmp/go.tgz; /usr/local/go/bin/go version

# ---- The agent user (non-root) ---------------------------------------------------------
RUN useradd --create-home --uid 1000 --shell /bin/bash agent \
    && mkdir -p /workspace /config /opt/agent && chown agent:agent /workspace /opt/agent

# ---- Rust (per user, via a checksum-verified rustup-init) ---------------------------------
USER agent
ENV CARGO_HOME=/opt/agent/cargo RUSTUP_HOME=/opt/agent/rustup
RUN set -eux; \
    case "$TARGETARCH" in arm64) t=aarch64-unknown-linux-gnu; s=$RUSTUP_SHA256_ARM64 ;; amd64) t=x86_64-unknown-linux-gnu; s=$RUSTUP_SHA256_AMD64 ;; *) exit 1 ;; esac; \
    curl -fsSLo /tmp/rustup-init "https://static.rust-lang.org/rustup/archive/${RUSTUP_VERSION}/${t}/rustup-init"; \
    echo "$s  /tmp/rustup-init" | sha256sum -c -; chmod +x /tmp/rustup-init; \
    /tmp/rustup-init -y --no-modify-path --profile minimal --default-toolchain stable -c clippy,rustfmt; \
    rm /tmp/rustup-init; /opt/agent/cargo/bin/cargo --version

# Toolchains for every shell the workers open, login shells included (Debian's
# /etc/profile resets PATH, so it goes in profile.d too). The service's own
# venv is deliberately NOT on PATH: a worker's `pip install` can't break it.
ENV PATH=/opt/agent/cargo/bin:/usr/local/go/bin:/home/agent/go/bin:/home/agent/.local/bin:/home/agent/.npm-global/bin:$PATH \
    NPM_CONFIG_PREFIX=/home/agent/.npm-global \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DISABLE_TELEMETRY=1 \
    DISABLE_AUTOUPDATER=1 \
    DISABLE_ERROR_REPORTING=1 \
    PYTHONUNBUFFERED=1

# ---- The agent service ---------------------------------------------------------------------
COPY --chown=agent:agent server/agent/pyproject.toml /opt/agent/src/pyproject.toml
COPY --chown=agent:agent server/agent/agent_service /opt/agent/src/agent_service
COPY --chmod=755 deploy/entrypoint.sh /opt/agent/entrypoint.sh
RUN python3 -m venv /opt/agent/venv \
    && /opt/agent/venv/bin/pip install --no-cache-dir /opt/agent/src

USER root
# The Agent SDK ships the Claude Code CLI; expose it as `claude` for
# scripts/connect.sh (signing in to connectors interactively).
RUN ln -s "$(/opt/agent/venv/bin/python -c 'import claude_agent_sdk, os; print(os.path.join(os.path.dirname(claude_agent_sdk.__file__), "_bundled", "claude"))')" /usr/local/bin/claude \
    && claude --version \
    && printf '%s\n' \
        'export PATH=/opt/agent/cargo/bin:/usr/local/go/bin:$HOME/go/bin:$HOME/.local/bin:$HOME/.npm-global/bin:$PATH' \
        'export CARGO_HOME=/opt/agent/cargo RUSTUP_HOME=/opt/agent/rustup NPM_CONFIG_PREFIX=$HOME/.npm-global' \
        > /etc/profile.d/00-intelligence-toolchains.sh
USER agent

WORKDIR /workspace
VOLUME ["/workspace", "/home/agent"]
EXPOSE 8800

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python3 -c "import socket; socket.create_connection(('127.0.0.1', 8800), 3)"

ENTRYPOINT ["/usr/bin/tini", "--", "/opt/agent/entrypoint.sh"]
CMD ["/opt/agent/venv/bin/python", "-m", "agent_service"]
