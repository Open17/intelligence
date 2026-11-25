// =============================================================================
//  endpointer.hpp - decides when the speaker has started and stopped talking
// =============================================================================
//
//  The endpointer is a small "state machine" fed one audio frame at a time
//  together with the VAD's speech probability for that frame. It buffers the
//  utterance audio and tells the caller, via the returned event, when
//  something interesting happens:
//
//     silence ──speech──► SpeechStarted ──► (talking) ──silence 300 ms──►
//     SpeculativePause  ── caller may start transcribing early (optional) ──►
//     ──silence 700 ms──►
//     PauseDetected  ── caller transcribes; if the text ends in . ? ! the
//                       caller emits it and calls reset()
//                   ──speech again──► SpeechResumed (the pause was a breath)
//                   ──silence reaches 2 s──► SilenceTimeout: emit anyway
//
//  It knows nothing about threads, models or clocks - it only counts frames.
//  That makes it easy to unit-test (see tests/test_endpointer.cpp).
//
//  Hysteresis: to START speech the probability must reach `speech_threshold`
//  (0.5), but once talking it only counts as silence when it drops below the
//  lower `silence_threshold` (0.35). This stops the state flickering when the
//  probability hovers around one value.
// =============================================================================
#pragma once

#include <cstddef>
#include <cstdint>
#include <deque>
#include <vector>

namespace echo {

struct EndpointerConfig {
    int sample_rate = 16000;
    int frame_ms = 32;                ///< duration of one frame (Silero VAD: 512 samples)
    float speech_threshold = 0.5f;    ///< VAD probability needed to START speech
    float silence_threshold = 0.35f;  ///< below this, an ongoing utterance is silent
    int pre_roll_ms = 300;            ///< audio kept from before speech starts (catches soft onsets)
    int min_speech_ms = 200;          ///< shorter utterances are discarded as clicks/noise
    int speculative_ms = 300;         ///< silence before an EARLY transcription (0 = off)
    int pause_ms = 700;               ///< silence before we transcribe and check punctuation
    int force_ms = 2000;              ///< silence after which we emit even without punctuation
    int max_utterance_ms = 28000;     ///< hard cap (whisper's window is 30 s)
};

enum class EndpointEvent {
    None,              ///< nothing new
    SpeechStarted,     ///< a new utterance began
    SpeechResumed,     ///< speech continued after some silence (revision changed)
    SpeculativePause,  ///< speculative_ms of silence: a good moment to start transcribing
    PauseDetected,     ///< pause_ms of silence: transcribe the utterance now
    SilenceTimeout,    ///< force_ms of silence: emit whatever we have
    MaxLengthReached,  ///< utterance hit max_utterance_ms: transcribe and emit
    TooShort,          ///< utterance was shorter than min_speech_ms: discarded
};

const char* toString(EndpointEvent event);

class Endpointer {
public:
    explicit Endpointer(const EndpointerConfig& config = {});

    /// Feeds one frame of audio and its speech probability (0..1).
    EndpointEvent processFrame(const float* samples, std::size_t count, float speech_probability);

    /// Moves the buffered utterance audio out and goes back to idle.
    /// Call this on SilenceTimeout and MaxLengthReached.
    std::vector<float> takeAudio();

    /// Drops the current utterance and goes back to idle (e.g. after the
    /// caller emitted a finished sentence).
    void reset();

    bool inUtterance() const { return in_utterance_; }
    bool isSpeech() const { return is_speech_; }          ///< latest frame counted as speech
    bool pauseDetected() const { return pause_reported_; }  ///< waiting after PauseDetected
    const std::vector<float>& audio() const { return audio_; }

    /// Changes whenever the utterance gains new speech. A transcript made from
    /// an older revision is out of date.
    std::uint64_t revision() const { return revision_; }

    int silenceMs() const { return silence_frames_ * config_.frame_ms; }
    int speechMs() const { return speech_frames_ * config_.frame_ms; }

    const EndpointerConfig& config() const { return config_; }

private:
    int framesFor(int ms) const;
    void rememberPreRoll(const float* samples, std::size_t count);

    EndpointerConfig config_;
    int speculative_frames_;  // 0 = disabled
    int pause_frames_;
    int force_frames_;
    int min_speech_frames_;
    std::size_t max_samples_;
    std::size_t pre_roll_samples_;

    std::deque<float> pre_roll_;   // recent audio while idle
    std::vector<float> audio_;     // the current utterance
    bool in_utterance_ = false;
    bool is_speech_ = false;
    bool pause_reported_ = false;
    int speech_frames_ = 0;
    int silence_frames_ = 0;
    std::uint64_t revision_ = 0;
};

}  // namespace echo
