# Security

What protects your voice, your conversations, and your devices, layer by layer.

## Threat model

| Who | Can they… | Why not |
|---|---|---|
| Someone on the internet | reach the voice server? | Only UDP 51820 (WireGuard) is open. WireGuard does not answer packets that aren't from a known key, so the server doesn't even look like it's there. The voice port is bound to the tunnel IP **and** dropped by the host firewall unless it comes through `wg0`. |
| Someone on your Wi-Fi / your ISP / Oracle's network | listen to your audio or replies? | Everything between Pi and server is inside WireGuard (ChaCha20-Poly1305, Curve25519, plus a per-device pre-shared key), and inside that, TLS 1.3 with a private CA. Two independent layers of encryption. |
| Someone who steals the pairing text | pair a fake device? | The bundle is encrypted (AES-256, PBKDF2 with 600,000 iterations) with a one-time 80-bit pairing code shown separately, and authenticated with an HMAC. |
| A device you've lost | keep using the server? | `sudo ./server_setup.sh remove-device NAME` deletes its WireGuard peer and its certificate is no longer on the allow-list. |
| A malicious web page / email the AI reads | make the AI do something bad? | Content is marked as data in every prompt; the voice model can only use its own handful of tools; workers are told not to send, delete, buy or post without your spoken confirmation; each job has a spending cap. The worker runs in a separate container that has no access to your TLS keys, voice audio or memory database. |
| The AI worker itself (it can run any command) | break out to the server? | It runs as a non-root user in its own container with all Linux capabilities dropped and `no-new-privileges`, no published ports, memory and process limits. |

## Layers in detail

### 1. Network: WireGuard
- Server `10.77.0.1`, devices `10.77.0.2+`. Each device has its own key pair **and** a
  pre-shared key (an extra symmetric layer that also hedges against future quantum attacks).
- Devices route only `10.77.0.1/32` through the tunnel. Devices can't reach each other.
- `deploy/firewall.sh` (run at boot by `intelligence-firewall.service`) opens UDP 51820 and
  adds a `DOCKER-USER` rule that drops TCP 8765 unless it arrives on `wg0`.

### 2. Transport: mutual TLS 1.3
- Private CA (ECDSA P-256) created on the server; its key never leaves `/etc/intelligence/pki` (root, 0700).
- The Pi trusts **only** that CA (not the public certificate authorities) and checks the server's name.
- The server requires a client certificate from the same CA; the certificate's name must be on
  `INTEL_ALLOWED_DEVICES`. TLS 1.2 and older are refused. WebSocket compression is off (compression under encryption can leak information through message sizes).

### 3. Application
- A versioned `hello` is required within 5 seconds; frames are size-limited.
- The voice container and the agent container talk over a private Docker network with a
  256-bit bearer token, compared in constant time.
- The voice model runs with `permission_mode="dontAsk"` and an explicit tool list: it cannot run
  commands, read files, or use anything not listed.

### 4. Secrets on the server
| Secret | Where | Who can read it |
|---|---|---|
| Anthropic API key / token | `/etc/intelligence/secrets/anthropic_credential` | root + containers (group `intelligence`), mounted as a Docker secret, loaded into the process environment by the entrypoint only (not visible in `docker inspect`) |
| Container token | `/etc/intelligence/secrets/agent_token` | same |
| TLS server key | `/etc/intelligence/secrets/server.key` | same, mounted only into `voice` |
| CA key | `/etc/intelligence/pki/ca.key` | root only, never mounted |
| WireGuard keys | `/etc/wireguard`, `/etc/intelligence/wireguard` | root only |

### 5. Privacy
- The Pi only sends audio while its local voice detector hears speech; silence never leaves the house.
- Speech that doesn't start with the wake phrase (outside the 10 s follow-up window) is discarded
  in memory immediately: never stored, never sent to Claude, never logged.
- Transcripts are **not** written to logs unless you set `INTEL_LOG_TRANSCRIPTS=true`.
- The Agent SDK's chat history for the voice model lives on a RAM disk (`tmpfs`) and is gone on restart.
- What *is* stored: long-term memory (`/data/memory.db`, mode 0600 inside a Docker volume):
  facts you asked it to remember, one-to-three sentence summaries of past chats, and the task log.
  Worker files live in the `workspace` volume.
- Speech recognition (whisper.cpp) and speech synthesis (Piper) run on **your** server; only text
  goes to Anthropic's API.

### 6. Devices
- The client runs as an unprivileged user under systemd with `NoNewPrivileges`, an empty
  capability set, `ProtectSystem=strict`, and a 200 MB memory cap.
- The device key is readable only by that user.
- `local_setup.sh` turns on `ufw` (SSH still allowed) and automatic security updates.

## Things you should do
1. Use SSH keys on the VPS and the Pi, and disable password logins (`PasswordAuthentication no`).
   The setup scripts warn if passwords are enabled.
2. Keep the pairing code private; it's shown once by `server_setup.sh` (and again with `pairing`).
3. If a device is lost: `sudo ./server_setup.sh remove-device NAME`.
4. Rotate the Anthropic key if you ever suspect it leaked: `sudo ./server_setup.sh set-key`.
