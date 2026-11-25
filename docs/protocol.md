# Device protocol (v1)

One WebSocket per device: `wss://10.77.0.1:8765`, through WireGuard, mutual TLS 1.3.

## Binary frames
| Direction | Content |
|---|---|
| device → server | microphone audio: PCM s16le, 16 kHz, mono (the client sends 30 ms frames); max 64 KiB per frame |
| server → device | speech: PCM s16le, mono, at the `sample_rate` of the preceding `audio_start` |

## Text frames (JSON, `type` field)

### device → server
| type | fields | meaning |
|---|---|---|
| `hello` | `version`, `device` | must be first, within 5 s |
| `speech_start` | | local VAD opened; audio follows |
| `speech_end` | | local VAD closed; server feeds silence so the sentence ends |
| `playback_done` | `id` | finished playing audio stream `id`; starts the follow-up window |
| `ping` | | server answers `pong` |

### server → device
| type | fields | meaning |
|---|---|---|
| `ready` | `version`, `followup_seconds` | handshake accepted |
| `state` | `state` | `idle`, `listening`, `thinking`, `speaking` |
| `earcon` | `name` | play a local sound: `wake`, `ok`, `error` |
| `audio_start` | `id`, `sample_rate` | speech stream begins |
| `audio_end` | `id` | no more audio for this stream |
| `stop_audio` | | drop queued audio |
| `error` | `message` | |

## Timing

```
device                         server
  │ speech_start, audio… ───────►│
  │ speech_end ─────────────────►│  (silence filler → echo emits the sentence)
  │◄────────────── earcon ok, state thinking
  │◄────────────── state speaking, audio_start{id}
  │◄────────────── audio…  (streams while the model is still writing)
  │◄────────────── audio_end{id}
  │ playback_done{id} ──────────►│  follow-up window opens (10 s)
  │◄────────────── state listening
  │                              │  … 10 s + 3 s grace without speech …
  │◄────────────── state idle
```

The client keeps its microphone muted while anything is playing (and for 350 ms after), so
the assistant never hears itself.
