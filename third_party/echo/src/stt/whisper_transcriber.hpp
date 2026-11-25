// =============================================================================
//  whisper_transcriber.hpp - turns a buffer of speech into text (whisper.cpp)
// =============================================================================
//
//  One WhisperTranscriber owns one loaded model. transcribe() is NOT
//  thread-safe: call it from a single thread (SpeechListener uses a dedicated
//  transcription thread).
// =============================================================================
#pragma once

#include <functional>
#include <optional>
#include <string>
#include <vector>

struct whisper_context;  // forward declaration: no need to include whisper.h here

namespace echo {

struct WhisperConfig {
    std::string model_path;          ///< e.g. "models/ggml-base.en.bin" (quantized works too)
    std::string language = "en";     ///< ignored by English-only ".en" models
    bool use_gpu = true;             ///< try the GPU first; falls back to CPU automatically
    int n_threads = 0;               ///< CPU threads, 0 = number of cores
    int audio_ctx = 0;               ///< encoder context; 0 = full 30 s (see README "Tuning")
    int beam_size = -1;              ///< -1 = auto (5 on GPU, greedy on CPU), 0 = greedy, N = beam
};

class WhisperTranscriber {
public:
    /// Loads the model. Throws std::runtime_error if it cannot be loaded.
    explicit WhisperTranscriber(const WhisperConfig& config);
    ~WhisperTranscriber();

    WhisperTranscriber(const WhisperTranscriber&) = delete;
    WhisperTranscriber& operator=(const WhisperTranscriber&) = delete;

    /// Transcribes 16 kHz mono float samples. Returns cleaned-up text, or an
    /// empty string if no speech was recognised.
    std::string transcribe(const std::vector<float>& samples);

    /// Same, but `should_abort` is polled while whisper runs; when it returns
    /// true the run stops early and std::nullopt is returned. Used to cancel
    /// transcriptions that became out of date (the speaker kept talking).
    std::optional<std::string> transcribe(const std::vector<float>& samples,
                                          const std::function<bool()>& should_abort);

    /// True if the model is running on a GPU.
    bool usingGpu() const { return using_gpu_; }

    /// e.g. "GPU - Metal: MTL0 (Apple M4)" or "CPU - 10 threads".
    std::string backendDescription() const;

private:
    WhisperConfig config_;
    whisper_context* ctx_ = nullptr;
    bool using_gpu_ = false;
    std::string gpu_name_;
};

}  // namespace echo
