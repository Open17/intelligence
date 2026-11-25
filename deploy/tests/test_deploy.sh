#!/usr/bin/env bash
# =============================================================================
#  Tests for the setup libraries: PKI, pairing bundles, WireGuard configs.
#  Runs as a normal user in a temporary directory; touches nothing else.
#
#      deploy/tests/test_deploy.sh
#
#  Needs: bash, openssl 3, python3, base64, tar, wg (wireguard-tools).
# =============================================================================
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
LIB=$HERE/../lib
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT

PASS=0 FAIL=0
check() {  # check "description" command...
    local desc=$1; shift
    if "$@" >/dev/null 2>&1; then PASS=$((PASS + 1)); echo "  ok   $desc"
    else FAIL=$((FAIL + 1)); echo "  FAIL $desc"; fi
}
refute() { local desc=$1; shift; if "$@" >/dev/null 2>&1; then FAIL=$((FAIL + 1)); echo "  FAIL $desc"; else PASS=$((PASS + 1)); echo "  ok   $desc"; fi; }

# shellcheck source=../lib/common.sh
source "$LIB/common.sh"
write_file() {  # test version: no chown (we are not root)
    local file=$1 mode=$2; cat > "$file"; chmod "$mode" "$file"
}

# ---- PKI -----------------------------------------------------------------------------
echo "PKI"
PKI_DIR=$T/pki
# shellcheck source=../lib/pki.sh
source "$LIB/pki.sh"
pki_init_ca
pki_server_cert 10.77.0.1
pki_device_cert pi
check "CA key is private (0600)" test "$(stat -c %a "$PKI_DIR/ca.key" 2>/dev/null || stat -f %Lp "$PKI_DIR/ca.key")" = 600
check "server cert chains to the CA" openssl verify -CAfile "$PKI_DIR/ca.crt" "$PKI_DIR/server.crt"
check "device cert chains to the CA" openssl verify -CAfile "$PKI_DIR/ca.crt" "$PKI_DIR/device-pi.crt"
check "device cert CN is the device name" grep -q "CN *= *pi$" <(openssl x509 -in "$PKI_DIR/device-pi.crt" -noout -subject -nameopt sep_multiline)
check "server cert has the tunnel IP and name" grep -q "IP Address:10.77.0.1" <(openssl x509 -in "$PKI_DIR/server.crt" -noout -ext subjectAltName)
check "server cert is for serverAuth only" grep -q "TLS Web Server Authentication" <(openssl x509 -in "$PKI_DIR/server.crt" -noout -ext extendedKeyUsage)
check "device cert is for clientAuth only" grep -q "TLS Web Client Authentication" <(openssl x509 -in "$PKI_DIR/device-pi.crt" -noout -ext extendedKeyUsage)
refute "device cert cannot act as a server" openssl verify -purpose sslserver -CAfile "$PKI_DIR/ca.crt" "$PKI_DIR/device-pi.crt"
before=$(openssl x509 -in "$PKI_DIR/ca.crt" -noout -fingerprint)
pki_init_ca
check "re-running keeps the existing CA" test "$before" = "$(openssl x509 -in "$PKI_DIR/ca.crt" -noout -fingerprint)"
check "Python's TLS stack accepts the certificates (mutual TLS)" python3 - "$PKI_DIR" <<'EOF'
import ssl, sys
d = sys.argv[1]
srv = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); srv.minimum_version = ssl.TLSVersion.TLSv1_3
srv.load_cert_chain(f"{d}/server.crt", f"{d}/server.key"); srv.load_verify_locations(f"{d}/ca.crt")
srv.verify_mode = ssl.CERT_REQUIRED
cli = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); cli.load_verify_locations(f"{d}/ca.crt")
cli.load_cert_chain(f"{d}/device-pi.crt", f"{d}/device-pi.key")
a, b = ssl.MemoryBIO(), ssl.MemoryBIO(); c, e = ssl.MemoryBIO(), ssl.MemoryBIO()
s = srv.wrap_bio(a, b, server_side=True); k = cli.wrap_bio(c, e, server_hostname="intelligence-server")
for _ in range(10):
    for obj, src, dst in ((k, e, a), (s, b, c)):
        try: obj.do_handshake()
        except ssl.SSLWantReadError: pass
        dst.write(src.read())
assert s.getpeercert()["subject"][0][0] == ("commonName", "pi"), s.getpeercert()
EOF

# ---- Pairing bundles ----------------------------------------------------------------------
echo "Pairing"
# shellcheck source=../lib/pairing.sh
source "$LIB/pairing.sh"
mkdir -p "$T/src"
echo "secret wg config" > "$T/src/intelligence.conf"
cp "$PKI_DIR/device-pi.key" "$T/src/device.key"
code=$(pairing_new_code)
check "pairing code looks like XXXX-XXXX-XXXX-XXXX" grep -Eq '^[A-Z2-7]{4}-[A-Z2-7]{4}-[A-Z2-7]{4}-[A-Z2-7]{4}$' <<<"$code"
pairing_pack "$T/src" pi "$code" > "$T/bundle.txt"
refute "bundle text does not contain the secrets" grep -q "secret wg config" "$T/bundle.txt"
check "right code unpacks" pairing_unpack "$T/bundle.txt" "$code" "$T/out1"
check "unpacked files are identical" cmp "$T/src/device.key" "$T/out1/device.key"
check "code is case/space-insensitive" pairing_unpack "$T/bundle.txt" " ${code,,} " "$T/out2"
refute "wrong code is rejected" pairing_unpack "$T/bundle.txt" "AAAA-BBBB-CCCC-DDDD" "$T/out3"
# Flip one character of the ciphertext (always a real change).
awk 'NR == 5 { c = substr($0, 1, 1); $0 = (c == "A" ? "B" : "A") substr($0, 2) } { print }' \
    "$T/bundle.txt" > "$T/tampered.txt"
refute "tampering really changed the bundle" cmp -s "$T/bundle.txt" "$T/tampered.txt"
refute "tampered bundle is rejected" pairing_unpack "$T/tampered.txt" "$code" "$T/out4"
sed 's/^/    /; /^ *$/d' "$T/bundle.txt" > "$T/mangled.txt"
check "indented paste without blank lines still works" pairing_unpack "$T/mangled.txt" "$code" "$T/out5"
refute "random text is rejected" pairing_unpack <(echo hello) "$code" "$T/out6"

# ---- WireGuard ------------------------------------------------------------------------------
if have wg; then
    echo "WireGuard"
    # shellcheck source=../lib/wireguard.sh
    source "$LIB/wireguard.sh"
    # Point the library at the temp dir (it defaults to /etc/...).
    # shellcheck disable=SC2034
    WG_DIR=$T/wg WG_PEERS_DIR=$T/wgpeers
    wg_init_server
    ip1=$(wg_add_peer pi); ip2=$(wg_add_peer kitchen); ip1b=$(wg_add_peer pi)
    check "devices get distinct tunnel IPs" test "$ip1" = 10.77.0.2 -a "$ip2" = 10.77.0.3
    check "re-adding a device keeps its IP" test "$ip1" = "$ip1b"
    check "server config lists both peers" test "$(grep -c '^\[Peer\]' "$WG_DIR/wg0.conf")" = 2
    wg_client_config pi vpn.example.com > "$T/pi.conf"
    check "device routes only the server through the tunnel" grep -q "^AllowedIPs = 10.77.0.1/32$" "$T/pi.conf"
    check "device uses a pre-shared key" grep -q "^PresharedKey = " "$T/pi.conf"
    check "device keys match the server's peer entry" grep -q "$(sed -n 's/^PrivateKey = //p' "$T/pi.conf" | wg pubkey)" "$WG_DIR/wg0.conf"
    wg_remove_peer kitchen
    check "removed device is gone from the server config" test "$(grep -c '^\[Peer\]' "$WG_DIR/wg0.conf")" = 1
    ip3=$(wg_add_peer garage)
    check "a freed IP is reused" test "$ip3" = 10.77.0.3
else
    echo "WireGuard: skipped (wg not installed)"
fi

echo
echo "$PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
