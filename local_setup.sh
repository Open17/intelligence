#!/usr/bin/env bash
# =============================================================================
#  local_setup.sh - set up a Raspberry Pi as an Intelligence voice device
# =============================================================================
#
#    sudo ./local_setup.sh                       pair and install (asks for the bundle)
#    sudo ./local_setup.sh --bundle pi.txt       ... using a bundle file
#    sudo ./local_setup.sh --audio               choose speaker / microphone again
#    sudo ./local_setup.sh status | logs | restart | uninstall
#
#  Needs Raspberry Pi OS (64-bit, Bookworm or newer), a microphone and a
#  speaker (USB, HAT or 3.5 mm), and the pairing bundle + code that
#  server_setup.sh printed. Uses about 40 MB of RAM.
# =============================================================================
set -euo pipefail

REPO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=deploy/lib/common.sh
source "$REPO_DIR/deploy/lib/common.sh"
# shellcheck source=deploy/lib/pairing.sh
source "$REPO_DIR/deploy/lib/pairing.sh"

APP=/opt/intelligence-client
ETC=/etc/intelligence
UNIT=intelligence-client
WG_IF=intelligence
SERVER_IP=10.77.0.1
BUNDLE_FILE=""

# =============================================================================
check_system() {
    step "Checking the system"
    [[ $(uname -m) == aarch64 || $(uname -m) == x86_64 ]] \
        || die "needs a 64-bit OS (found $(uname -m)); install Raspberry Pi OS (64-bit)"
    # shellcheck disable=SC1091
    source /etc/os-release
    ok "${PRETTY_NAME:-Linux} ($(uname -m))"
    local mem; mem=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
    ok "${mem} MB RAM (the client needs about 40 MB)"
}

install_packages() {
    step "Installing packages"
    apt-get update -qq
    apt_install python3 python3-venv python3-dev build-essential libportaudio2 alsa-utils \
        wireguard-tools openssl ca-certificates iputils-ping unattended-upgrades
    dpkg-reconfigure -f noninteractive unattended-upgrades >/dev/null 2>&1 || true
    ok "Python, PortAudio, WireGuard, automatic security updates"
}

read_bundle() {
    step "Pairing with your server"
    local text
    if [[ -n $BUNDLE_FILE ]]; then
        [[ -f $BUNDLE_FILE ]] || die "bundle file not found: $BUNDLE_FILE"
        text=$(cat "$BUNDLE_FILE")
    else
        info "On the server, 'sudo ./server_setup.sh pairing' shows the bundle and code."
        info "Paste the whole block (BEGIN to END lines), then press Enter:"
        text=""
        local line
        while IFS= read -r line; do
            text+="$line"$'\n'
            [[ $line == *"-----END INTELLIGENCE PAIRING-----"* ]] && break
        done
    fi
    grep -q "BEGIN INTELLIGENCE PAIRING" <<<"$text" || die "that is not a pairing bundle"

    local code=${PAIRING_CODE:-}
    ask_secret code "Pairing code (XXXX-XXXX-XXXX-XXXX)"

    WORK=$(mktemp -d); chmod 700 "$WORK"
    trap 'rm -rf "$WORK"' EXIT
    printf '%s\n' "$text" > "$WORK/bundle.txt"
    pairing_unpack "$WORK/bundle.txt" "$code" "$WORK/files" || die "pairing failed"
    for f in intelligence.conf ca.crt device.crt device.key client.env; do
        [[ -f $WORK/files/$f ]] || die "bundle is missing $f"
    done
    ok "bundle verified and decrypted"
}

setup_tunnel() {
    step "Encrypted tunnel (WireGuard)"
    install -d -m 700 /etc/wireguard
    install -m 600 "$WORK/files/intelligence.conf" "/etc/wireguard/$WG_IF.conf"
    systemctl enable "wg-quick@$WG_IF" >/dev/null 2>&1
    systemctl restart "wg-quick@$WG_IF"
    for _ in 1 2 3 4 5 6; do
        if ping -c1 -W2 "$SERVER_IP" >/dev/null 2>&1; then ok "tunnel to the server is up"; return; fi
        sleep 2
    done
    warn "can't reach the server through the tunnel yet."
    warn "Check that UDP 51820 is open in the Oracle Cloud security list, then: sudo ./local_setup.sh restart"
}

# Desktop images run PipeWire per user; Lite images use ALSA directly.
detect_audio_user() {
    RUN_USER=intelligence
    if pgrep -x pipewire >/dev/null 2>&1 || pgrep -x pulseaudio >/dev/null 2>&1; then
        local desktop_user=${SUDO_USER:-}
        if [[ -n $desktop_user && $desktop_user != root ]]; then
            RUN_USER=$desktop_user
            info "desktop audio (PipeWire) detected: running as '$RUN_USER' so it can use the sound server"
        fi
    fi
    if [[ $RUN_USER == intelligence ]]; then
        id intelligence >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin \
            --groups audio intelligence
    fi
}

install_app() {
    step "Installing the client"
    install -d -m 755 "$APP"
    rm -rf "$APP/src"; cp -r "$REPO_DIR/client" "$APP/src"
    [[ -d $APP/venv ]] || python3 -m venv "$APP/venv"
    "$APP/venv/bin/pip" install --quiet --upgrade pip
    "$APP/venv/bin/pip" install --quiet "$APP/src"
    ok "installed in $APP"

    install -d -m 750 -o root -g "$(id -gn "$RUN_USER")" "$ETC"
    if [[ -n ${WORK:-} ]]; then
        install -m 444 "$WORK/files/ca.crt" "$ETC/ca.crt"
        install -m 444 "$WORK/files/device.crt" "$ETC/device.crt"
        install -m 400 -o "$RUN_USER" "$WORK/files/device.key" "$ETC/device.key"
        local keep=""
        [[ -f $ETC/client.env ]] && keep=$(grep -E '^INTEL_(INPUT|OUTPUT)_DEVICE|^INTEL_VOLUME' "$ETC/client.env" || true)
        { cat "$WORK/files/client.env"; if [[ -n $keep ]]; then echo "$keep"; fi; } \
            | write_file "$ETC/client.env" 640 "root:$(id -gn "$RUN_USER")"
    fi
    ok "certificates in $ETC (device key readable only by '$RUN_USER')"
}

choose_audio() {
    step "Speaker and microphone"
    local list
    list=$(sudo -u "$RUN_USER" env ${XDG_ENV:-} "$APP/venv/bin/python" -m intelligence_client --list-devices 2>/dev/null || true)
    echo "$list" | sed 's/^/    /'
    local usb_in usb_out input output
    usb_in=$(echo "$list" | grep -iE 'usb|mic' | grep -E '[1-9][0-9]* in' | head -1 | sed -E 's/^[ *>]*([0-9]+).*/\1/' || true)
    usb_out=$(echo "$list" | grep -iE 'usb|speaker|headphones|hifiberry|dac' | grep -E '[1-9][0-9]* out' | head -1 | sed -E 's/^[ *>]*([0-9]+).*/\1/' || true)
    if [[ $RUN_USER != intelligence ]]; then
        usb_in=""; usb_out=""  # desktop: PipeWire owns the hardware; use the system default
        info "Tip: on the desktop, pick the speaker/mic in the volume menu and press Enter here."
    fi
    input=${INPUT_DEVICE:-}; output=${OUTPUT_DEVICE:-}
    ask input "Microphone: number or part of the name (Enter = ${usb_in:-system default})" "${usb_in:-}"
    ask output "Speaker: number or part of the name (Enter = ${usb_out:-system default})" "${usb_out:-}"
    sed -i '/^INTEL_INPUT_DEVICE=/d; /^INTEL_OUTPUT_DEVICE=/d' "$ETC/client.env"
    printf 'INTEL_INPUT_DEVICE=%s\nINTEL_OUTPUT_DEVICE=%s\n' "$input" "$output" >> "$ETC/client.env"
    if confirm "Play a test sound and record 3 seconds to check them?" y; then
        sudo -u "$RUN_USER" env ${XDG_ENV:-} "$APP/venv/bin/python" -m intelligence_client --test-audio || \
            warn "audio test failed; run 'sudo ./local_setup.sh --audio' to pick other devices"
    fi
}

install_service() {
    step "Starting the service"
    local uid; uid=$(id -u "$RUN_USER")
    local env_line=""
    if [[ $RUN_USER != intelligence ]]; then
        env_line="Environment=XDG_RUNTIME_DIR=/run/user/$uid"
        loginctl enable-linger "$RUN_USER" >/dev/null 2>&1 || true  # sound server runs at boot
    fi
    write_file "/etc/systemd/system/$UNIT.service" 644 <<EOF
[Unit]
Description=Intelligence voice client
After=network-online.target wg-quick@$WG_IF.service sound.target
Wants=network-online.target wg-quick@$WG_IF.service

[Service]
User=$RUN_USER
SupplementaryGroups=audio
$env_line
ExecStart=$APP/venv/bin/python -m intelligence_client --config $ETC/client.env
Restart=always
RestartSec=3
Nice=-5
MemoryMax=200M

# Hardening: the client can only talk to the network and the sound card.
NoNewPrivileges=yes
CapabilityBoundingSet=
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
RestrictNamespaces=yes
RestrictRealtime=no
RestrictSUIDSGID=yes
LockPersonality=yes
SystemCallArchitectures=native
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable "$UNIT" >/dev/null 2>&1
    systemctl restart "$UNIT"
    sleep 4
    if journalctl -u "$UNIT" --since "-10s" --no-pager 2>/dev/null | grep -q "connected to"; then
        ok "connected to the server"
    elif systemctl is-active --quiet "$UNIT"; then
        ok "service running (connecting...). Watch it with: sudo ./local_setup.sh logs"
    else
        warn "the service did not start; see: sudo ./local_setup.sh logs"
    fi
}

harden_pi() {
    step "Securing this Pi"
    if have ufw || confirm "Enable a firewall (allow SSH, block other incoming connections)?" y; then
        have ufw || apt_install ufw
        ufw allow OpenSSH >/dev/null 2>&1 || ufw allow 22/tcp >/dev/null
        ufw default deny incoming >/dev/null
        ufw default allow outgoing >/dev/null
        ufw --force enable >/dev/null
        ok "firewall on (SSH still allowed)"
    fi
    if sshd -T 2>/dev/null | grep -qi '^passwordauthentication yes'; then
        warn "SSH accepts passwords: use SSH keys and set 'PasswordAuthentication no'."
    fi
}

set_xdg_env() {
    XDG_ENV=""
    [[ $RUN_USER != intelligence ]] && XDG_ENV="XDG_RUNTIME_DIR=/run/user/$(id -u "$RUN_USER")"
    return 0
}

# =============================================================================
main() {
    local cmd=install
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --bundle) BUNDLE_FILE=${2:?}; shift 2 ;;
            --audio) cmd=audio; shift ;;
            -y|--yes) ASSUME_YES=1; shift ;;
            status) systemctl status "$UNIT" --no-pager; wg show "$WG_IF" 2>/dev/null | grep -E 'handshake|transfer'; exit ;;
            logs) exec journalctl -u "$UNIT" -f -n 100 ;;
            restart) require_root; systemctl restart "wg-quick@$WG_IF" "$UNIT"; ok "restarted"; exit ;;
            uninstall)
                require_root
                systemctl disable --now "$UNIT" "wg-quick@$WG_IF" >/dev/null 2>&1 || true
                rm -rf "$APP" "$ETC" "/etc/systemd/system/$UNIT.service" "/etc/wireguard/$WG_IF.conf"
                systemctl daemon-reload; ok "removed"; exit ;;
            -h|--help|help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit ;;
            *) die "unknown option '$1' (try: $0 help)" ;;
        esac
    done
    require_root

    if [[ $cmd == audio ]]; then
        RUN_USER=$(systemctl show -p User --value "$UNIT" 2>/dev/null); RUN_USER=${RUN_USER:-intelligence}
        set_xdg_env; choose_audio; systemctl restart "$UNIT"; ok "restarted with the new devices"; exit
    fi

    check_system
    install_packages
    read_bundle
    setup_tunnel
    detect_audio_user
    set_xdg_env
    install_app
    choose_audio
    install_service
    harden_pi
    echo
    echo "${GREEN}${BOLD}Done!${RESET} Say \"Hey Claude\" (or just \"Claude\") and ask away."
    echo "  After a reply you have 10 seconds to keep talking without the wake phrase."
    echo "  Logs: sudo ./local_setup.sh logs    Status: sudo ./local_setup.sh status"
}

main "$@"
