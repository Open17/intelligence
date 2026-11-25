# Architecture

```
 Raspberry Pi 5 (1 GB)                          Oracle VPS (Docker)
┌─────────────────────────┐                   ┌──────────────────────────────────────────────────────┐
│ intelligence-client     │   WireGuard       │ voice container                                      │
│                         │   + mTLS 1.3      │                                                      │
│ mic ─► WebRTC VAD gate ─┼──── 16 kHz PCM ──►│ echo-stream (VAD + whisper.cpp)                      │
│        (only speech)    │                   │    │ sentence                                        │
│                         │                   │    ▼                                                 │
│                         │                   │ wake phrase / 10 s follow-up window                  │
│                         │                   │    │ request                                         │
│                         │                   │    ▼                                                 │
│                         │                   │ Haiku 5.5 (medium) via Agent SDK ──tools──┐          │
│                         │                   │    │ streamed text            ask_expert  │          │
│                         │                   │    ▼                          start_task  │          │
│ speaker ◄─ player ◄─────┼── 22 kHz PCM ─────│ sentence chunker ─► Piper TTS             │          │
│  (+ local earcons)      │                   │                                           │          │
│                         │                   │ memory.db (facts, chat summaries, tasks)  │          │
└─────────────────────────┘                   └───────────────────────────────────────────┼──────────┘
                                                                     bearer token, private │ network
                                              ┌────────────────────────────────────────────▼─────────┐
                                              │ agent container ("the computer")                     │
                                              │ Sonnet 5.5 / Opus 5.5 via Agent SDK                  │
                                              │ Debian bookworm: C/C++, Python, Node, Go, Rust, Java │
                                              │ /workspace, connectors (MCP), spending caps          │
                                              └──────────────────────────────────────────────────────┘
```

## A request, step by step

1. **Pi**: WebRTC VAD hears speech → sends `speech_start`, 450 ms of pre-roll and the live audio.
   About 0.9 s after you stop it sends `speech_end` and stops sending.
2. **Server, STT**: `echo-stream` (C++, echo + whisper.cpp) transcribes. Real-time silence is fed
   after `speech_end` so echo's endpointing sees the pause. Transcription starts speculatively
   300 ms into the pause, so the text is usually ready the moment the 700 ms pause completes.
3. **Wake**: the sentence must start with "Hey Claude"/"Claude" (including whisper's usual
   mis-hearings such as "Cloud,") unless it started within 10 s of the end of the last reply.
4. **Haiku 5.5** answers through a pre-connected Agent SDK session, streaming tokens.
5. **Speech**: the first clause is cut as soon as it's long enough, synthesized by Piper and
   sent while Haiku is still writing; later sentences follow.
6. **Pi** plays it and reports `playback_done` → the 10 s follow-up window starts.

## Which model does what

| Situation | Model | Effort | How |
|---|---|---|---|
| Conversation, quick facts, simple questions, web lookups | Haiku 5.5 | medium | answers directly |
| Question needing care, or the computer/your accounts while you wait | Sonnet 5.5 | high | `ask_expert(medium)`: spoken directly |
| Genuinely hard question | Opus 5.5 | medium | `ask_expert(hard)` |
| Doing something (email, calendar, files, research) | Sonnet 5.5 | high | `start_task(action)`: background |
| Difficult multi-step job | Opus 5.5 | medium | `start_task(complex)` |
| **Building anything** (app, script, site, bot…) | **Opus 5.5** | **high** | `start_task(build)`, also enforced by a classifier in code |

When a background task finishes, its result is announced on the device you used last.

## Memory and the 5-minute reset

Every turn of a chat resends the whole chat, so long chats get expensive. After 5 minutes
without talking, the chat is closed: Haiku writes a one-to-three sentence summary into
`memory.db`, and a fresh session is connected **in advance** with the summary, the remembered
facts and the recent task log in its system prompt. The next "Hey Claude" uses that spare session
instantly; it knows what the last one knew, for a fraction of the tokens.

## Latency budget (typical)

| Stage | Time |
|---|---|
| End of speech → transcript (echo pause + speculative whisper) | ~0.7–0.9 s |
| Network (Pi ⇄ VPS, both ways) | 20–80 ms |
| Haiku first tokens (pre-connected session) | ~0.4–1 s |
| First clause → Piper → first audio | ~0.1–0.3 s |
| **You stop talking → you hear the answer** | **~1.5–2.5 s** |

Measured with `deploy/tests/test_stack.sh` (real containers, real speech, a model call that
returns at once): **1.4 s** from the end of speech to the first reply audio, including the Pi's
0.9 s voice-detector hang-over. A real Haiku reply adds its time to first token on top.

Knobs: `INTEL_STT_PAUSE_MS` (shorter pause, more cut-offs), `WHISPER_MODEL=tiny.en` at build time
(faster, less accurate), `INTEL_STT_AUDIO_CTX=768` (faster whisper on short sentences).

## Code map

| Path | What |
|---|---|
| `server/echo_stream/` | C++ bridge: stdin PCM → echo → JSON lines |
| `server/voice/intelligence/` | voice server: `gateway` (mTLS WebSocket), `conversation` (state machine), `stt`, `tts`, `wake`, `speech_text`, `memory`, `brain/` (Haiku session, routing, tasks) |
| `server/agent/agent_service/` | runs Sonnet/Opus jobs; `runner`, `server`, `prompts` |
| `client/intelligence_client/` | Pi client: `audio`, `vad`, `dsp`, `app` |
| `deploy/` | compose file, entrypoint, firewall, setup libraries (PKI, WireGuard) |
| `third_party/echo/` | the echo speech-to-text library (git subtree) |
