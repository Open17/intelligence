// End-to-end test of the whole pipeline (VAD -> endpointing -> whisper ->
// callback) using pushAudio() instead of a microphone.
//
// Usage: test_speech_listener <models-dir> <path/to/jfk.wav>
#include <algorithm>
#include <cctype>
#include <chrono>
#include <condition_variable>
#include <filesystem>
#include <mutex>
#include <thread>
#include <vector>

#include "echo/echo.hpp"
#include "test_framework.hpp"

namespace {
std::filesystem::path g_models;
std::string g_whisper_model;
std::string g_wav_path;
constexpr int kSkip = 77;

// Lower-case letters and single spaces only, so "Ask not! What..." == "ask not what...".
std::string wordsOnly(const std::string& s) {
    std::string out;
    for (unsigned char c : s) {
        if (std::isalpha(c)) {
            out.push_back(static_cast<char>(std::tolower(c)));
        } else if (!out.empty() && out.back() != ' ') {
            out.push_back(' ');
        }
    }
    return out;
}

// Collects sentences delivered on the output thread so the test thread can wait for them.
struct Collector {
    std::mutex mutex;
    std::condition_variable cv;
    std::vector<echo::Sentence> sentences;

    void add(const echo::Sentence& s) {
        std::lock_guard<std::mutex> lock(mutex);
        sentences.push_back(s);
        cv.notify_all();
    }
    // Waits until `count` sentences arrived or the timeout expires.
    bool waitFor(std::size_t count, std::chrono::seconds timeout) {
        std::unique_lock<std::mutex> lock(mutex);
        return cv.wait_for(lock, timeout, [&] { return sentences.size() >= count; });
    }
};

echo::SpeechListenerConfig makeConfig() {
    echo::SpeechListenerConfig config;
    config.vad_model_path = (g_models / "silero_vad.onnx").string();
    config.whisper_model_path = g_whisper_model;
    config.use_microphone = false;
    return config;
}

// Pushes audio in 32 ms chunks. `realtime` sleeps between chunks like a mic would.
void feed(echo::SpeechListener& listener, const std::vector<float>& audio, bool realtime) {
    constexpr std::size_t kChunk = 512;
    for (std::size_t i = 0; i < audio.size(); i += kChunk) {
        const std::size_t n = std::min(kChunk, audio.size() - i);
        listener.pushAudio(audio.data() + i, n);
        if (realtime) std::this_thread::sleep_for(std::chrono::milliseconds(32));
    }
}
}  // namespace

TEST_CASE(transcribes_jfk_through_the_pipeline) {
    echo::SpeechListener listener(makeConfig());
    Collector collector;
    std::string simple_text;
    listener.setOnSentence([&](const std::string& text) { simple_text += text + " "; });
    listener.setOnSentenceDetailed([&](const echo::Sentence& s) { collector.add(s); });
    listener.start();

    std::vector<float> audio = echo::loadAudioFile(g_wav_path);
    audio.insert(audio.end(), 16000 * 3, 0.0f);  // 3 s of silence so the speaker "finishes"
    feed(listener, audio, /*realtime=*/true);

    CHECK(collector.waitFor(1, std::chrono::seconds(10)));
    // Give a possible second sentence a moment, then stop.
    std::this_thread::sleep_for(std::chrono::milliseconds(500));
    listener.stop();

    std::string all;
    for (const auto& s : collector.sentences) {
        all += s.text + " ";
        std::cout << "  \"" << s.text << "\"  silence " << static_cast<int>(s.timings.silence_wait_ms)
                  << " ms, transcription " << static_cast<int>(s.timings.transcription_ms)
                  << " ms, total " << static_cast<int>(s.timings.total_ms) << " ms"
                  << (s.forced ? " (forced)" : "") << '\n';
        CHECK(s.timings.total_ms > 0);
        CHECK(s.timings.silence_wait_ms >= 250);  // speculative_ms = 300 (minus jitter)
        CHECK(s.timings.total_ms >= 650);         // never emitted before pause_ms = 700
    }
    // JFK pauses dramatically, so the speech may arrive as several sentences.
    CHECK(wordsOnly(all).find("ask not what your country can do for you") != std::string::npos);
    CHECK_EQ(simple_text, all);  // both callbacks received the same text
}

TEST_CASE(silence_produces_no_sentences) {
    echo::SpeechListener listener(makeConfig());
    Collector collector;
    listener.setOnSentenceDetailed([&](const echo::Sentence& s) { collector.add(s); });
    listener.start();
    feed(listener, std::vector<float>(16000 * 3, 0.0f), /*realtime=*/false);
    CHECK(!collector.waitFor(1, std::chrono::seconds(2)));
    listener.stop();
}

TEST_CASE(can_restart_after_stop) {
    echo::SpeechListener listener(makeConfig());
    listener.start();
    CHECK(listener.isRunning());
    listener.stop();
    CHECK(!listener.isRunning());
    listener.start();
    CHECK(listener.isRunning());
    listener.stop();
    listener.stop();  // a second stop() is harmless
}

int main(int argc, char** argv) {
    g_models = argc > 1 ? argv[1] : "models";
    g_wav_path = argc > 2 ? argv[2] : "jfk.wav";
    for (const char* name : {"ggml-tiny.en.bin", "ggml-base.en-q5_1.bin", "ggml-base.en.bin"}) {
        if (std::filesystem::exists(g_models / name)) {
            g_whisper_model = (g_models / name).string();
            break;
        }
    }
    if (g_whisper_model.empty() || !std::filesystem::exists(g_models / "silero_vad.onnx") ||
        !std::filesystem::exists(g_wav_path)) {
        std::cout << "skipping: models or " << g_wav_path << " missing\n";
        return kSkip;
    }
    return echo::test::runAll();
}
