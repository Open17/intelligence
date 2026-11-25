#!/usr/bin/env bash
# =============================================================================
#  Host firewall rules for Intelligence (applied at boot by
#  intelligence-firewall.service, after Docker).
#
#  1. Accept WireGuard (UDP 51820) - the only new inbound port.
#  2. Drop anything for the voice port (8765) that did not come through the
#     WireGuard interface. Docker's published ports bypass the INPUT chain,
#     so this has to be in DOCKER-USER.
#
#  Idempotent: rules are only added if missing. SSH is never touched.
# =============================================================================
set -euo pipefail

WG_IF=${WG_IF:-wg0}
WG_PORT=${WG_PORT:-51820}
VOICE_PORT=${VOICE_PORT:-8765}

add() {  # add CHAIN RULE...  (inserted at the top, only if not present)
    local chain=$1; shift
    iptables -C "$chain" "$@" 2>/dev/null || iptables -I "$chain" 1 "$@"
}

add INPUT -p udp --dport "$WG_PORT" -m comment --comment intelligence-wireguard -j ACCEPT

if iptables -n -L DOCKER-USER >/dev/null 2>&1; then
    add DOCKER-USER -p tcp --dport "$VOICE_PORT" ! -i "$WG_IF" -m comment --comment intelligence-voice-wg-only -j DROP
fi

if command -v ip6tables >/dev/null 2>&1 && ip6tables -n -L DOCKER-USER >/dev/null 2>&1; then
    ip6tables -C DOCKER-USER -p tcp --dport "$VOICE_PORT" -j DROP 2>/dev/null \
        || ip6tables -I DOCKER-USER 1 -p tcp --dport "$VOICE_PORT" -j DROP
fi

# Hosts using ufw (e.g. some Ubuntu images): open WireGuard there too.
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow "$WG_PORT/udp" comment intelligence-wireguard >/dev/null
fi
