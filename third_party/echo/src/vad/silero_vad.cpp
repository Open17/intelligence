// =============================================================================
//  silero_vad.cpp - see silero_vad.hpp
// =============================================================================
#include "vad/silero_vad.hpp"

#include <algorithm>
#include <filesystem>
#include <stdexcept>

namespace echo {

namespace {

constexpr std::size_t kStateSize = 2 * 1 * 128;

// Names of the model's input and output tensors (see the header).
constexpr const char* kInputNames[] = {"input", "state", "sr"};
constexpr const char* kOutputNames[] = {"output", "stateN"};

}  // namespace

SileroVad::SileroVad(const std::string& model_path)
    : env_(ORT_LOGGING_LEVEL_WARNING, "silero-vad"),
      memory_info_(Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault)),
      input_(kContextSamples + kWindowSamples, 0.0f),
      state_(kStateSize, 0.0f) {
    if (!std::filesystem::exists(model_path)) {
        throw std::runtime_error("Silero VAD model not found: " + model_path +
                                 " (run scripts/download-models.sh)");
    }

    // The model is tiny: one thread is fastest and leaves every other core
    // free for whisper.cpp.
    options_.SetIntraOpNumThreads(1);
    options_.SetInterOpNumThreads(1);
    options_.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_ENABLE_ALL);

    // ONNX Runtime wants a wide-character path on Windows and a normal one
    // elsewhere. std::filesystem::path::c_str() gives exactly the native kind.
    const std::filesystem::path path(model_path);
    session_ = std::make_unique<Ort::Session>(env_, path.c_str(), options_);
}

float SileroVad::process(const float* window) {
    // input_ = [ 64 samples of context | 512 new samples ]
    std::copy(window, window + kWindowSamples, input_.begin() + kContextSamples);

    const std::array<int64_t, 2> input_shape{1, static_cast<int64_t>(input_.size())};
    const std::array<int64_t, 3> state_shape{2, 1, 128};
    const std::array<int64_t, 1> sr_shape{1};

    // CreateTensor does not copy: the tensors just point at our vectors.
    std::array<Ort::Value, 3> inputs{
        Ort::Value::CreateTensor<float>(memory_info_, input_.data(), input_.size(),
                                        input_shape.data(), input_shape.size()),
        Ort::Value::CreateTensor<float>(memory_info_, state_.data(), state_.size(),
                                        state_shape.data(), state_shape.size()),
        Ort::Value::CreateTensor<int64_t>(memory_info_, sample_rate_.data(), sample_rate_.size(),
                                          sr_shape.data(), sr_shape.size()),
    };

    auto outputs = session_->Run(Ort::RunOptions{nullptr}, kInputNames, inputs.data(),
                                 inputs.size(), kOutputNames, 2);

    const float probability = outputs[0].GetTensorData<float>()[0];

    // Keep the new recurrent state for the next call...
    const float* new_state = outputs[1].GetTensorData<float>();
    std::copy(new_state, new_state + kStateSize, state_.begin());

    // ...and the last 64 samples of this window as the next call's context.
    std::copy(input_.end() - static_cast<std::ptrdiff_t>(kContextSamples), input_.end(),
              input_.begin());

    return probability;
}

void SileroVad::reset() {
    std::fill(state_.begin(), state_.end(), 0.0f);
    std::fill(input_.begin(), input_.end(), 0.0f);
}

std::string SileroVad::backendDescription() const {
    return std::string("CPU (ONNX Runtime ") + Ort::GetVersionString() + ")";
}

}  // namespace echo
