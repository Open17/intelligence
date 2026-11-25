// =============================================================================
//  audio_dsp.cpp - see audio_dsp.hpp
// =============================================================================
#include "core/audio_dsp.hpp"

#include <algorithm>
#include <cmath>

namespace echo {

HighPassFilter::HighPassFilter(float cutoff_hz, float sample_rate) {
    // Audio EQ Cookbook high-pass with Q = 1/sqrt(2) (Butterworth: flat
    // pass-band, no resonance peak).
    const double pi = 3.14159265358979323846;
    const double w0 = 2.0 * pi * cutoff_hz / sample_rate;
    const double cos_w0 = std::cos(w0);
    const double alpha = std::sin(w0) / (2.0 * 0.7071067811865476);
    const double a0 = 1.0 + alpha;

    // Divide everything by a0 once so the per-sample loop has no division.
    b0_ = static_cast<float>(((1.0 + cos_w0) / 2.0) / a0);
    b1_ = static_cast<float>((-(1.0 + cos_w0)) / a0);
    b2_ = static_cast<float>(((1.0 + cos_w0) / 2.0) / a0);
    a1_ = static_cast<float>((-2.0 * cos_w0) / a0);
    a2_ = static_cast<float>((1.0 - alpha) / a0);
}

void HighPassFilter::process(float* samples, std::size_t count) {
    for (std::size_t i = 0; i < count; ++i) {
        const float x = samples[i];
        const float y = b0_ * x + b1_ * x1_ + b2_ * x2_ - a1_ * y1_ - a2_ * y2_;
        x2_ = x1_;
        x1_ = x;
        y2_ = y1_;
        y1_ = y;
        samples[i] = y;
    }
}

void HighPassFilter::reset() { x1_ = x2_ = y1_ = y2_ = 0.0f; }

float normalizePeak(std::vector<float>& audio, float target_peak, float max_gain) {
    float peak = 0.0f;
    for (float s : audio) peak = std::max(peak, std::fabs(s));
    if (peak <= 0.0f) return 1.0f;
    const float gain = std::min(target_peak / peak, max_gain);
    for (float& s : audio) s *= gain;
    return gain;
}

}  // namespace echo
