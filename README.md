# Intelligence

**A private, low-latency voice assistant for your Raspberry Pi, powered by Claude.**

Say *"Hey Claude"* (or just *"Claude"*) and talk. Your Pi streams your voice through an
encrypted tunnel to your own server, where it's transcribed, answered by Claude and turned back
into speech, which plays on the Pi's speaker. Ask it something hard and it hands the
question to a bigger model; ask it to *do* something and it works on it in the background and
tells you when it's done.

```
"Hey Claude, what's a good name for a sourdough starter?"
  → Haiku answers in about two seconds.

"Claude, build me a script that backs up my photos to a zip every night."
  → "I've got that running in the background. Anything else?"
  → (a few minutes later) "Your photo backup script is done. It's in projects/photo-backup..."
```

- **Wake phrase** "Hey Claude" / "Claude", then **10 seconds** to keep talking without it.
- **The right model for the job**: Haiku 5.5 for conversation, Sonnet 5.5 for actions,
  Opus 5.5 for hard problems, and **Opus 5.5 at high effort for anything it builds**.
- **Long-term memory**: remembers facts you tell it and what you talked about. After 5 quiet
  minutes it starts a fresh (cheaper) chat that keeps the same memory.
- **Runs on a 1 GB Pi**: the Pi only captures and plays audio (~40 MB RAM). Everything else
  runs on your server.
- **Private and locked down**: WireGuard + mutual TLS 1.3, speech recognition and synthesis on
  your own server, nothing stored unless it's memory. See [docs/security.md](docs/security.md).

## What you need

| | |
|---|---|
| Server | A VPS with Ubuntu 22.04/24.04 or Debian 12, **4 GB+ RAM** (Oracle Cloud's free *Ampere A1* is ideal: up to 4 cores / 24 GB) |
| Device | Raspberry Pi 5 (1 GB is plenty) with **Raspberry Pi OS 64-bit**, a USB microphone (or mic HAT) and a speaker |
| Claude | An Anthropic API key ([console.anthropic.com](https://console.anthropic.com)) or a Claude subscription token (`claude setup-token`) |

## Setup (two commands)

### 1. On the server

```bash
git clone <this repository> intelligence && cd intelligence
sudo ./server_setup.sh
```

It installs Docker and WireGuard, asks for your API key and a few preferences, creates all
keys and certificates, builds and starts the two containers (the first build takes 10 to 20
minutes), runs a self-test, and finally prints a **pairing block** and a **pairing code** for
your Pi.

**One manual step** in the Oracle Cloud console: *Networking → Virtual Cloud Networks → your VCN
→ Security Lists → Default → Add Ingress Rule*: source `0.0.0.0/0`, protocol **UDP**, port
**51820**. That's the only port you open.

### 2. On the Raspberry Pi

```bash
git clone <this repository> intelligence && cd intelligence
sudo ./local_setup.sh
```

Paste the pairing block, type the pairing code, pick your microphone and speaker (it suggests
USB devices and plays a test sound), and you're done. It starts automatically at boot.

> No git remote? Copy the folder over instead: `rsync -a intelligence/ user@host:intelligence/`.

## Using it

| Say | What happens |
|---|---|
| "Hey Claude" | a chime: it's listening; talk within 10 s |
| "Hey Claude, what's the weather in Paris?" | answers right away |
| "Claude, explain how mortgages work" | Haiku answers, or asks Sonnet if it needs care |
| "Claude, what's on my calendar tomorrow?" | Sonnet checks your connected calendar while you wait |
| "Claude, build me a website for my bakery" | Opus (high effort) builds it in the background and tells you when it's done |
| "Claude, remember that I take my coffee black" | saved to long-term memory |
| "What tasks are running?" / "Cancel that task" | manages background jobs |
| "Thanks, that's all" | stops listening right away |

## Managing it

On the **server**:

```bash
sudo ./server_setup.sh status            # containers + tunnel
sudo ./server_setup.sh logs              # live logs (transcripts are NOT logged by default)
sudo ./server_setup.sh add-device kitchen   # pair another Pi
sudo ./server_setup.sh remove-device pi  # revoke a lost device
sudo ./server_setup.sh connect           # sign in to connectors (Gmail, Calendar, Notion, ...) for Sonnet/Opus
sudo ./server_setup.sh set-key           # change the API key
sudo ./server_setup.sh                   # update after `git pull` (keeps all keys and data)
```

Settings (voice, models, effort levels, follow-up window, spending caps per job) are in
`/etc/intelligence/server.env`; apply changes with `sudo ./server_setup.sh restart`.
Setup asks for the voice: `en_US-hfc_female-medium` (the default) or `en_US-hfc_male-medium`;
both are in the image, so switching `INTEL_PIPER_VOICE` only needs a restart.

On the **Pi**:

```bash
sudo ./local_setup.sh status | logs | restart
sudo ./local_setup.sh --audio            # pick a different speaker / microphone
```

### Connectors (OAuth)

Sonnet and Opus can use your accounts through MCP connectors. Run
`sudo ./server_setup.sh connect`, which opens Claude Code inside the agent container, then type
`/mcp` to add a server and sign in. Logins are saved in the container's home volume and used by
every future task. Servers that use a static token can also go in `/etc/intelligence/mcp.json`
(see [deploy/mcp.example.json](deploy/mcp.example.json)).

### The workers' computer

Sonnet and Opus work inside the `agent` container: Debian bookworm-slim with git, gcc/g++/clang,
CMake, Ninja, Python 3 (pip, venv), Node.js LTS, Go, Rust, Java 17, sqlite3, ripgrep, jq and more.
Projects go in `/workspace/projects/` (a persistent volume). To look around:
`sudo docker exec -it intelligence-agent-1 bash`.

## How it works

[docs/architecture.md](docs/architecture.md) has the full picture, the model routing table and
the latency budget. [docs/protocol.md](docs/protocol.md) describes the device protocol.

| Part | Language | Why |
|---|---|---|
| Speech-to-text | C++ ([echo](third_party/echo): Silero VAD + whisper.cpp) | lowest latency on a CPU |
| Voice server, agent service | Python (asyncio, Claude Agent SDK) | the Agent SDK's home, async streaming |
| Text-to-speech | Piper (ONNX) | fast and natural on a CPU, fully local |
| Pi client | Python + PortAudio + WebRTC VAD | ~40 MB RAM, easy to install |
| Setup | Bash | runs on a fresh server or Pi with nothing installed |

## Development

```bash
make test          # server + client unit and integration tests
make lint          # shellcheck (and ruff if installed)
make docker        # build both images
make test-docker   # run the test suite inside the voice image (real echo + Piper)
```

Branching and commit conventions: [CONTRIBUTING.md](CONTRIBUTING.md).

## Costs

Haiku handles most turns and is very cheap; Sonnet and Opus only run when needed. Every job
has a spending cap (`AGENT_BUDGET_*_USD`, by default $1 per question, $5 per task, $15 per build),
so a runaway task stops instead of running up a bill.
