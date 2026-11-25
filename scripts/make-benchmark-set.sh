#!/usr/bin/env bash
# =============================================================================
#  make-benchmark-set.sh - build a speech test set for echo-bench (macOS only)
#
#  Speaks every line of bench/sentences.txt with several of macOS's built-in
#  voices (`say`) and writes 48 kHz WAV files plus a manifest:
#
#     bench/audio/<nn>_<voice>.wav
#     bench/manifest.tsv            <file> TAB <reference text>
#
#  48 kHz (like most microphones) means the files go through the same
#  resampling path as live audio. Synthetic speech is cleaner than a real
#  voice, so treat absolute error rates as optimistic; the benchmark is for
#  COMPARING settings. Add your own recordings to the manifest for real-world
#  numbers.
#
#  Usage: ./scripts/make-benchmark-set.sh [voice ...]
# =============================================================================
set -euo pipefail

if ! command -v say >/dev/null 2>&1; then
    echo "error: needs macOS's 'say' command (or write bench/manifest.tsv by hand)" >&2
    exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SENTENCES="$ROOT/bench/sentences.txt"
OUT="$ROOT/bench/audio"
MANIFEST="$ROOT/bench/manifest.tsv"

# Default voices: a mix of US, UK, Australian, Irish and Indian English.
if [[ $# -gt 0 ]]; then
    voices=("$@")
else
    voices=("Samantha" "Daniel" "Karen" "Moira" "Rishi")
fi

mkdir -p "$OUT"
: > "$MANIFEST"

n=0
v=0
while IFS= read -r line; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    n=$((n + 1))
    # Each sentence is spoken by two different voices (rotating through the list).
    for k in 0 1; do
        voice="${voices[$(((v + k) % ${#voices[@]}))]}"
        file="$OUT/$(printf '%02d' "$n")_${voice// /_}.wav"
        say -v "$voice" --data-format=LEI16@48000 -o "$file" "$line"
        printf '%s\t%s\n' "audio/$(basename "$file")" "$line" >> "$MANIFEST"
    done
    v=$((v + 1))
done < "$SENTENCES"

echo "Wrote $(wc -l < "$MANIFEST" | tr -d ' ') clips to $OUT and $MANIFEST"
