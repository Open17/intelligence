// Transcribes whisper.cpp's JFK sample with the real model.
// Skipped when no whisper model has been downloaded.
//
// Usage: test_whisper_transcriber <models-dir> <path/to/jfk.wav>
#include "stt/whisper_transcriber.hpp"
#include "util/ggml_utils.hpp"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <filesystem>

#include "echo/audio_file.hpp"
#include "test_framework.hpp"

namespace {
std::string g_model_path;
std::string g_wav_path;
constexpr int kSkip = 77;

std::string lowercase(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return s;
}
}  // namespace

TEST_CASE(transcribes_jfk_sample) {
    echo::WhisperConfig config;
    config.model_path = g_model_path;
    echo::WhisperTranscriber whisper(config);
    std::cout << "  backend: " << whisper.backendDescription() << '\n';

    const auto audio = echo::loadAudioFile(g_wav_path);
    CHECK(audio.size() > 16000u * 10u);  // the clip is ~11 s long

    const auto start = std::chrono::steady_clock::now();
    const std::string text = whisper.transcribe(audio);
    const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                        std::chrono::steady_clock::now() - start).count();
    std::cout << "  text (" << ms << " ms): " << text << '\n';

    CHECK(lowercase(text).find("ask not what your country can do for you") != std::string::npos);
}

TEST_CASE(silence_gives_empty_text) {
    echo::WhisperConfig config;
    config.model_path = g_model_path;
    echo::WhisperTranscriber whisper(config);
    const std::vector<float> silence(16000 * 2, 0.0f);
    CHECK_EQ(whisper.transcribe(silence), std::string(""));
}

int main(int argc, char** argv) {
    const std::filesystem::path models = argc > 1 ? argv[1] : "models";
    g_wav_path = argc > 2 ? argv[2] : "jfk.wav";
    // Use whichever English model is present, smallest first.
    for (const char* name : {"ggml-tiny.en.bin", "ggml-base.en-q5_1.bin", "ggml-base.en.bin"}) {
        if (std::filesystem::exists(models / name)) {
            g_model_path = (models / name).string();
            break;
        }
    }
    if (g_model_path.empty() || !std::filesystem::exists(g_wav_path)) {
        std::cout << "skipping: no whisper model in " << models << " or missing " << g_wav_path << '\n';
        return kSkip;
    }
    std::cout << "model: " << g_model_path << '\n';
    echo::configureGgmlLogging(/*verbose=*/false);
    return echo::test::runAll();
}
