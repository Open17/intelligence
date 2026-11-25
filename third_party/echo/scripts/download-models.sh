#!/usr/bin/env bash
# =============================================================================
#  download-models.sh - fetch the model files echo needs into ./models
#
#  Usage:
#     ./scripts/download-models.sh                  # Silero VAD + base.en (default)
#     ./scripts/download-models.sh --pi             # Silero VAD + tiny.en (+ q5_1) for Raspberry Pi
#     ./scripts/download-models.sh --whisper tiny.en-q5_1 --whisper base.en-q5_1
#     ./scripts/download-models.sh --grammar        # also get the Qwen2.5 0.5B grammar model
#     ./scripts/download-models.sh --all            # everything listed below
#
#  Whisper model names (quantized variants are smaller and usually faster):
#     tiny.en  tiny.en-q5_1  tiny.en-q8_0
#     base.en  base.en-q5_1  base.en-q8_0
#     small.en small.en-q5_1 small.en-q8_0
#
#  Files that already exist are skipped, so it is safe to run this repeatedly.
# =============================================================================
set -euo pipefail

# --- Where things come from ----------------------------------------------------
WHISPER_BASE_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
SILERO_URL="https://github.com/snakers4/silero-vad/raw/v6.2.3/src/silero_vad/data/silero_vad.onnx"
GRAMMAR_URL="https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf"

# Resolve the repository root from this script's location, so the script works
# no matter which folder you run it from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODELS_DIR="${MODELS_DIR:-$SCRIPT_DIR/../models}"

usage() {
    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

# --- Parse arguments -----------------------------------------------------------
whisper_models=()
want_grammar=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --whisper)  [[ $# -ge 2 ]] || usage 1; whisper_models+=("$2"); shift 2 ;;
        --pi)       whisper_models+=("tiny.en" "tiny.en-q5_1"); shift ;;
        --grammar)  want_grammar=1; shift ;;
        --all)      whisper_models+=("tiny.en" "tiny.en-q5_1" "base.en" "base.en-q5_1"); want_grammar=1; shift ;;
        -h|--help)  usage 0 ;;
        *)          echo "Unknown option: $1" >&2; usage 1 ;;
    esac
done

# Default when no whisper model was requested.
if [[ ${#whisper_models[@]} -eq 0 ]]; then
    whisper_models=("base.en")
fi

# --- Helpers -------------------------------------------------------------------
download() {
    local url="$1" dest="$2"
    if [[ -s "$dest" ]]; then
        echo "  [skip] $(basename "$dest") already exists"
        return
    fi
    echo "  [get ] $(basename "$dest")"
    # Download to a temporary name first so an interrupted download never
    # leaves a half-written file that looks valid.
    if command -v curl >/dev/null 2>&1; then
        curl -fL --progress-bar -o "$dest.part" "$url"
    elif command -v wget >/dev/null 2>&1; then
        wget -q --show-progress -O "$dest.part" "$url"
    else
        echo "error: need curl or wget" >&2
        exit 1
    fi
    mv "$dest.part" "$dest"
}

# --- Do it ---------------------------------------------------------------------
mkdir -p "$MODELS_DIR"
MODELS_DIR="$(cd "$MODELS_DIR" && pwd)"
echo "Downloading models into $MODELS_DIR"

download "$SILERO_URL" "$MODELS_DIR/silero_vad.onnx"

for name in "${whisper_models[@]}"; do
    download "$WHISPER_BASE_URL/ggml-$name.bin" "$MODELS_DIR/ggml-$name.bin"
done

if [[ $want_grammar -eq 1 ]]; then
    download "$GRAMMAR_URL" "$MODELS_DIR/qwen2.5-0.5b-instruct-q4_k_m.gguf"
fi

echo "Done."
