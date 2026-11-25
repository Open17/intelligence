// Unit tests for the Endpointer state machine (src/core/endpointer.hpp).
//
// The tests feed hand-written sequences of VAD probabilities - no real audio
// or model is needed, which is exactly why the endpointer is kept separate
// from threads and models.
#include "core/endpointer.hpp"

#include <vector>

#include "test_framework.hpp"

using echo::EndpointEvent;
using echo::Endpointer;
using echo::EndpointerConfig;

namespace {

constexpr std::size_t kFrame = 512;  // samples per 32 ms frame
const std::vector<float> kAudio(kFrame, 0.1f);

// Feeds `frames` frames with probability `p`; returns the LAST non-None event.
EndpointEvent feed(Endpointer& ep, int frames, float p) {
    EndpointEvent last = EndpointEvent::None;
    for (int i = 0; i < frames; ++i) {
        const EndpointEvent e = ep.processFrame(kAudio.data(), kFrame, p);
        if (e != EndpointEvent::None) last = e;
    }
    return last;
}

// Feeds frames one by one and returns how many it took until `wanted` fired
// (or -1 if it never did within `max_frames`).
int framesUntil(Endpointer& ep, EndpointEvent wanted, float p, int max_frames = 1000) {
    for (int i = 1; i <= max_frames; ++i) {
        if (ep.processFrame(kAudio.data(), kFrame, p) == wanted) return i;
    }
    return -1;
}

}  // namespace

TEST_CASE(silence_stays_idle) {
    Endpointer ep;
    CHECK(feed(ep, 100, 0.1f) == EndpointEvent::None);
    CHECK(!ep.inUtterance());
}

TEST_CASE(speech_starts_utterance_with_pre_roll) {
    EndpointerConfig cfg;
    cfg.pre_roll_ms = 64;  // 2 frames
    Endpointer ep(cfg);
    feed(ep, 10, 0.0f);
    CHECK(ep.processFrame(kAudio.data(), kFrame, 0.9f) == EndpointEvent::SpeechStarted);
    CHECK(ep.inUtterance());
    // 1024 samples of pre-roll (64 ms at 16 kHz) + the speech frame itself.
    CHECK_EQ(ep.audio().size(), 1024u + kFrame);
}

TEST_CASE(pause_fires_after_700ms) {
    Endpointer ep;
    feed(ep, 20, 0.9f);  // 640 ms of speech
    // ceil(700 / 32) = 22 frames of silence.
    CHECK_EQ(framesUntil(ep, EndpointEvent::PauseDetected, 0.0f), 22);
    CHECK(ep.pauseDetected());
}

TEST_CASE(timeout_fires_after_2s_of_silence) {
    Endpointer ep;
    feed(ep, 20, 0.9f);
    // ceil(2000 / 32) = 63 frames of silence in total; PauseDetected is on the way.
    CHECK_EQ(framesUntil(ep, EndpointEvent::SilenceTimeout, 0.0f), 63);
    const auto audio = ep.takeAudio();
    CHECK(!audio.empty());
    CHECK(!ep.inUtterance());
}

TEST_CASE(resuming_speech_bumps_revision) {
    Endpointer ep;
    feed(ep, 20, 0.9f);
    const auto rev = ep.revision();
    feed(ep, 22, 0.0f);  // pause detected
    CHECK(ep.processFrame(kAudio.data(), kFrame, 0.9f) == EndpointEvent::SpeechResumed);
    CHECK(ep.revision() != rev);
    CHECK(!ep.pauseDetected());
    // The pause timer starts over.
    CHECK_EQ(framesUntil(ep, EndpointEvent::PauseDetected, 0.0f), 22);
}

TEST_CASE(short_noise_is_discarded) {
    Endpointer ep;  // min_speech_ms = 200 -> 7 frames
    feed(ep, 3, 0.9f);
    CHECK(feed(ep, 22, 0.0f) == EndpointEvent::TooShort);
    CHECK(!ep.inUtterance());
}

TEST_CASE(hysteresis_keeps_speech_between_thresholds) {
    Endpointer ep;
    // 0.4 is below the start threshold (0.5): does not start speech...
    CHECK(feed(ep, 5, 0.4f) == EndpointEvent::None);
    CHECK(!ep.inUtterance());
    // ...but once talking, 0.4 is above the stay threshold (0.35).
    feed(ep, 1, 0.9f);
    feed(ep, 30, 0.4f);
    CHECK(ep.isSpeech());
    CHECK_EQ(ep.silenceMs(), 0);
}

TEST_CASE(max_length_is_enforced) {
    EndpointerConfig cfg;
    cfg.max_utterance_ms = 3200;  // 100 frames
    Endpointer ep(cfg);
    CHECK_EQ(framesUntil(ep, EndpointEvent::MaxLengthReached, 0.9f), 100);
    CHECK_EQ(ep.takeAudio().size(), 100u * kFrame);
}

TEST_CASE(revision_never_repeats_after_reset) {
    Endpointer ep;
    feed(ep, 10, 0.9f);
    const auto first = ep.revision();
    ep.reset();
    feed(ep, 10, 0.9f);
    CHECK(ep.revision() > first);
}

TEST_CASE(thresholds_are_configurable) {
    EndpointerConfig cfg;
    cfg.pause_ms = 320;   // 10 frames
    cfg.force_ms = 640;   // 20 frames
    Endpointer ep(cfg);
    feed(ep, 10, 0.9f);
    CHECK_EQ(framesUntil(ep, EndpointEvent::PauseDetected, 0.0f), 10);
    CHECK_EQ(framesUntil(ep, EndpointEvent::SilenceTimeout, 0.0f), 10);
}

TEST_CASE(speculative_pause_fires_before_the_real_pause) {
    Endpointer ep;  // speculative 300 ms -> 10 frames, pause 700 ms -> 22 frames
    feed(ep, 20, 0.9f);
    CHECK_EQ(framesUntil(ep, EndpointEvent::SpeculativePause, 0.0f), 10);
    CHECK(!ep.pauseDetected());
    CHECK_EQ(framesUntil(ep, EndpointEvent::PauseDetected, 0.0f), 12);
}

TEST_CASE(speculative_pause_fires_again_after_resuming) {
    Endpointer ep;
    feed(ep, 20, 0.9f);
    feed(ep, 12, 0.0f);   // speculative pause, but not the real one
    feed(ep, 5, 0.9f);    // talking again
    CHECK_EQ(framesUntil(ep, EndpointEvent::SpeculativePause, 0.0f), 10);
}

TEST_CASE(speculative_pause_can_be_disabled) {
    EndpointerConfig cfg;
    cfg.speculative_ms = 0;
    Endpointer ep(cfg);
    feed(ep, 20, 0.9f);
    CHECK_EQ(framesUntil(ep, EndpointEvent::SpeculativePause, 0.0f, 100), -1);
}

TEST_CASE(no_speculation_for_too_short_sounds) {
    Endpointer ep;
    feed(ep, 3, 0.9f);  // below min_speech_ms
    CHECK_EQ(framesUntil(ep, EndpointEvent::SpeculativePause, 0.0f, 30), -1);
}

int main() { return echo::test::runAll(); }
