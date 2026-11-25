// =============================================================================
//  grammar_corrector.hpp - optional clean-up of transcripts with a small LLM
// =============================================================================
//
//  Speech is messy: "um so I I was like thinking we could uh go". This stage
//  asks a small instruction-tuned model (default: Qwen2.5-0.5B-Instruct,
//  ~400 MB GGUF) running in llama.cpp to remove filler words and fix grammar
//  WITHOUT changing the meaning.
//
//  Small models sometimes misbehave (e.g. answer a question instead of
//  correcting it), so the output passes sanity checks and the original text
//  is returned whenever the result looks suspicious.
//
//  Like WhisperTranscriber, correct() is not thread-safe: call it from one thread.
// =============================================================================
#pragma once

#include <cstdint>
#include <string>
#include <vector>

// Forward declarations of llama.cpp's types, so this header does not need llama.h.
struct llama_model;
struct llama_context;
struct llama_sampler;
struct llama_vocab;
typedef int32_t llama_token;

namespace echo {

struct GrammarConfig {
    std::string model_path;      ///< GGUF instruct model
    bool use_gpu = true;         ///< try the GPU first; falls back to CPU automatically
    int n_threads = 0;           ///< CPU threads, 0 = number of cores
    int max_output_tokens = 200; ///< hard cap on generated tokens
};

class GrammarCorrector {
public:
    /// Loads the model. Throws std::runtime_error if it cannot be loaded.
    explicit GrammarCorrector(const GrammarConfig& config);
    ~GrammarCorrector();

    GrammarCorrector(const GrammarCorrector&) = delete;
    GrammarCorrector& operator=(const GrammarCorrector&) = delete;

    /// Returns the cleaned-up text, or `text` unchanged if the model's answer
    /// fails the sanity checks.
    std::string correct(const std::string& text);

    bool usingGpu() const { return using_gpu_; }

    /// e.g. "GPU - Metal: MTL0 (Apple M4)" or "CPU - 4 threads".
    std::string backendDescription() const;

private:
    std::string buildPrompt(const std::string& text) const;
    std::vector<llama_token> tokenize(const std::string& text) const;
    std::string generate(const std::vector<llama_token>& prompt, int max_tokens);

    GrammarConfig config_;
    llama_model* model_ = nullptr;
    llama_context* ctx_ = nullptr;
    llama_sampler* sampler_ = nullptr;
    const llama_vocab* vocab_ = nullptr;
    bool using_gpu_ = false;
    std::string gpu_name_;

    // Tokens whose results are currently stored in the model's KV cache. The
    // long instructions at the start of every prompt are identical, so we keep
    // them cached and only process the new part - a big speed-up on CPUs.
    std::vector<llama_token> cached_tokens_;
};

}  // namespace echo
