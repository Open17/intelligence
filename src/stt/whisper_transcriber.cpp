// =============================================================================
//  whisper_transcriber.cpp - see whisper_transcriber.hpp
// =============================================================================
#include "stt/whisper_transcriber.hpp"

#include <whisper.h>

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <stdexcept>

#include "core/text_utils.hpp"
#include "util/ggml_utils.hpp"

namespace echo {

namespace {

constexpr int kSampleRate = WHISPER_SAMPLE_RATE;  // 16000

whisper_context* loadModel(const std::string& path, bool use_gpu) {
    whisper_context_params params = whisper_context_default_params();
    params.use_gpu = use_gpu;
    params.flash_attn = use_gpu;  // faster attention kernels on GPU backends
    return whisper_init_from_file_with_params(path.c_str(), params);
}

}  // namespace

WhisperTranscriber::WhisperTranscriber(const WhisperConfig& config) : config_(config) {
    config_.n_threads = resolveThreadCount(config_.n_threads);

    if (!std::filesystem::exists(config_.model_path)) {
        throw std::runtime_error("whisper model not found: " + config_.model_path +
                                 " (run scripts/download-models.sh)");
    }

    // GPU first (if wanted and present), CPU as the fallback.
    gpu_name_ = config_.use_gpu ? findGpuDevice() : std::string();
    if (!gpu_name_.empty()) {
        ctx_ = loadModel(config_.model_path, /*use_gpu=*/true);
        using_gpu_ = ctx_ != nullptr;
    }
    if (ctx_ == nullptr) {
        ctx_ = loadModel(config_.model_path, /*use_gpu=*/false);
        using_gpu_ = false;
    }
    if (ctx_ == nullptr) {
        throw std::runtime_error("failed to load whisper model: " + config_.model_path);
    }

    // Beam search: measured on the benchmark it cuts errors in noisy audio by
    // ~40% for ~12 ms extra on a GPU, but costs ~20% more time on a CPU (far
    // more on a Raspberry Pi), so "auto" only uses it on a GPU.
    if (config_.beam_size < 0) {
        config_.beam_size = using_gpu_ ? 5 : 0;
    }

    // Warm-up: the very first run is slow (GPU kernels get compiled/loaded,
    // buffers allocated). Do it now on 1 s of faint noise so the user's first
    // sentence is as fast as the rest. The output is thrown away.
    std::vector<float> noise(kSampleRate);
    unsigned seed = 12345;
    for (float& s : noise) {
        seed = seed * 1664525u + 1013904223u;  // tiny deterministic random generator
        s = (static_cast<float>(seed >> 8) / 16777216.0f - 0.5f) * 0.02f;
    }
    transcribe(noise);
}

WhisperTranscriber::~WhisperTranscriber() {
    if (ctx_ != nullptr) {
        whisper_free(ctx_);
    }
}

std::string WhisperTranscriber::transcribe(const std::vector<float>& samples) {
    return transcribe(samples, {}).value_or(std::string());
}

std::optional<std::string> WhisperTranscriber::transcribe(
    const std::vector<float>& samples, const std::function<bool()>& should_abort) {
    // Whisper "hallucinates" words such as "you" or "Thank you." when given
    // silence or noise. In the pipeline the VAD keeps non-speech away from
    // whisper; this check is a cheap extra guard for (near-)digital silence.
    float peak = 0.0f;
    for (float s : samples) peak = std::max(peak, std::fabs(s));
    if (peak < 1e-3f) {
        return std::string();
    }

    // Very short clips confuse whisper; pad with silence up to 1 second.
    std::vector<float> padded;
    const std::vector<float>* audio = &samples;
    if (samples.size() < static_cast<std::size_t>(kSampleRate)) {
        padded = samples;
        padded.resize(kSampleRate, 0.0f);
        audio = &padded;
    }

    // Greedy decoding picks the single most likely next word each step; beam
    // search keeps `beam_size` candidate transcripts and picks the best one.
    const bool beam = config_.beam_size > 1;
    whisper_full_params params =
        whisper_full_default_params(beam ? WHISPER_SAMPLING_BEAM_SEARCH : WHISPER_SAMPLING_GREEDY);
    if (beam) params.beam_search.beam_size = config_.beam_size;
    params.n_threads = config_.n_threads;
    params.language = config_.language.c_str();
    params.translate = false;
    params.no_context = true;       // every utterance is independent
    params.no_timestamps = true;    // we only want text: skip timestamp tokens (faster)
    params.single_segment = true;   // one utterance -> one segment
    params.print_progress = false;
    params.print_realtime = false;
    params.print_special = false;
    params.print_timestamps = false;
    params.suppress_blank = true;
    params.suppress_nst = true;     // suppress non-speech tokens like "(music)"
    params.temperature_inc = 0.0f;  // no slow re-decoding fallback: keeps latency predictable
    params.audio_ctx = config_.audio_ctx;

    // Cancellation: whisper.cpp polls this C callback between compute steps.
    // A capture-less lambda converts to the required function pointer; the
    // std::function travels through the `void*` user-data pointer.
    struct AbortState {
        const std::function<bool()>* check;
        bool aborted = false;
    } abort_state{&should_abort};
    if (should_abort) {
        params.abort_callback = [](void* data) {
            auto* state = static_cast<AbortState*>(data);
            if (!state->aborted && (*state->check)()) {
                state->aborted = true;
                suppressGgmlErrors(true);  // the "failed to encode" that follows is expected
            }
            return state->aborted;
        };
        params.abort_callback_user_data = &abort_state;
    }

    const int rc = whisper_full(ctx_, params, audio->data(), static_cast<int>(audio->size()));
    if (abort_state.aborted) {
        suppressGgmlErrors(false);
        return std::nullopt;
    }
    if (rc != 0) {
        throw std::runtime_error("whisper_full() failed");
    }

    std::string text;
    const int segments = whisper_full_n_segments(ctx_);
    for (int i = 0; i < segments; ++i) {
        text += whisper_full_get_segment_text(ctx_, i);
    }
    return cleanTranscript(text);
}

std::string WhisperTranscriber::backendDescription() const {
    const std::string decoding =
        config_.beam_size > 1 ? ", beam " + std::to_string(config_.beam_size) : ", greedy";
    if (using_gpu_) {
        return std::string("GPU - ") + compiledGpuBackend() + ": " + gpu_name_ + decoding;
    }
    return "CPU - " + std::to_string(config_.n_threads) + " threads" + decoding;
}

}  // namespace echo
