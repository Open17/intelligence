#!/usr/bin/env bash
# WireGuard tunnel between the server and each device.
#
#   server  10.77.0.1/24  listens on UDP 51820 (the ONLY port opened to the internet)
#   devices 10.77.0.2+    each with its own key pair and a pre-shared key
#
# Devices route only 10.77.0.1 through the tunnel (AllowedIPs = 10.77.0.1/32):
# the rest of the Pi's traffic is untouched, and devices cannot reach each other.

WG_DIR=/etc/wireguard
WG_IF=wg0
WG_PORT=${WG_PORT:-51820}
WG_NET=10.77.0
WG_SERVER_IP=$WG_NET.1
WG_PEERS_DIR=/etc/intelligence/wireguard  # one file per device: name, IP, keys

wg_init_server() {
    install -d -m 700 "$WG_DIR" "$WG_PEERS_DIR"
    if [[ ! -f $WG_DIR/server.key ]]; then
        (umask 077 && wg genkey > "$WG_DIR/server.key")
        wg pubkey < "$WG_DIR/server.key" > "$WG_DIR/server.pub"
    fi
    wg_write_server_config
}

wg_write_server_config() {
    {
        echo "# Managed by server_setup.sh - edits are overwritten."
        echo "[Interface]"
        echo "Address = $WG_SERVER_IP/24"
        echo "ListenPort = $WG_PORT"
        echo "PrivateKey = $(cat "$WG_DIR/server.key")"
        local peer
        for peer in "$WG_PEERS_DIR"/*.peer; do
            [[ -f $peer ]] || continue
            # shellcheck disable=SC1090
            ( source "$peer"
              echo
              echo "# device: $NAME"
              echo "[Peer]"
              echo "PublicKey = $PUBLIC_KEY"
              echo "PresharedKey = $PRESHARED_KEY"
              echo "AllowedIPs = $IP/32" )
        done
    } | write_file "$WG_DIR/$WG_IF.conf" 600
}

# wg_add_peer NAME  -> prints the device's IP; creates keys once
wg_add_peer() {
    local name=$1 file="$WG_PEERS_DIR/$1.peer" n ip used
    if [[ ! -f $file ]]; then
        for n in $(seq 2 254); do
            used=$(grep -h "^IP=$WG_NET.$n\$" "$WG_PEERS_DIR"/*.peer 2>/dev/null || true)
            [[ -z $used ]] && { ip=$WG_NET.$n; break; }
        done
        [[ -n ${ip:-} ]] || die "no free tunnel addresses left"
        local priv; priv=$(wg genkey)
        {
            echo "NAME=$name"
            echo "IP=$ip"
            echo "PRIVATE_KEY=$priv"
            echo "PUBLIC_KEY=$(echo "$priv" | wg pubkey)"
            echo "PRESHARED_KEY=$(wg genpsk)"
        } | write_file "$file" 600
    fi
    wg_write_server_config
    # shellcheck disable=SC1090
    ( source "$file"; echo "$IP" )
}

wg_remove_peer() {
    rm -f "$WG_PEERS_DIR/$1.peer"
    wg_write_server_config
}

# wg_client_config NAME ENDPOINT  -> prints the device's wg config
wg_client_config() {
    local name=$1 endpoint=$2
    # shellcheck disable=SC1090
    ( source "$WG_PEERS_DIR/$name.peer"
      echo "[Interface]"
      echo "Address = $IP/32"
      echo "PrivateKey = $PRIVATE_KEY"
      echo
      echo "[Peer]"
      echo "PublicKey = $(cat "$WG_DIR/server.pub")"
      echo "PresharedKey = $PRESHARED_KEY"
      echo "Endpoint = $endpoint:$WG_PORT"
      echo "AllowedIPs = $WG_SERVER_IP/32"
      echo "PersistentKeepalive = 25" )
}

wg_reload() {
    if systemctl is-active --quiet "wg-quick@$WG_IF"; then
        wg syncconf "$WG_IF" <(wg-quick strip "$WG_IF")
    else
        systemctl enable --now "wg-quick@$WG_IF" >/dev/null 2>&1
    fi
}
