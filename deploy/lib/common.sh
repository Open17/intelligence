#!/usr/bin/env bash
# Shared helpers for server_setup.sh and local_setup.sh: output, prompts, checks.
# shellcheck disable=SC2034  # colours are used by the sourcing scripts

if [[ -t 1 ]]; then
    BOLD=$'\e[1m'; DIM=$'\e[2m'; RED=$'\e[31m'; GREEN=$'\e[32m'; YELLOW=$'\e[33m'; BLUE=$'\e[34m'; RESET=$'\e[0m'
else
    BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; BLUE=""; RESET=""
fi

step()  { printf '\n%s==>%s %s%s%s\n' "$BLUE" "$RESET" "$BOLD" "$*" "$RESET"; }
info()  { printf '    %s\n' "$*"; }
ok()    { printf '    %s✓%s %s\n' "$GREEN" "$RESET" "$*"; }
warn()  { printf '    %s!%s %s\n' "$YELLOW" "$RESET" "$*" >&2; }
die()   { printf '\n%sError:%s %s\n' "$RED" "$RESET" "$*" >&2; exit 1; }

require_root() {
    [[ $EUID -eq 0 ]] || die "run this with sudo:  sudo $0 $*"
}

# ask VAR "Question" "default"  -> sets VAR (keeps an existing non-empty value)
ask() {
    local __var=$1 __q=$2 __def=${3:-} __ans
    if [[ -n ${!__var:-} ]]; then return; fi
    if [[ ${ASSUME_YES:-0} == 1 || ! -t 0 ]]; then
        printf -v "$__var" '%s' "$__def"; return
    fi
    read -r -p "    $__q${__def:+ [$__def]}: " __ans
    printf -v "$__var" '%s' "${__ans:-$__def}"
}

# ask_secret VAR "Question"  -> hidden input, never echoed or logged
ask_secret() {
    local __var=$1 __q=$2 __ans
    if [[ -n ${!__var:-} ]]; then return; fi
    [[ -t 0 ]] || die "$__q is required (set it in the environment for non-interactive runs)"
    read -r -s -p "    $__q: " __ans; echo
    printf -v "$__var" '%s' "$__ans"
}

# confirm "Question" [default y|n]
confirm() {
    local q=$1 def=${2:-y} ans
    if [[ ${ASSUME_YES:-0} == 1 || ! -t 0 ]]; then [[ $def == y ]]; return; fi
    read -r -p "    $q [$([[ $def == y ]] && echo Y/n || echo y/N)]: " ans
    ans=${ans:-$def}
    [[ ${ans,,} == y* ]]
}

have() { command -v "$1" >/dev/null 2>&1; }

apt_install() {
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$@" >/dev/null
}

# Writes stdin to FILE atomically with MODE (and OWNER if given); never world-readable.
write_file() {
    local file=$1 mode=$2 owner=${3:-root:root} tmp
    tmp=$(mktemp "$(dirname "$file")/.tmp.XXXXXX")
    cat > "$tmp"
    chmod "$mode" "$tmp"
    chown "$owner" "$tmp"
    mv -f "$tmp" "$file"
}
