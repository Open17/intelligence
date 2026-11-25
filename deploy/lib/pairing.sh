#!/usr/bin/env bash
# Pairing bundles: everything a device needs (WireGuard config, CA, its
# certificate and key, settings), encrypted with a one-time pairing code.
#
#   AES-256-CBC, key from PBKDF2-SHA256 (600,000 iterations, random salt)
#   + HMAC-SHA256 over the ciphertext, checked before decrypting
#
# The code is 80 random bits shown as XXXX-XXXX-XXXX-XXXX. It's passed to
# openssl and python through the environment, never on a command line
# (other users could read command lines in `ps`).

PAIRING_BEGIN="-----BEGIN INTELLIGENCE PAIRING-----"
PAIRING_END="-----END INTELLIGENCE PAIRING-----"

pairing_new_code() {
    python3 -c 'import base64, secrets
c = base64.b32encode(secrets.token_bytes(10)).decode()
print("-".join(c[i:i + 4] for i in range(0, 16, 4)))'
}

_pairing_hmac() {  # FILE (code in $CODE)
    python3 -c 'import hashlib, hmac, os, sys
print(hmac.new(os.environ["CODE"].encode(), open(sys.argv[1], "rb").read(), hashlib.sha256).hexdigest())' "$1"
}

# pairing_pack SRC_DIR DEVICE CODE  -> armored bundle on stdout
pairing_pack() {
    local src=$1 device=$2 bin mac
    bin=$(mktemp)
    tar -C "$src" -czf - . \
        | CODE=$3 openssl enc -aes-256-cbc -pbkdf2 -iter 600000 -md sha256 -salt -pass env:CODE > "$bin"
    mac=$(CODE=$3 _pairing_hmac "$bin")
    echo "$PAIRING_BEGIN"
    echo "Device: $device"
    echo "MAC: $mac"
    echo
    base64 < "$bin" | tr -d '\n' | fold -w 76
    echo
    echo "$PAIRING_END"
    rm -f "$bin"
}

# pairing_unpack BUNDLE_TEXT_FILE CODE OUT_DIR  -> returns 1 with a message on failure
pairing_unpack() {
    local text=$1 code out=$3 bin want got
    code=$(printf '%s' "$2" | tr -d ' \n' | tr '[:lower:]' '[:upper:]')
    grep -q -- "$PAIRING_BEGIN" "$text" || { echo "that is not a pairing bundle" >&2; return 1; }
    bin=$(mktemp)
    # The body is every pure-base64 line (headers contain ':' or '-'), so a
    # paste that lost blank lines or gained indentation still works.
    if ! sed 's/^[[:space:]]*//; s/[[:space:]]*$//' "$text" | grep -E '^[A-Za-z0-9+/=]+$' \
            | base64 -d > "$bin" 2>/dev/null; then
        rm -f "$bin"; echo "the bundle is damaged (copy it again)" >&2; return 1
    fi
    want=$(sed -n 's/^[[:space:]]*MAC: //p' "$text" | tr -d '[:space:]')
    got=$(CODE=$code _pairing_hmac "$bin")
    if [[ -z $want || $got != "$want" ]]; then
        rm -f "$bin"; echo "wrong pairing code, or the bundle was changed" >&2; return 1
    fi
    mkdir -p "$out"
    if ! CODE=$code openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -md sha256 -pass env:CODE -in "$bin" \
            | tar -xzf - -C "$out"; then
        rm -f "$bin"; echo "could not decrypt the bundle" >&2; return 1
    fi
    rm -f "$bin"
}
