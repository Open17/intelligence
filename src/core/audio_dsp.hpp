// =============================================================================
//  audio_dsp.hpp - small signal-processing helpers for cleaner capture
// =============================================================================
//
//  HighPassFilter removes everything below ~80 Hz: DC offset (a constant
//  shift some cheap USB microphones add), mains hum (50/60 Hz), desk thumps
//  and rumble. None of that is speech, but it confuses the VAD and whisper.
//
//  normalizePeak() scales an utterance so it is neither too quiet nor clipped
//  before transcription.
// =============================================================================
#pragma once

#include <cstddef>
#include <vector>

namespace echo {

/// A 2nd-order Butterworth high-pass filter ("biquad", from the classic
/// Audio EQ Cookbook by Robert Bristow-Johnson). Keeps state between calls,
/// so feed it consecutive blocks of one stream.
class HighPassFilter {
public:
    HighPassFilter(float cutoff_hz, float sample_rate);

    /// Filters `count` samples in place.
    void process(float* samples, std::size_t count);

    /// Forgets the filter's memory (call between unrelated streams).
    void reset();

private:
    // y[n] = b0*x[n] + b1*x[n-1] + b2*x[n-2] - a1*y[n-1] - a2*y[n-2]
    float b0_, b1_, b2_, a1_, a2_;
    float x1_ = 0, x2_ = 0, y1_ = 0, y2_ = 0;  // previous inputs / outputs
};

/// Scales `audio` so its loudest sample reaches `target_peak` (0..1). The gain
/// is limited to `max_gain` so near-silence is not blown up into loud noise.
/// Returns the gain that was applied.
float normalizePeak(std::vector<float>& audio, float target_peak = 0.9f, float max_gain = 31.6f);

}  // namespace echo
