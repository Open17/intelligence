// Unit tests for the capture DSP helpers (src/core/audio_dsp.hpp).
#include "core/audio_dsp.hpp"

#include <cmath>
#include <vector>

#include "test_framework.hpp"

using echo::HighPassFilter;

namespace {

constexpr float kRate = 16000.0f;
constexpr float kPi = 3.14159265f;

std::vector<float> sine(float freq, float amplitude, std::size_t n, float offset = 0.0f) {
    std::vector<float> x(n);
    for (std::size_t i = 0; i < n; ++i) {
        x[i] = offset + amplitude * std::sin(2.0f * kPi * freq * static_cast<float>(i) / kRate);
    }
    return x;
}

// Peak amplitude over the second half (after the filter has settled).
float settledPeak(const std::vector<float>& x) {
    float peak = 0.0f;
    for (std::size_t i = x.size() / 2; i < x.size(); ++i) peak = std::max(peak, std::fabs(x[i]));
    return peak;
}

}  // namespace

TEST_CASE(highpass_removes_dc_offset) {
    std::vector<float> x(16000, 0.25f);  // pure DC
    HighPassFilter(80.0f, kRate).process(x.data(), x.size());
    CHECK(settledPeak(x) < 1e-3f);
}

TEST_CASE(highpass_attenuates_mains_hum) {
    auto x = sine(50.0f, 1.0f, 16000);
    HighPassFilter(80.0f, kRate).process(x.data(), x.size());
    CHECK(settledPeak(x) < 0.45f);  // ~ -9 dB at 50 Hz
}

TEST_CASE(highpass_keeps_speech_frequencies) {
    for (float freq : {300.0f, 1000.0f, 3000.0f}) {
        auto x = sine(freq, 0.5f, 16000, /*offset=*/0.2f);
        HighPassFilter(80.0f, kRate).process(x.data(), x.size());
        const float peak = settledPeak(x);
        CHECK(peak > 0.48f && peak < 0.52f);  // amplitude kept, offset gone
    }
}

TEST_CASE(highpass_state_carries_across_blocks) {
    auto whole = sine(440.0f, 0.5f, 4096, 0.1f);
    auto blocks = whole;
    HighPassFilter a(80.0f, kRate), b(80.0f, kRate);
    a.process(whole.data(), whole.size());
    for (std::size_t i = 0; i < blocks.size(); i += 512) b.process(blocks.data() + i, 512);
    float max_diff = 0.0f;
    for (std::size_t i = 0; i < whole.size(); ++i) max_diff = std::max(max_diff, std::fabs(whole[i] - blocks[i]));
    CHECK(max_diff < 1e-6f);
}

TEST_CASE(normalize_raises_quiet_audio) {
    auto x = sine(440.0f, 0.03f, 1600);
    const float gain = echo::normalizePeak(x);
    CHECK(gain > 29.0f && gain < 31.0f);
    CHECK(std::fabs(settledPeak(x) - 0.9f) < 0.01f);
}

TEST_CASE(normalize_gain_is_capped) {
    auto x = sine(440.0f, 0.0001f, 1600);
    CHECK(std::fabs(echo::normalizePeak(x, 0.9f, 10.0f) - 10.0f) < 1e-4f);
}

TEST_CASE(normalize_lowers_hot_audio_and_ignores_silence) {
    auto loud = sine(440.0f, 1.0f, 1600);
    echo::normalizePeak(loud);
    CHECK(settledPeak(loud) <= 0.9001f);
    std::vector<float> silence(100, 0.0f);
    CHECK_EQ(echo::normalizePeak(silence), 1.0f);
}

int main() { return echo::test::runAll(); }
