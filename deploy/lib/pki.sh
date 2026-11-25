#!/usr/bin/env bash
# Private certificate authority for mutual TLS between devices and the server.
#
# ECDSA P-256 everywhere. The CA key never leaves /etc/intelligence/pki
# (root only). The server gets its own key and certificate; each device gets
# a client certificate whose common name is the device name, which the
# server's allow-list checks.

PKI_DIR=${PKI_DIR:-/etc/intelligence/pki}
SERVER_NAME=intelligence-server

# pki_issue FILE_BASENAME COMMON_NAME KIND(server|client) [SAN]
pki_issue() {
    local base=$1 cn=$2 kind=$3 san=${4:-} ext
    ext=$(mktemp)
    {
        echo "basicConstraints=critical,CA:FALSE"
        echo "keyUsage=critical,digitalSignature"
        if [[ $kind == server ]]; then echo "extendedKeyUsage=serverAuth"; else echo "extendedKeyUsage=clientAuth"; fi
        if [[ -n $san ]]; then echo "subjectAltName=$san"; fi
    } > "$ext"
    (umask 077 && openssl ecparam -name prime256v1 -genkey -noout -out "$PKI_DIR/$base.key" 2>/dev/null)
    openssl req -new -key "$PKI_DIR/$base.key" -subj "/CN=$cn" -out "$PKI_DIR/$base.csr" 2>/dev/null
    openssl x509 -req -in "$PKI_DIR/$base.csr" -CA "$PKI_DIR/ca.crt" -CAkey "$PKI_DIR/ca.key" \
        -CAcreateserial -sha256 -days 1825 -extfile "$ext" -out "$PKI_DIR/$base.crt" 2>/dev/null
    rm -f "$ext" "$PKI_DIR/$base.csr"
}

pki_init_ca() {
    install -d -m 700 "$PKI_DIR"
    [[ -f $PKI_DIR/ca.key ]] && return 0
    (umask 077 && openssl ecparam -name prime256v1 -genkey -noout -out "$PKI_DIR/ca.key" 2>/dev/null)
    openssl req -x509 -new -key "$PKI_DIR/ca.key" -sha256 -days 3650 \
        -subj "/CN=Intelligence Private CA" \
        -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
        -addext "keyUsage=critical,keyCertSign,cRLSign" \
        -out "$PKI_DIR/ca.crt" 2>/dev/null
}

pki_server_cert() {  # TUNNEL_IP
    [[ -f $PKI_DIR/server.crt ]] && return 0
    pki_issue server "$SERVER_NAME" server "DNS:$SERVER_NAME,IP:$1"
}

pki_device_cert() {  # DEVICE_NAME
    [[ -f $PKI_DIR/device-$1.crt ]] && return 0
    pki_issue "device-$1" "$1" client
}
