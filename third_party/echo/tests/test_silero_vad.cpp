// Tests for SileroVad with the real model (skipped if models/silero_vad.onnx is missing).
//
// Usage: test_silero_vad <models-dir>
#include "vad/silero_vad.hpp"

#include <cmath>
#include <filesystem>
#include <random>
#include <vector>

#include "test_framework.hpp"

namespace {
std::string g_model_path;
constexpr int kSkip = 77;  // CTest's SKIP_RETURN_CODE
}  // namespace

TEST_CASE(silence_is_not_speech) {
    echo::SileroVad vad(g_model_path);
    std::vector<float> silence(echo::SileroVad::kWindowSamples, 0.0f);
    float max_prob = 0.0f;
    for (int i = 0; i < 30; ++i) max_prob = std::max(max_prob, vad.process(silence.data()));
    CHECK(max_prob < 0.1f);
}

TEST_CASE(white_noise_is_not_speech) {
    echo::SileroVad vad(g_model_path);
    std::mt19937 rng(42);
    std::uniform_real_distribution<float> dist(-0.05f, 0.05f);
    std::vector<float> noise(echo::SileroVad::kWindowSamples);
    float sum = 0.0f;
    for (int i = 0; i < 30; ++i) {
        for (float& s : noise) s = dist(rng);
        sum += vad.process(noise.data());
    }
    CHECK(sum / 30.0f < 0.5f);
}

TEST_CASE(probability_is_in_range_and_reset_works) {
    echo::SileroVad vad(g_model_path);
    std::vector<float> tone(echo::SileroVad::kWindowSamples);
    for (std::size_t i = 0; i < tone.size(); ++i) {
        tone[i] = 0.3f * std::sin(2.0f * 3.14159f * 220.0f * static_cast<float>(i) / 16000.0f);
    }
    const float p = vad.process(tone.data());
    CHECK(p >= 0.0f && p <= 1.0f);

    // After reset() the model must behave exactly like a fresh one.
    vad.reset();
    echo::SileroVad fresh(g_model_path);
    CHECK(std::fabs(vad.process(tone.data()) - fresh.process(tone.data())) < 1e-6f);
}

int main(int argc, char** argv) {
    const std::filesystem::path models = argc > 1 ? argv[1] : "models";
    g_model_path = (models / "silero_vad.onnx").string();
    if (!std::filesystem::exists(g_model_path)) {
        std::cout << "skipping: " << g_model_path << " not found\n";
        return kSkip;
    }
    return echo::test::runAll();
}
