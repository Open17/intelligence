# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed
- **Voice**: the default text-to-speech voice is now Piper's `en_US-hfc_female-medium`
  (was `en_US-lessac-medium`), with `en_US-hfc_male-medium` as the second choice. Both ship
  in the image; `server_setup.sh` asks which one on first install (`INTEL_PIPER_VOICE` in
  `server.env`). The build argument is now `PIPER_VOICES` (a list; the first is the default).

### Fixed
- **Pi setup**: `local_setup.sh` stopped silently right after "installed in
  /opt/intelligence-client" on every fresh install (no saved audio settings yet), so the
  client service was never created. It now carries on to the speaker/microphone step.

## [0.1.0] - 2026-11-24

First release.

### Added
- **Voice server** (`server/voice`): mutual-TLS 1.3 WebSocket gateway; per-device
  conversation flow with the wake phrase ("Hey Claude" / "Claude", including whisper's
  common mis-hearings) and a 10 s follow-up window that starts when the device has finished
  playing the reply; streamed replies (text → sentence chunker → Piper → device).
- **Speech-to-text** with echo (Silero VAD + whisper.cpp) through `echo-stream`, a C++
  stdin/stdout bridge; real-time silence filling so sentences end when the device stops sending.
- **Brain**: Haiku 5.5 (medium) through the Claude Agent SDK with tools to hand work to
  Sonnet 5.5 (high) and Opus 5.5 (medium), and builds always to Opus 5.5 (high); background
  tasks announced when they finish; long-term memory (facts, chat summaries, task log); a fresh
  chat after 5 idle minutes that keeps the same memory; a pre-connected spare chat for instant wake.
- **Agent service** (`server/agent`): Sonnet/Opus jobs with the Agent SDK in a sandboxed
  Debian bookworm container with C/C++, Python, Node.js, Go, Rust and Java; connectors via MCP;
  per-job spending caps and turn limits; cancellation.
- **Raspberry Pi client** (`client`): ~40 MB, WebRTC VAD gate, always-open audio streams,
  resampling, half-duplex echo guard, local earcons, `--list-devices`, `--test-audio`.
- **Setup**: `server_setup.sh` and `local_setup.sh`; private CA, WireGuard tunnel with
  per-device keys and pre-shared keys, encrypted pairing bundles with a one-time code, firewall
  rules, hardened containers and systemd service.
- Documentation: architecture, security model, device protocol.
