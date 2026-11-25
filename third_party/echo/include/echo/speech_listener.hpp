// =============================================================================
//  echo/speech_listener.hpp - the main entry point of the library
// =============================================================================
//
//      #include <echo/echo.hpp>
//
//      echo::SpeechListener listener(echo::SpeechListenerConfig{});
//      listener.setOnSentence([](const std::string& text) {
//          std::cout << "You said: " << text << '\n';   // e.g. send to an AI API
//      });
//      listener.start();
//      ...                                             // your program runs
//      listener.stop();
//
//  HOW IT WORKS (one thread per stage, connected by queues):
//
//   mic callback ──ring buffer──► VAD thread ──jobs──► transcription thread
//   (miniaudio)   (lock-free)     Silero VAD +          whisper.cpp
//                                 endpointing  ◄─results─┘
//                                      │ finished sentence
//                                      ▼
//                                 output thread ──► your callback
//                                 (+ optional grammar correction, llama.cpp)
//
//  Callbacks run on the output thread, never on the audio thread, so a slow
//  callback (e.g. a network request) cannot cause dropped audio - but it does
//  delay the NEXT sentence's callback. Hand long work off to your own thread
//  if that matters.
// =============================================================================
#pragma once

#include <cstddef>
#include <functional>
#include <memory>
#include <string>
#include <vector>

#include "echo/config.hpp"

namespace echo {

/// How long each stage took for one sentence, in milliseconds. All measured
/// from "end of speech" = the last audio frame the VAD classified as speech.
struct SentenceTimings {
    double silence_wait_ms = 0;   ///< end of speech -> transcription started (~speculative_ms)
    double transcription_ms = 0;  ///< running whisper
    double hold_ms = 0;           ///< transcription done -> sentence accepted: waiting for
                                  ///< pause_ms (speculative), or until force_ms if the
                                  ///< text looked unfinished
    double grammar_ms = 0;        ///< delay added by grammar correction after the sentence was
                                  ///< accepted (~0 when precomputed during the pause; 0 if off)
    double total_ms = 0;          ///< end of speech -> your callback is called
};

/// One finished sentence plus details about how it was produced.
struct Sentence {
    std::string text;          ///< final text
    std::string raw_text;      ///< whisper's text before filler removal / grammar correction
    bool forced = false;       ///< true if emitted by the silence timeout / length cap
                               ///< instead of ending in . ? !
    double audio_ms = 0;       ///< length of the audio that was transcribed
    SentenceTimings timings;
};

/// Where each model ended up running, e.g. for printing at startup.
struct BackendInfo {
    std::string vad;            ///< e.g. "CPU (ONNX Runtime 1.32.0)"
    std::string transcription;  ///< e.g. "GPU - Metal: MTL0 (Apple M4)"
    std::string grammar;        ///< e.g. "CPU - 4 threads", or "disabled"
};

class SpeechListener {
public:
    using SentenceCallback = std::function<void(const std::string&)>;
    using DetailedSentenceCallback = std::function<void(const Sentence&)>;

    /// Loads all models (this can take a second or two). Throws
    /// std::runtime_error if a model file is missing or cannot be loaded.
    explicit SpeechListener(SpeechListenerConfig config = {});

    /// Stops listening (if needed) and frees everything.
    ~SpeechListener();

    // A listener owns threads and a microphone: it cannot be copied.
    SpeechListener(const SpeechListener&) = delete;
    SpeechListener& operator=(const SpeechListener&) = delete;

    /// Called with the text of each finished sentence.
    void setOnSentence(SentenceCallback callback);

    /// Like setOnSentence, but with timings and other details. Both callbacks
    /// may be set; they are called one after the other.
    void setOnSentenceDetailed(DetailedSentenceCallback callback);

    /// Starts the worker threads and (unless use_microphone is false) the
    /// microphone. Throws std::runtime_error if the microphone cannot be opened.
    void start();

    /// Stops the microphone and joins all threads. Sentences still being
    /// processed are dropped. Safe to call more than once; start() works again after.
    void stop();

    bool isRunning() const;

    /// Feeds 16 kHz mono float samples when config.use_microphone is false
    /// (e.g. from a file or network stream). Call from ONE thread only. Never
    /// blocks; samples are dropped if the pipeline falls >10 s behind.
    void pushAudio(const float* samples, std::size_t count);

    BackendInfo backendInfo() const;

    /// Name of the microphone in use (after start()).
    std::string inputDeviceName() const;

    const SpeechListenerConfig& config() const;

    /// Names of the available microphones, for SpeechListenerConfig::capture_device.
    static std::vector<std::string> listInputDevices();

    /// True if this build includes the grammar-correction stage
    /// (CMake option ECHO_ENABLE_GRAMMAR).
    static bool grammarCorrectionAvailable();

private:
    // "Pointer to implementation": all the threads, models and third-party
    // types live in speech_listener.cpp, so including this header stays cheap
    // and does not pull in whisper.h, onnxruntime, miniaudio, ...
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace echo
