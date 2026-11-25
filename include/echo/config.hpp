// =============================================================================
//  echo/config.hpp - every setting of the speech-to-text pipeline in one place
// =============================================================================
//
//  Fill in a SpeechListenerConfig and pass it to SpeechListener. The defaults
//  are tuned for "under ~1 s from end of speech to text" on a laptop:
//
//      echo::SpeechListenerConfig config;
//      config.whisper_model_path = "models/ggml-tiny.en.bin";   // e.g. on a Pi
//      config.endpointing.pause_ms = 500;                       // snappier
//      echo::SpeechListener listener(config);
// =============================================================================
#pragma once

#include <string>

namespace echo {

/// Where whisper.cpp and the grammar model run. The VAD always uses the CPU.
enum class ComputeBackend {
    Auto,  ///< GPU if one is available, otherwise CPU (recommended)
    Gpu,   ///< GPU only: loading fails if no GPU is available
    Cpu,   ///< CPU only, even if a GPU is present
};

/// Rules for deciding when you have finished a sentence. All times in ms.
/// The VAD works in 32 ms frames, so times are rounded up to multiples of 32.
struct EndpointingConfig {
    float speech_threshold = 0.5f;   ///< VAD probability needed to START speech
    float silence_threshold = 0.35f; ///< below this an ongoing utterance counts as silent
    int pre_roll_ms = 300;           ///< audio kept from just before speech starts
    int min_speech_ms = 200;         ///< shorter sounds are ignored (coughs, clicks)
    int speculative_ms = 300;        ///< start transcribing early after this much silence, so
                                     ///< the text is ready at pause_ms (0 = off; saves CPU)
    int pause_ms = 700;              ///< silence before transcribing; emit if the text looks finished
    int force_ms = 2000;             ///< silence after which unfinished-looking text is emitted anyway
    int max_utterance_ms = 28000;    ///< longest single utterance (whisper's limit is 30 s)
};

struct SpeechListenerConfig {
    // ---- Models (see scripts/download-models.sh) -----------------------------
    std::string vad_model_path = "models/silero_vad.onnx";
    std::string whisper_model_path = "models/ggml-base.en.bin";  ///< tiny.en for a Pi
    std::string language = "en";  ///< for multilingual models; ".en" models ignore it

    // ---- Compute ------------------------------------------------------------------
    ComputeBackend backend = ComputeBackend::Auto;
    int n_threads = 0;          ///< CPU threads for inference; 0 = number of CPU cores
    int whisper_audio_ctx = 0;  ///< 0 = default. Smaller (e.g. 768) is faster but less accurate
    int whisper_beam_size = -1; ///< -1 = auto (beam 5 on GPU, greedy on CPU), 0 = greedy, N = beam

    // ---- Endpointing -----------------------------------------------------------------
    EndpointingConfig endpointing;

    // ---- Text clean-up ------------------------------------------------------------------
    /// Remove "um", "uh", stutters ("the the") and comma-delimited "you know",
    /// "I mean", "like" with simple rules. Instant; Sentence::raw_text keeps
    /// whisper's exact output either way.
    bool remove_filler_words = true;

    // ---- Grammar correction (optional, off by default) ------------------------------------
    /// Clean up each sentence with a small LLM: removes filler words ("um",
    /// "you know") and fixes grammar without changing the meaning. Adds roughly
    /// 50-150 ms per sentence on a GPU, more on a CPU. Requires a build with
    /// ECHO_ENABLE_GRAMMAR=ON (the default) and the model file.
    bool enable_grammar_correction = false;
    std::string grammar_model_path = "models/qwen2.5-0.5b-instruct-q4_k_m.gguf";

    // ---- Audio input ------------------------------------------------------------------
    bool use_microphone = true;  ///< false: feed audio yourself with pushAudio()
    int capture_device = -1;     ///< index from SpeechListener::listInputDevices(); -1 = default
    /// High-pass filter cut-off in Hz applied to all input (removes DC offset,
    /// mains hum and rumble below the speech range). 0 = off.
    int highpass_hz = 80;
    /// Scale each utterance to a consistent level before whisper (quiet or
    /// distant microphones). Gain is capped at +30 dB.
    bool normalize_volume = true;

    // ---- Diagnostics --------------------------------------------------------------------
    bool verbose = false;  ///< print whisper/ggml logs and endpointing events to stderr
};

const char* toString(ComputeBackend backend);

}  // namespace echo
