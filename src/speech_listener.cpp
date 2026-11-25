// =============================================================================
//  speech_listener.cpp - wires capture, VAD, endpointing and whisper together
// =============================================================================
//
//  Three worker threads (plus miniaudio's own audio thread):
//
//   1. VAD thread         reads 32 ms frames from the ring buffer, runs Silero
//                         VAD and the Endpointer, and decides WHEN to
//                         transcribe and WHEN a sentence is finished.
//   2. Transcription      runs whisper.cpp on jobs from the VAD thread and
//      thread             sends the text back.
//   3. Output thread      cleans up finished sentences (filler removal, optional
//                         grammar correction - usually precomputed during the
//                         pause) and calls the user's callbacks.
//
//  The VAD thread is the only one that touches the Endpointer, so the
//  endpointing logic itself needs no locks. Threads talk only through queues.
//
//  Life of a sentence ("Turn on the lights."):
//    speech frames ... last speech frame (t=0)
//    t=320 ms  SpeculativePause: start transcribing EARLY (speculatively)
//    t~450 ms  whisper returns "Turn on the lights." - held, not emitted yet
//    t=704 ms  PauseDetected: still silent, so the early transcript is valid
//              and ends in '.' -> emit immediately (no transcription wait)
//    Had the speaker continued at t=500 ms, the early transcript would be
//    out of date (new revision) and is thrown away, or the running whisper
//    job is cancelled. If the text had been "Turn on the" we would keep
//    waiting: more speech makes it stale; 2 s of silence emits it anyway.
// =============================================================================
#include "echo/speech_listener.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <iostream>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <thread>

#include "audio/audio_capture.hpp"
#include "core/audio_dsp.hpp"
#include "core/blocking_queue.hpp"
#include "core/endpointer.hpp"
#include "core/ring_buffer.hpp"
#include "core/text_utils.hpp"
#include "grammar/grammar_corrector.hpp"
#include "stt/whisper_transcriber.hpp"
#include "util/ggml_utils.hpp"
#include "vad/silero_vad.hpp"

namespace echo {

const char* toString(ComputeBackend backend) {
    switch (backend) {
        case ComputeBackend::Auto: return "auto";
        case ComputeBackend::Gpu: return "gpu";
        case ComputeBackend::Cpu: return "cpu";
    }
    return "?";
}

namespace {

using Clock = std::chrono::steady_clock;
using TimePoint = Clock::time_point;

constexpr int kSampleRate = SileroVad::kSampleRate;
constexpr std::size_t kRingCapacity = kSampleRate * 10;  // 10 s of audio

double msBetween(TimePoint from, TimePoint to) {
    return std::chrono::duration<double, std::milli>(to - from).count();
}

// --- Messages passed between the threads -----------------------------------------

struct TranscriptionJob {
    std::uint64_t revision = 0;   // Endpointer revision the audio belongs to
    std::vector<float> audio;
    bool force = false;           // emit the result no matter how it ends
    TimePoint speech_end;
};

struct TranscriptionResult {
    std::uint64_t revision = 0;
    std::string text;
    bool force = false;
    bool skipped = false;         // job was out of date and not transcribed
    double audio_ms = 0;
    TimePoint speech_end, started, finished;
};

// Work for the output thread: either a finished sentence, or (precompute)
// an early transcript to grammar-correct ahead of time, during the pause.
struct FinishedSentence {
    std::string text;
    bool precompute = false;      // only warm the grammar cache, don't emit
    std::uint64_t revision = 0;
    bool forced = false;
    double audio_ms = 0;
    TimePoint speech_end, transcribe_start, transcribe_end, decided;
};

EndpointerConfig toEndpointerConfig(const EndpointingConfig& in) {
    EndpointerConfig out;
    out.sample_rate = kSampleRate;
    out.frame_ms = SileroVad::kFrameMs;
    out.speech_threshold = in.speech_threshold;
    out.silence_threshold = in.silence_threshold;
    out.pre_roll_ms = in.pre_roll_ms;
    out.min_speech_ms = in.min_speech_ms;
    out.speculative_ms = in.speculative_ms;
    out.pause_ms = in.pause_ms;
    out.force_ms = in.force_ms;
    out.max_utterance_ms = in.max_utterance_ms;
    return out;
}

}  // namespace

// =============================================================================
//  Impl - the actual state of a SpeechListener
// =============================================================================
struct SpeechListener::Impl {
    explicit Impl(SpeechListenerConfig cfg);

    void vadLoop();
    void transcriptionLoop();
    void outputLoop();

    // VAD-thread helpers.
    void handleResult(const TranscriptionResult& result);
    void handleEvent(EndpointEvent event);
    void submit(std::vector<float> audio, std::uint64_t revision, bool force);
    void decide(const TranscriptionResult& result);
    void emit(const TranscriptionResult& result, bool forced);

    void log(const std::string& message) const {
        if (config.verbose) std::cerr << "[echo] " << message << '\n';
    }

    SpeechListenerConfig config;

    // Models (loaded once in the constructor).
    std::unique_ptr<SileroVad> vad;
    std::unique_ptr<WhisperTranscriber> whisper;
    std::unique_ptr<GrammarCorrector> grammar;  // only when enabled

    // Audio input.
    SpscRingBuffer<float> audio_ring{kRingCapacity};
    std::unique_ptr<AudioCapture> capture;

    // Queues between threads.
    BlockingQueue<TranscriptionJob> jobs;          // VAD -> transcription
    BlockingQueue<TranscriptionResult> results;    // transcription -> VAD
    BlockingQueue<FinishedSentence> sentences;     // VAD -> output

    std::thread vad_thread, transcription_thread, output_thread;
    std::atomic<bool> running{false};

    // Shared between VAD and transcription threads, so atomic: lets the
    // transcription thread skip jobs that became stale while queued.
    std::atomic<std::uint64_t> latest_revision{0};
    std::atomic<std::uint64_t> protected_revision{0};  // never skip this one

    // User callbacks (set from any thread, called from the output thread).
    std::mutex callback_mutex;
    SentenceCallback on_sentence;
    DetailedSentenceCallback on_sentence_detailed;

    // ---- State owned by the output thread ----
    // The last text grammar-corrected ahead of time, and the result.
    std::string grammar_cache_input, grammar_cache_output;

    // ---- State owned by the VAD thread (no locking needed) ----
    std::unique_ptr<Endpointer> endpointer;
    TimePoint last_speech_time;
    std::optional<TranscriptionResult> held;         // unpunctuated text waiting for timeout
    std::optional<std::uint64_t> in_flight;          // revision being transcribed
    std::optional<std::uint64_t> emit_when_ready;    // timed out while still transcribing
};

SpeechListener::Impl::Impl(SpeechListenerConfig cfg) : config(std::move(cfg)) {
    configureGgmlLogging(config.verbose);

    vad = std::make_unique<SileroVad>(config.vad_model_path);

    WhisperConfig wc;
    wc.model_path = config.whisper_model_path;
    wc.language = config.language;
    wc.use_gpu = config.backend != ComputeBackend::Cpu;
    wc.n_threads = config.n_threads;
    wc.audio_ctx = config.whisper_audio_ctx;
    wc.beam_size = config.whisper_beam_size;
    whisper = std::make_unique<WhisperTranscriber>(wc);

    if (config.enable_grammar_correction) {
        // (In a build with ECHO_ENABLE_GRAMMAR=OFF this throws a clear error.)
        GrammarConfig gc;
        gc.model_path = config.grammar_model_path;
        gc.use_gpu = config.backend != ComputeBackend::Cpu;
        gc.n_threads = config.n_threads;
        grammar = std::make_unique<GrammarCorrector>(gc);
    }

    const bool gpu_missing = !whisper->usingGpu() || (grammar && !grammar->usingGpu());
    if (config.backend == ComputeBackend::Gpu && gpu_missing) {
        throw std::runtime_error(
            "backend=gpu was requested but no usable GPU was found "
            "(compiled GPU backend: " + std::string(compiledGpuBackend()) + ")");
    }
}

// -----------------------------------------------------------------------------
//  Thread 1: VAD + endpointing
// -----------------------------------------------------------------------------
void SpeechListener::Impl::vadLoop() {
    endpointer = std::make_unique<Endpointer>(toEndpointerConfig(config.endpointing));
    held.reset();
    in_flight.reset();
    emit_when_ready.reset();

    std::vector<float> frame(SileroVad::kWindowSamples);
    std::size_t filled = 0;

    // Input conditioning: strip DC offset / hum / rumble before anything
    // looks at the audio. The filter keeps state across frames.
    const bool use_highpass = config.highpass_hz > 0;
    HighPassFilter highpass(static_cast<float>(std::max(1, config.highpass_hz)),
                            static_cast<float>(kSampleRate));

    while (running.load()) {
        // 1) Collect any transcripts that came back.
        TranscriptionResult result;
        while (results.tryPop(result)) {
            handleResult(result);
        }

        // 2) Gather exactly one frame of audio. If not enough has arrived yet,
        //    nap briefly: a frame is 32 ms, so a 2 ms poll adds little latency.
        filled += audio_ring.pop(frame.data() + filled, frame.size() - filled);
        if (filled < frame.size()) {
            std::this_thread::sleep_for(std::chrono::milliseconds(2));
            continue;
        }
        filled = 0;

        // 3) Speech probability -> endpointing decision.
        if (use_highpass) highpass.process(frame.data(), frame.size());
        try {
            const float probability = vad->process(frame.data());
            const EndpointEvent event =
                endpointer->processFrame(frame.data(), frame.size(), probability);
            if (endpointer->isSpeech()) {
                last_speech_time = Clock::now();  // "end of speech" for the timings
            }
            if (event != EndpointEvent::None) {
                handleEvent(event);
            }
        } catch (const std::exception& e) {
            std::cerr << "[echo] VAD error: " << e.what() << '\n';
        }
    }
}

void SpeechListener::Impl::handleEvent(EndpointEvent event) {
    log(std::string("event: ") + toString(event) + " (revision " +
        std::to_string(endpointer->revision()) + ")");

    switch (event) {
        case EndpointEvent::SpeechStarted:
        case EndpointEvent::SpeechResumed:
            // New speech makes any held transcript out of date.
            held.reset();
            latest_revision.store(endpointer->revision());
            break;

        case EndpointEvent::SpeculativePause:
            // ~300 ms of silence: start transcribing now so the text is
            // (usually) ready by the time the real pause point is reached.
            submit(endpointer->audio(), endpointer->revision(), /*force=*/false);
            break;

        case EndpointEvent::PauseDetected: {
            // ~700 ms of silence: decide whether the sentence is finished.
            const std::uint64_t revision = endpointer->revision();
            if (held && held->revision == revision) {
                decide(*held);  // the speculative transcript is already here
            } else if (in_flight && *in_flight == revision) {
                // Still transcribing: handleResult() decides when it arrives.
            } else {
                submit(endpointer->audio(), revision, /*force=*/false);
            }
            break;
        }

        case EndpointEvent::SilenceTimeout: {
            // ~2 s of silence: the speaker is done, punctuation or not.
            const std::uint64_t revision = endpointer->revision();
            std::vector<float> audio = endpointer->takeAudio();
            if (held && held->revision == revision) {
                emit(*held, /*forced=*/true);   // we already have the text
                held.reset();
            } else if (in_flight && *in_flight == revision) {
                emit_when_ready = revision;      // whisper is still working on it
                protected_revision.store(revision);
            } else {
                submit(std::move(audio), revision, /*force=*/true);
            }
            break;
        }

        case EndpointEvent::MaxLengthReached: {
            // Very long monologue: transcribe this chunk and start a new one.
            const std::uint64_t revision = endpointer->revision();
            held.reset();
            submit(endpointer->takeAudio(), revision, /*force=*/true);
            break;
        }

        case EndpointEvent::TooShort:  // a click or cough: already discarded
        case EndpointEvent::None:
            break;
    }
}

void SpeechListener::Impl::submit(std::vector<float> audio, std::uint64_t revision, bool force) {
    TranscriptionJob job;
    job.revision = revision;
    job.audio = std::move(audio);
    job.force = force;
    job.speech_end = last_speech_time;
    if (!force) {
        in_flight = revision;
    } else {
        protected_revision.store(revision);
    }
    jobs.push(std::move(job));
}

void SpeechListener::Impl::handleResult(const TranscriptionResult& result) {
    if (in_flight && *in_flight == result.revision) {
        in_flight.reset();
    }

    // Case 1: the silence timeout already fired while this was transcribing.
    if (emit_when_ready && *emit_when_ready == result.revision) {
        emit_when_ready.reset();
        if (!result.text.empty()) emit(result, /*forced=*/true);
        return;
    }

    // Case 2: a forced job (timeout / length cap): always emit.
    if (result.force) {
        if (!result.text.empty()) emit(result, /*forced=*/true);
        return;
    }

    // Case 3: a tentative transcript. Ignore it if the speaker has said more
    // since (different revision) or the utterance ended.
    if (result.skipped || !endpointer->inUtterance() ||
        result.revision != endpointer->revision()) {
        log("dropping stale transcript");
        return;
    }
    // Arrived early (speculative): keep it until the real pause point, where
    // handleEvent(PauseDetected) decides.
    if (!endpointer->pauseDetected()) {
        held = result;
        // If it will probably be emitted, let the output thread run the
        // grammar model on it now, while we wait out the rest of the pause.
        if (grammar && looksFinished(result.text)) {
            FinishedSentence warm;
            warm.precompute = true;
            warm.revision = result.revision;
            warm.text = result.text;
            sentences.push(std::move(warm));
        }
        return;
    }
    decide(result);
}

// The 700 ms pause has been reached and `result` is a valid transcript of the
// utterance: emit it if it looks finished, otherwise hold it.
void SpeechListener::Impl::decide(const TranscriptionResult& result) {
    if (result.text.empty()) {
        log("whisper heard no words; discarding utterance");
        endpointer->reset();
        return;
    }
    if (looksFinished(result.text)) {
        endpointer->reset();  // done - start listening for the next sentence
        emit(result, /*forced=*/false);
        return;
    }
    log("not finished yet: \"" + result.text + "\" - waiting");
    if (!held || held->revision != result.revision) held = result;
}

void SpeechListener::Impl::emit(const TranscriptionResult& result, bool forced) {
    FinishedSentence s;
    s.text = result.text;
    s.forced = forced;
    s.audio_ms = result.audio_ms;
    s.speech_end = result.speech_end;
    s.transcribe_start = result.started;
    s.transcribe_end = result.finished;
    s.decided = Clock::now();
    sentences.push(std::move(s));
}

// -----------------------------------------------------------------------------
//  Thread 2: transcription
// -----------------------------------------------------------------------------
void SpeechListener::Impl::transcriptionLoop() {
    TranscriptionJob job;
    while (jobs.pop(job)) {  // sleeps until there is a job; false on shutdown
        TranscriptionResult result;
        result.revision = job.revision;
        result.force = job.force;
        result.speech_end = job.speech_end;
        result.audio_ms = 1000.0 * static_cast<double>(job.audio.size()) / kSampleRate;

        // A job is out of date once the speaker kept talking (new revision).
        // Checked before starting AND while whisper runs, so stale speculative
        // work is skipped or cancelled instead of delaying the next job.
        auto is_stale = [this, &job] {
            return !running.load() ||
                   (!job.force && job.revision != latest_revision.load() &&
                    job.revision != protected_revision.load());
        };
        result.started = Clock::now();
        if (is_stale()) {
            result.skipped = true;
        } else {
            try {
                if (config.normalize_volume) normalizePeak(job.audio);
                const std::optional<std::string> text = whisper->transcribe(job.audio, is_stale);
                if (text) {
                    result.text = *text;
                } else {
                    result.skipped = true;  // cancelled part-way
                }
            } catch (const std::exception& e) {
                std::cerr << "[echo] transcription error: " << e.what() << '\n';
            }
        }
        result.finished = Clock::now();
        log((result.skipped ? std::string("skipped stale job after ")
                            : "transcribed " + std::to_string(static_cast<int>(result.audio_ms)) +
                                  " ms of audio in ") +
            std::to_string(static_cast<int>(msBetween(result.started, result.finished))) +
            " ms: \"" + result.text + "\"");
        results.push(std::move(result));
    }
}

// -----------------------------------------------------------------------------
//  Thread 3: output (post-processing + user callbacks)
// -----------------------------------------------------------------------------
void SpeechListener::Impl::outputLoop() {
    FinishedSentence s;
    while (sentences.pop(s)) {
        if (s.precompute) {
            // Skip if the speaker has kept talking (the text is out of date).
            if (s.revision != latest_revision.load()) continue;
            const std::string cleaned =
                config.remove_filler_words ? removeFillerWords(s.text) : s.text;
            if (cleaned.empty() || cleaned == grammar_cache_input) continue;
            try {
                grammar_cache_output = grammar->correct(cleaned);
                grammar_cache_input = cleaned;
            } catch (const std::exception& e) {
                std::cerr << "[echo] grammar correction error: " << e.what() << '\n';
            }
            continue;
        }

        Sentence out;
        out.raw_text = s.text;
        out.text = s.text;
        out.forced = s.forced;
        out.audio_ms = s.audio_ms;

        // Rule-based filler removal (microseconds).
        if (config.remove_filler_words) {
            out.text = removeFillerWords(out.text);
            if (out.text.empty()) {
                log("sentence was only filler words; not emitted");
                continue;
            }
        }

        // Optional grammar correction. Usually it was already done during the
        // pause (precompute above); then it costs nothing here.
        if (grammar) {
            if (out.text == grammar_cache_input) {
                out.text = grammar_cache_output;
            } else {
                try {
                    out.text = grammar->correct(out.text);
                } catch (const std::exception& e) {
                    std::cerr << "[echo] grammar correction error: " << e.what() << '\n';
                }
            }
            // Measured from the moment the sentence was accepted, so it also
            // counts any wait behind a precompute that was still running.
            out.timings.grammar_ms = msBetween(s.decided, Clock::now());
        }

        const TimePoint now = Clock::now();
        out.timings.silence_wait_ms = msBetween(s.speech_end, s.transcribe_start);
        out.timings.transcription_ms = msBetween(s.transcribe_start, s.transcribe_end);
        out.timings.hold_ms = std::max(0.0, msBetween(s.transcribe_end, s.decided));
        out.timings.total_ms = msBetween(s.speech_end, now);

        // Copy the callbacks under the lock, then call them WITHOUT holding it,
        // so a callback may safely call setOnSentence() itself.
        SentenceCallback simple;
        DetailedSentenceCallback detailed;
        {
            std::lock_guard<std::mutex> lock(callback_mutex);
            simple = on_sentence;
            detailed = on_sentence_detailed;
        }
        try {
            if (simple) simple(out.text);
            if (detailed) detailed(out);
        } catch (const std::exception& e) {
            std::cerr << "[echo] exception in sentence callback: " << e.what() << '\n';
        }
    }
}

// =============================================================================
//  SpeechListener - the public methods just forward to Impl
// =============================================================================
SpeechListener::SpeechListener(SpeechListenerConfig config)
    : impl_(std::make_unique<Impl>(std::move(config))) {}

SpeechListener::~SpeechListener() { stop(); }

void SpeechListener::setOnSentence(SentenceCallback callback) {
    std::lock_guard<std::mutex> lock(impl_->callback_mutex);
    impl_->on_sentence = std::move(callback);
}

void SpeechListener::setOnSentenceDetailed(DetailedSentenceCallback callback) {
    std::lock_guard<std::mutex> lock(impl_->callback_mutex);
    impl_->on_sentence_detailed = std::move(callback);
}

void SpeechListener::start() {
    Impl& d = *impl_;
    if (d.running.load()) return;

    // Fresh state for this run.
    d.audio_ring.clear();
    d.vad->reset();
    d.jobs.reopen();
    d.results.reopen();
    d.sentences.reopen();
    d.running.store(true);

    // Start consumers before the producer (the microphone).
    d.output_thread = std::thread([&d] { d.outputLoop(); });
    d.transcription_thread = std::thread([&d] { d.transcriptionLoop(); });
    d.vad_thread = std::thread([&d] { d.vadLoop(); });

    if (d.config.use_microphone) {
        try {
            d.capture = std::make_unique<AudioCapture>(d.audio_ring, kSampleRate,
                                                       d.config.capture_device);
            d.capture->start();
        } catch (...) {
            stop();   // join the threads we just started...
            throw;    // ...then let the caller see the original error
        }
    }
}

void SpeechListener::stop() {
    Impl& d = *impl_;
    // exchange() sets the flag and tells us its old value in one atomic step,
    // so two concurrent stop() calls cannot both do the shutdown.
    if (!d.running.exchange(false)) return;

    if (d.capture) {
        d.capture->stop();
        d.capture.reset();
    }
    // Closing the queues wakes threads blocked in pop().
    d.jobs.close();
    d.results.close();
    d.sentences.close();

    for (std::thread* t : {&d.vad_thread, &d.transcription_thread, &d.output_thread}) {
        if (t->joinable()) t->join();
    }
}

bool SpeechListener::isRunning() const { return impl_->running.load(); }

void SpeechListener::pushAudio(const float* samples, std::size_t count) {
    if (impl_->config.use_microphone) {
        throw std::logic_error("pushAudio() requires config.use_microphone = false");
    }
    impl_->audio_ring.push(samples, count);
}

BackendInfo SpeechListener::backendInfo() const {
    BackendInfo info;
    info.vad = impl_->vad->backendDescription();
    info.transcription = impl_->whisper->backendDescription();
    info.grammar = impl_->grammar ? impl_->grammar->backendDescription() : "disabled";
    return info;
}

std::string SpeechListener::inputDeviceName() const {
    if (!impl_->config.use_microphone) return "(pushAudio)";
    return impl_->capture ? impl_->capture->deviceName() : "(not started)";
}

const SpeechListenerConfig& SpeechListener::config() const { return impl_->config; }

std::vector<std::string> SpeechListener::listInputDevices() { return AudioCapture::listDevices(); }

bool SpeechListener::grammarCorrectionAvailable() {
#if defined(ECHO_HAS_GRAMMAR)
    return true;
#else
    return false;
#endif
}

}  // namespace echo
