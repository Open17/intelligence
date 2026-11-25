#!/usr/bin/env bash
# =============================================================================
#  server_setup.sh - install / update the Intelligence server on your VPS
# =============================================================================
#
#    sudo ./server_setup.sh                 install or update everything
#    sudo ./server_setup.sh add-device NAME pair another device (e.g. kitchen)
#    sudo ./server_setup.sh pairing [NAME]  show a device's pairing bundle again
#    sudo ./server_setup.sh remove-device NAME
#    sudo ./server_setup.sh connect         sign in to connectors (Gmail, ...) for Sonnet/Opus
#    sudo ./server_setup.sh set-key         change the Anthropic API key / token
#    sudo ./server_setup.sh status | logs | restart | stop
#
#  Tested on Ubuntu 22.04/24.04 and Debian 12 (x86-64 and ARM64, e.g. Oracle
#  Cloud Ampere A1). Safe to run again: existing keys and data are kept.
# =============================================================================
set -euo pipefail

REPO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=deploy/lib/common.sh
source "$REPO_DIR/deploy/lib/common.sh"
# shellcheck source=deploy/lib/pki.sh
source "$REPO_DIR/deploy/lib/pki.sh"
# shellcheck source=deploy/lib/wireguard.sh
source "$REPO_DIR/deploy/lib/wireguard.sh"
# shellcheck source=deploy/lib/pairing.sh
source "$REPO_DIR/deploy/lib/pairing.sh"

ETC=/etc/intelligence
SECRETS=$ETC/secrets
PAIRING=$ETC/pairing
INTEL_GID=1500
COMPOSE=(docker compose --project-directory "$REPO_DIR/deploy" -f "$REPO_DIR/deploy/docker-compose.yml")

# =============================================================================
#  Steps
# =============================================================================
check_system() {
    step "Checking the system"
    [[ -r /etc/os-release ]] || die "unsupported OS (no /etc/os-release)"
    # shellcheck disable=SC1091
    source /etc/os-release
    case "${ID:-}:${ID_LIKE:-}" in
        ubuntu:*|debian:*|*:*debian*) ok "$PRETTY_NAME" ;;
        *) die "this script supports Ubuntu and Debian (found ${PRETTY_NAME:-unknown}). Oracle Cloud: pick an Ubuntu image." ;;
    esac
    case "$(uname -m)" in
        x86_64|aarch64) ok "architecture $(uname -m)" ;;
        *) die "unsupported CPU architecture $(uname -m)" ;;
    esac
    local mem_mb; mem_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
    if (( mem_mb < 3500 )); then
        warn "only ${mem_mb} MB RAM: 4 GB+ is recommended (Oracle's free Ampere A1 has up to 24 GB)"
    else
        ok "${mem_mb} MB RAM"
    fi
}

install_packages() {
    step "Installing system packages (Docker, WireGuard, security updates)"
    apt-get update -qq
    apt_install ca-certificates curl gnupg openssl iptables wireguard-tools \
        unattended-upgrades fail2ban python3
    ok "base packages"

    if ! have docker || ! docker compose version >/dev/null 2>&1; then
        # Docker's official repository (signed), not the distro's older package.
        # shellcheck disable=SC1091
        source /etc/os-release
        install -m 0755 -d /etc/apt/keyrings
        curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
        chmod a+r /etc/apt/keyrings/docker.asc
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$ID ${VERSION_CODENAME} stable" \
            > /etc/apt/sources.list.d/docker.list
        apt-get update -qq
        apt_install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    fi
    systemctl enable --now docker >/dev/null 2>&1
    ok "$(docker --version)"

    # Automatic security updates and SSH brute-force protection.
    dpkg-reconfigure -f noninteractive unattended-upgrades >/dev/null 2>&1 || true
    systemctl enable --now fail2ban >/dev/null 2>&1 || true
    ok "automatic security updates + fail2ban"
}

configure() {
    step "Configuration"
    install -d -m 755 "$ETC"
    getent group intelligence >/dev/null || groupadd --system --gid "$INTEL_GID" intelligence
    install -d -m 750 -o root -g intelligence "$SECRETS"
    install -d -m 700 "$PAIRING"

    # ---- Anthropic credential ----------------------------------------------------
    if [[ ! -s $SECRETS/anthropic_credential || ${FORCE_KEY:-0} == 1 ]]; then
        info "Claude needs an Anthropic API key (console.anthropic.com -> API Keys),"
        info "or a Claude subscription token from 'claude setup-token' (starts sk-ant-oat)."
        local key=${ANTHROPIC_API_KEY:-${CLAUDE_CODE_OAUTH_TOKEN:-}}
        ask_secret key "Paste it here (hidden)"
        key=$(printf '%s' "$key" | tr -d ' \r\n')
        [[ $key == sk-ant-* ]] || die "that doesn't look like an Anthropic key (should start with sk-ant-)"
        printf '%s' "$key" | write_file "$SECRETS/anthropic_credential" 440 root:intelligence
        unset key
        ok "credential saved (readable only by root and the containers)"
    else
        ok "Anthropic credential already set (change it: sudo $0 set-key)"
    fi

    # ---- Internal token between the two containers ------------------------------------
    if [[ ! -s $SECRETS/agent_token ]]; then
        openssl rand -hex 32 | write_file "$SECRETS/agent_token" 440 root:intelligence
    fi

    # ---- Settings (not secret) ----------------------------------------------------------
    if [[ ! -f $ETC/server.env ]]; then
        local name user tz endpoint
        ask name "What should the assistant be called" "Claude"
        ask user "Your first name (optional, so it can greet you)" ""
        tz=$(timedatectl show -p Timezone --value 2>/dev/null || echo UTC)
        ask tz "Your time zone" "$tz"
        endpoint=$(curl -4 -fsS --max-time 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')
        ask endpoint "This server's public IP or hostname (devices connect to it)" "$endpoint"
        local voice
        ask voice "Voice: 1 = female, 2 = male" "1"
        case ${voice,,} in
            2|m|male)   voice=en_US-hfc_male-medium ;;
            1|f|female) voice=en_US-hfc_female-medium ;;
            *) warn "unknown voice '$voice': using the female voice"; voice=en_US-hfc_female-medium ;;
        esac
        write_file "$ETC/server.env" 644 <<EOF
# Intelligence server settings. Apply changes with: sudo ./server_setup.sh restart
INTEL_ASSISTANT_NAME=$name
INTEL_USER_NAME=$user
TZ=$tz
INTEL_PUBLIC_ENDPOINT=$endpoint

# Voice: en_US-hfc_female-medium (default) or en_US-hfc_male-medium
INTEL_PIPER_VOICE=/opt/voices/$voice.onnx

# Models (see README: "Which model does what")
INTEL_VOICE_MODEL=claude-haiku-5-5
INTEL_VOICE_EFFORT=medium
INTEL_ACTION_MODEL=claude-sonnet-5-5
INTEL_ACTION_EFFORT=high
INTEL_HARD_MODEL=claude-opus-5-5
INTEL_HARD_EFFORT=medium
INTEL_BUILD_MODEL=claude-opus-5-5
INTEL_BUILD_EFFORT=high

# Conversation
INTEL_FOLLOWUP_SECONDS=10
INTEL_SESSION_IDLE_SECONDS=300

# Spending caps per job in USD (a runaway job stops instead of burning money)
AGENT_BUDGET_QUESTION_USD=1
AGENT_BUDGET_TASK_USD=5
AGENT_BUDGET_BUILD_USD=15

# Devices allowed to connect (certificate names, comma separated; managed by add/remove-device)
INTEL_ALLOWED_DEVICES=

# Privacy: set to true to write what you say into the server logs (debugging only)
INTEL_LOG_TRANSCRIPTS=false
INTEL_LOG_LEVEL=INFO
EOF
        ok "settings in $ETC/server.env"
    else
        ok "settings already in $ETC/server.env"
    fi
    [[ -f $ETC/mcp.json ]] || echo '{"mcpServers": {}}' | write_file "$ETC/mcp.json" 640 root:intelligence
}

setup_crypto() {
    step "Keys and certificates"
    pki_init_ca
    pki_server_cert "$WG_SERVER_IP"
    install -m 440 -o root -g intelligence "$PKI_DIR/ca.crt" "$SECRETS/ca.crt"
    install -m 440 -o root -g intelligence "$PKI_DIR/server.crt" "$SECRETS/server.crt"
    install -m 440 -o root -g intelligence "$PKI_DIR/server.key" "$SECRETS/server.key"
    ok "private CA and server certificate (ECDSA P-256, TLS 1.3 only)"
    wg_init_server
    ok "WireGuard server key"
}

setup_firewall() {
    step "Firewall"
    install -m 755 "$REPO_DIR/deploy/firewall.sh" /usr/local/sbin/intelligence-firewall
    write_file /etc/systemd/system/intelligence-firewall.service 644 <<'EOF'
[Unit]
Description=Intelligence firewall rules (WireGuard in, voice port tunnel-only)
After=docker.service wg-quick@wg0.service
Wants=docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/intelligence-firewall
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
    # Docker binds the voice port to the tunnel IP, so the tunnel must exist first.
    install -d /etc/systemd/system/docker.service.d
    write_file /etc/systemd/system/docker.service.d/intelligence.conf 644 <<'EOF'
[Unit]
Wants=wg-quick@wg0.service
After=wg-quick@wg0.service
EOF
    systemctl daemon-reload
    wg_reload
    systemctl enable intelligence-firewall >/dev/null 2>&1
    systemctl restart intelligence-firewall
    ok "UDP $WG_PORT open; voice port only reachable through the tunnel"

    if sshd -T 2>/dev/null | grep -qi '^passwordauthentication yes'; then
        warn "SSH accepts passwords. Use SSH keys and set 'PasswordAuthentication no' in /etc/ssh/sshd_config."
    fi
}

allowed_devices() {  # updates INTEL_ALLOWED_DEVICES from the paired devices
    local names
    names=$(find "$WG_PEERS_DIR" -name '*.peer' -exec basename {} .peer \; 2>/dev/null | sort | paste -sd, -)
    sed -i "s/^INTEL_ALLOWED_DEVICES=.*/INTEL_ALLOWED_DEVICES=$names/" "$ETC/server.env"
}

pair_device() {  # NAME
    local name=$1 endpoint ip code tmp
    [[ $name =~ ^[a-z0-9][a-z0-9-]{0,30}$ ]] || die "device names use lowercase letters, digits and dashes"
    endpoint=$(grep '^INTEL_PUBLIC_ENDPOINT=' "$ETC/server.env" | cut -d= -f2)
    ip=$(wg_add_peer "$name")
    pki_device_cert "$name"
    allowed_devices
    wg_reload

    tmp=$(mktemp -d); chmod 700 "$tmp"
    wg_client_config "$name" "$endpoint" > "$tmp/intelligence.conf"
    cp "$PKI_DIR/ca.crt" "$tmp/ca.crt"
    cp "$PKI_DIR/device-$name.crt" "$tmp/device.crt"
    cp "$PKI_DIR/device-$name.key" "$tmp/device.key"
    cat > "$tmp/client.env" <<EOF
INTEL_SERVER_URL=wss://$WG_SERVER_IP:8765
INTEL_SERVER_NAME=$SERVER_NAME
INTEL_DEVICE_NAME=$name
EOF
    # Encrypt with a one-time pairing code: the bundle text alone is useless.
    code=$(pairing_new_code)
    pairing_pack "$tmp" "$name" "$code" | write_file "$PAIRING/$name.txt" 600
    printf '%s\n' "$code" | write_file "$PAIRING/$name.code" 600
    rm -rf "$tmp"
    ok "device '$name' paired (tunnel address $ip)"
}

show_pairing() {  # NAME
    local name=$1
    [[ -f $PAIRING/$name.txt ]] || die "no device called '$name' (sudo $0 add-device $name)"
    echo
    echo "${BOLD}Pair '$name': on the Raspberry Pi run${RESET}"
    echo
    echo "    git clone <this repository> intelligence && cd intelligence && sudo ./local_setup.sh"
    echo
    echo "and paste this block when asked (or copy the file $PAIRING/$name.txt to the Pi"
    echo "and run: sudo ./local_setup.sh --bundle $name.txt):"
    echo
    cat "$PAIRING/$name.txt"
    echo
    echo "${BOLD}Pairing code:${RESET} ${GREEN}$(cat "$PAIRING/$name.code")${RESET}   (it will ask for this; keep it private)"
    echo
}

build_and_start() {
    step "Building and starting the containers (the first build takes 10-20 minutes)"
    export INTEL_GID
    "${COMPOSE[@]}" build --pull
    "${COMPOSE[@]}" up -d --remove-orphans
    info "waiting for the services to become healthy..."
    for _ in $(seq 1 60); do
        if [[ $("${COMPOSE[@]}" ps --format '{{.Health}}' | grep -c healthy) -ge 2 ]]; then
            ok "voice and agent containers are healthy"; return
        fi
        sleep 5
    done
    warn "the services are not healthy yet; check: sudo $0 logs"
}

self_test() {
    step "Self-test"
    if "${COMPOSE[@]}" exec -T voice python3 -m pytest -q /opt/voice/tests -x -p no:cacheprovider \
        >/tmp/intelligence-selftest.log 2>&1; then
        ok "speech-to-text, text-to-speech, wake phrase and protocol tests pass"
    else
        warn "self-test failed; see /tmp/intelligence-selftest.log"
    fi
}

print_next_steps() {
    echo
    echo "${GREEN}${BOLD}The server is ready.${RESET}"
    echo
    echo "${BOLD}One manual step in the Oracle Cloud console${RESET} (the VPS firewall above it):"
    echo "  Networking -> Virtual Cloud Networks -> your VCN -> Security Lists -> Default"
    echo "  -> Add Ingress Rule: Source 0.0.0.0/0, IP Protocol UDP, Destination Port $WG_PORT"
    echo "  (No other ports need opening. Never open 8765.)"
    show_pairing pi
}

# =============================================================================
#  Commands
# =============================================================================
cmd_install() {
    require_root
    check_system
    install_packages
    configure
    setup_crypto
    [[ -f $WG_PEERS_DIR/pi.peer ]] || pair_device pi
    allowed_devices
    setup_firewall
    build_and_start
    self_test
    print_next_steps
}

main() {
    local cmd=${1:-install}
    case "$cmd" in
        install|update) cmd_install ;;
        add-device)
            require_root; [[ -n ${2:-} ]] || die "usage: $0 add-device NAME"
            pair_device "$2"; "${COMPOSE[@]}" up -d voice >/dev/null; show_pairing "$2" ;;
        remove-device)
            require_root; [[ -n ${2:-} ]] || die "usage: $0 remove-device NAME"
            wg_remove_peer "$2"; rm -f "$PKI_DIR/device-$2".* "$PAIRING/$2".*
            allowed_devices; wg_reload; "${COMPOSE[@]}" up -d --force-recreate voice >/dev/null
            ok "device '$2' removed: its tunnel key and certificate no longer work" ;;
        pairing) require_root; show_pairing "${2:-pi}" ;;
        connect)
            require_root
            info "Starting Claude Code inside the agent container. Type /mcp to add and sign in"
            info "to connectors (they are saved for Sonnet/Opus). Exit with /exit or Ctrl-D."
            "${COMPOSE[@]}" exec -it agent /opt/agent/entrypoint.sh claude ;;
        set-key) require_root; FORCE_KEY=1 configure; "${COMPOSE[@]}" up -d --force-recreate ;;
        status) "${COMPOSE[@]}" ps; wg show 2>/dev/null | grep -E 'peer|handshake|transfer' || true ;;
        logs) "${COMPOSE[@]}" logs -f --tail=100 "${@:2}" ;;
        restart) require_root; "${COMPOSE[@]}" up -d --force-recreate ;;
        stop) require_root; "${COMPOSE[@]}" down ;;
        -h|--help|help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//' ;;
        *) die "unknown command '$cmd' (try: $0 help)" ;;
    esac
}

main "$@"
