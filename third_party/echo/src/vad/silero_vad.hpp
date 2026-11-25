// =============================================================================
//  silero_vad.hpp - voice activity detection with the Silero VAD ONNX model
// =============================================================================
//
//  Silero VAD looks at a short window of audio and returns the probability
//  (0..1) that it contains human speech. It is a small recurrent network: it
//  carries a hidden "state" from one window to the next, so it must be fed
//  consecutive windows in order, and reset() between unrelated streams.
//
//  Model contract (v5/v6, 16 kHz):
//    inputs   "input"  float[1, 64 + 512]  64 samples of context + 512 new ones
//             "state"  float[2, 1, 128]    recurrent state from the last call
//             "sr"     int64[1]            sample rate (16000)
//    outputs  "output" float[1, 1]         speech probability
//             "stateN" float[2, 1, 128]    the new state to pass next time
//
//  512 samples at 16 kHz = 32 ms. That is the frame size the whole endpointing
//  pipeline uses (Silero only supports this window size at 16 kHz).
// =============================================================================
#pragma once

#include <onnxruntime_cxx_api.h>

#include <array>
#include <memory>
#include <string>
#include <vector>

namespace echo {

class SileroVad {
public:
    static constexpr int kSampleRate = 16000;
    static constexpr std::size_t kWindowSamples = 512;  // 32 ms per frame
    static constexpr std::size_t kContextSamples = 64;  // carried over from the previous frame
    static constexpr int kFrameMs = 32;

    /// Loads the .onnx model. Throws std::runtime_error (or Ort::Exception) on failure.
    explicit SileroVad(const std::string& model_path);

    /// Runs the model on exactly kWindowSamples samples.
    /// @return speech probability in [0, 1]
    float process(const float* window);

    /// Forgets all history (state + context). Call before an unrelated stream.
    void reset();

    /// Where the model runs, for display ("CPU (ONNX Runtime 1.32.0)").
    std::string backendDescription() const;

private:
    Ort::Env env_;
    Ort::SessionOptions options_;
    std::unique_ptr<Ort::Session> session_;  // unique_ptr: Session has no default constructor
    Ort::MemoryInfo memory_info_;

    std::vector<float> input_;    // context + window, fed to the model
    std::vector<float> state_;    // 2 x 1 x 128 recurrent state
    std::array<int64_t, 1> sample_rate_{kSampleRate};
};

}  // namespace echo
