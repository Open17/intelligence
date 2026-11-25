// =============================================================================
//  endpointer.cpp - see endpointer.hpp
// =============================================================================
#include "core/endpointer.hpp"

#include <algorithm>

namespace echo {

const char* toString(EndpointEvent event) {
    switch (event) {
        case EndpointEvent::None: return "None";
        case EndpointEvent::SpeechStarted: return "SpeechStarted";
        case EndpointEvent::SpeechResumed: return "SpeechResumed";
        case EndpointEvent::SpeculativePause: return "SpeculativePause";
        case EndpointEvent::PauseDetected: return "PauseDetected";
        case EndpointEvent::SilenceTimeout: return "SilenceTimeout";
        case EndpointEvent::MaxLengthReached: return "MaxLengthReached";
        case EndpointEvent::TooShort: return "TooShort";
    }
    return "?";
}

Endpointer::Endpointer(const EndpointerConfig& config) : config_(config) {
    // Convert every millisecond setting into a whole number of frames once,
    // rounding up so e.g. 700 ms with 32 ms frames = 22 frames (704 ms).
    pause_frames_ = framesFor(config_.pause_ms);
    force_frames_ = std::max(framesFor(config_.force_ms), pause_frames_ + 1);
    // Speculation only makes sense BEFORE the real pause point.
    speculative_frames_ = config_.speculative_ms > 0 ? framesFor(config_.speculative_ms) : 0;
    if (speculative_frames_ >= pause_frames_) speculative_frames_ = 0;
    min_speech_frames_ = framesFor(config_.min_speech_ms);
    max_samples_ = static_cast<std::size_t>(config_.max_utterance_ms) *
                   static_cast<std::size_t>(config_.sample_rate) / 1000;
    pre_roll_samples_ = static_cast<std::size_t>(config_.pre_roll_ms) *
                        static_cast<std::size_t>(config_.sample_rate) / 1000;
}

int Endpointer::framesFor(int ms) const {
    return std::max(1, (ms + config_.frame_ms - 1) / config_.frame_ms);
}

void Endpointer::rememberPreRoll(const float* samples, std::size_t count) {
    pre_roll_.insert(pre_roll_.end(), samples, samples + count);
    while (pre_roll_.size() > pre_roll_samples_) {
        pre_roll_.pop_front();
    }
}

EndpointEvent Endpointer::processFrame(const float* samples, std::size_t count,
                                       float speech_probability) {
    // ---- Idle: waiting for speech to begin ----------------------------------
    if (!in_utterance_) {
        if (speech_probability < config_.speech_threshold) {
            rememberPreRoll(samples, count);
            return EndpointEvent::None;
        }
        // Speech! Start the utterance with the pre-roll so the first sound
        // (which the VAD may only notice half-way through) is not cut off.
        in_utterance_ = true;
        is_speech_ = true;
        pause_reported_ = false;
        speech_frames_ = 1;
        silence_frames_ = 0;
        ++revision_;
        audio_.assign(pre_roll_.begin(), pre_roll_.end());
        audio_.insert(audio_.end(), samples, samples + count);
        pre_roll_.clear();
        return EndpointEvent::SpeechStarted;
    }

    // ---- In an utterance ------------------------------------------------------
    audio_.insert(audio_.end(), samples, samples + count);

    // Hysteresis: the bar is lower to STAY in speech than to get back into it.
    const float threshold = is_speech_ ? config_.silence_threshold : config_.speech_threshold;
    const bool speech = speech_probability >= threshold;

    EndpointEvent event = EndpointEvent::None;

    if (speech) {
        if (!is_speech_) {
            // Talking again after a gap: any transcript made so far is stale.
            ++revision_;
            pause_reported_ = false;
            event = EndpointEvent::SpeechResumed;
        }
        is_speech_ = true;
        silence_frames_ = 0;
        ++speech_frames_;
    } else {
        is_speech_ = false;
        ++silence_frames_;

        if (silence_frames_ == speculative_frames_ && !pause_reported_ &&
            speech_frames_ >= min_speech_frames_) {
            event = EndpointEvent::SpeculativePause;
        } else if (silence_frames_ == pause_frames_ && !pause_reported_) {
            if (speech_frames_ < min_speech_frames_) {
                reset();
                return EndpointEvent::TooShort;
            }
            pause_reported_ = true;
            event = EndpointEvent::PauseDetected;
        } else if (silence_frames_ >= force_frames_) {
            return EndpointEvent::SilenceTimeout;  // caller calls takeAudio()
        }
    }

    if (audio_.size() >= max_samples_) {
        return EndpointEvent::MaxLengthReached;  // caller calls takeAudio()
    }
    return event;
}

std::vector<float> Endpointer::takeAudio() {
    std::vector<float> out = std::move(audio_);
    reset();
    return out;
}

void Endpointer::reset() {
    audio_.clear();
    pre_roll_.clear();
    in_utterance_ = false;
    is_speech_ = false;
    pause_reported_ = false;
    speech_frames_ = 0;
    silence_frames_ = 0;
    // revision_ is deliberately NOT reset: it must never repeat, so a late
    // transcript from an old utterance can never match a new one.
}

}  // namespace echo
