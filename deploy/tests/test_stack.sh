#!/usr/bin/env bash
# =============================================================================
#  Full-stack test: runs the real Docker Compose stack (voice + agent) with
#  throwaway keys, and talks to it like a Pi with synthesized speech.
#
#      deploy/tests/test_stack.sh [PYTHON]
#
#  PYTHON must have the client package installed (make venv does this).
#  The stack gets a deliberately INVALID Anthropic key: nothing is billed.
#  The test checks everything up to the Claude call: TLS, protocol, the VAD
#  gate, real speech-to-text, wake detection, the follow-up window, and that
#  the spoken apology for the failed Claude call comes back as audio.
# =============================================================================
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
PY=${1:-python3}
T=$(mktemp -d)
PROJECT=intelligence-test
INTEL_GID=$(id -g)
export INTEL_ETC=$T/etc INTEL_BIND_IP=127.0.0.1 INTEL_GID
COMPOSE=(docker compose -p "$PROJECT" --project-directory "$ROOT/deploy" -f "$ROOT/deploy/docker-compose.yml")

cleanup() {
    "${COMPOSE[@]}" logs --no-color > "$T/stack.log" 2>&1 || true
    "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
    [[ ${KEEP:-0} == 1 ]] && echo "logs kept in $T/stack.log" || rm -rf "$T"
}
trap cleanup EXIT

echo "==> throwaway keys and settings in $T"
mkdir -p "$INTEL_ETC/secrets"
(
    # shellcheck source=../lib/common.sh
    source "$ROOT/deploy/lib/common.sh"
    # shellcheck disable=SC2034  # read by pki.sh
    PKI_DIR=$T/pki
    # shellcheck source=../lib/pki.sh
    source "$ROOT/deploy/lib/pki.sh"
    pki_init_ca; pki_server_cert 10.77.0.1; pki_device_cert pi
)
cp "$T/pki/ca.crt" "$T/pki/server.crt" "$T/pki/server.key" "$INTEL_ETC/secrets/"
openssl rand -hex 32 > "$INTEL_ETC/secrets/agent_token"
printf 'sk-ant-api03-invalid-key-for-testing' > "$INTEL_ETC/secrets/anthropic_credential"
chmod 444 "$INTEL_ETC"/secrets/*  # test only: the real setup uses 0440 + a group
echo '{"mcpServers": {}}' > "$INTEL_ETC/mcp.json"
cat > "$INTEL_ETC/server.env" <<EOF
INTEL_ALLOWED_DEVICES=pi
INTEL_LOG_TRANSCRIPTS=true
INTEL_FOLLOWUP_SECONDS=10
TZ=UTC
EOF

echo "==> starting the stack"
"${COMPOSE[@]}" up -d --build --quiet-pull >/dev/null
for _ in $(seq 1 60); do
    [[ $("${COMPOSE[@]}" ps --format '{{.Health}}' | grep -c '^healthy$') -ge 2 ]] && break
    sleep 2
done
"${COMPOSE[@]}" ps --format '    {{.Service}}: {{.Health}}'

echo "==> synthesizing test speech with the image's own Piper"
synth() {  # TEXT OUT.pcm  (16 kHz s16le mono)
    docker run --rm -i --entrypoint python3 intelligence-voice:latest - "$1" > "$2" <<'EOF'
import sys
import numpy as np
from piper import PiperVoice
v = PiperVoice.load("/opt/voices/voice.onnx")
pcm = b"".join(c.audio_int16_bytes for c in v.synthesize(sys.argv[1]))
x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
n = int(len(x) * 16000 / v.config.sample_rate)
y = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
sys.stdout.buffer.write(np.concatenate([np.zeros(4800), y]).astype(np.int16).tobytes())
EOF
}
synth "Hey Claude, what time is it in Tokyo?" "$T/wake.pcm"
synth "And what about in Paris?" "$T/followup.pcm"

echo "==> talking to it like a Pi"
result=$(cd "$ROOT/client" && "$PY" "$HERE/stack_client.py" "wss://127.0.0.1:8765" "$T/pki" "$T/wake.pcm" "$T/followup.pcm")
echo "    $result"
"${COMPOSE[@]}" logs --no-color voice > "$T/voice.log" 2>&1

PASS=0 FAIL=0
check() { if eval "$2"; then PASS=$((PASS + 1)); echo "  ok   $1"; else FAIL=$((FAIL + 1)); echo "  FAIL $1"; fi; }
check "both containers healthy" '[[ $("${COMPOSE[@]}" ps --format "{{.Health}}" | grep -c "^healthy$") -ge 2 ]]'
check "device connected over mutual TLS" 'grep -q "\[pi\] connected" "$T/voice.log"'
check "real speech-to-text heard the wake phrase" 'grep -qiE "heard: (hey|a)?,? ?(claude|cloud|clod)" "$T/voice.log"'
check "the follow-up was accepted without the wake phrase" 'grep -qi "heard: and what about in paris" "$T/voice.log"'
check "Haiku was called (and refused the fake key)" 'grep -qiE "voice turn failed|invalid api key|authentication" "$T/voice.log"'
check "two replies came back as audio" '[[ $(echo "$result" | python3 -c "import json,sys; t=json.load(sys.stdin)[\"turns\"]; print(sum(1 for x in t if x[\"audio_bytes\"] > 10000))") == 2 ]]'
check "the device heard the ok earcon" 'echo "$result" | grep -q "\"ok\""'
check "the follow-up window was open after each reply" 'echo "$result" | grep -q "\"final_state\": \"listening\""'
check "the agent service is listening" '"${COMPOSE[@]}" logs agent 2>&1 | grep -q "agent service listening"'
check "the voice port is not reachable without a client certificate" \
    '! "$PY" -c "import ssl,socket; c=ssl.create_default_context(cafile=\"$T/pki/ca.crt\"); c.check_hostname=False; s=c.wrap_socket(socket.create_connection((\"127.0.0.1\",8765)),server_hostname=\"x\"); s.send(b\"GET / HTTP/1.1\r\n\r\n\"); assert s.recv(10)" 2>/dev/null'

echo
echo "$PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]] || { KEEP=1; exit 1; }
