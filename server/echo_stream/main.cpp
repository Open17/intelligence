// =============================================================================
//  echo-stream - speech-to-text over stdin/stdout for the voice server
// =============================================================================
//
//  Reads raw audio from stdin and writes one JSON object per line to stdout:
//
//      stdin  : signed 16-bit little-endian PCM, 16 kHz, mono (no header)
//      stdout : {"type":"ready","vad":"...","transcription":"..."}
//               {"type":"sentence","text":"...","raw":"...","forced":false,
//                "audio_ms":1830,"total_ms":702}
//
//  The Python voice server starts one echo-stream per connected device and
//  pipes the device's microphone audio straight in. Keeping the C++ side a
//  separate process means a crash in a native library can never take the
//  server (or other devices) down, and the server stays pure Python.
//
//  At end of input, 2.5 s of silence is pushed so the last sentence is
//  finished, then the program waits for it and exits. That makes it easy to
//  test with a file:
//
//      ffmpeg -i in.wav -f s16le -ac 1 -ar 16000 - | echo-stream --model m.bin
// =============================================================================

#include <echo/echo.hpp>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace {

constexpr std::size_t kChunkSamples = 512;  // 32 ms, one Silero VAD frame

std::mutex g_out_mutex;
std::atomic<long> g_sentences{0};

/// JSON string escaping (RFC 8259): quotes, backslashes and control characters.
std::string jsonEscape(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 8);
    for (unsigned char c : s) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (c < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof buf, "\\u%04x", c);
                    out += buf;
                } else {
                    out += static_cast<char>(c);  // UTF-8 passes through unchanged
                }
        }
    }
    return out;
}

void writeLine(const std::string& line) {
    std::lock_guard<std::mutex> lock(g_out_mutex);
    std::fwrite(line.data(), 1, line.size(), stdout);
    std::fputc('\n', stdout);
    std::fflush(stdout);
}

void usage() {
    std::cerr <<
        "usage: echo-stream [options] < pcm_s16le_16k_mono\n"
        "  --model PATH        whisper model     (default models/ggml-base.en.bin)\n"
        "  --vad-model PATH    Silero VAD model  (default models/silero_vad.onnx)\n"
        "  --threads N         CPU threads, 0 = all cores (default 0)\n"
        "  --audio-ctx N       whisper audio context, 0 = full (default 0)\n"
        "  --beam N            -1 auto, 0 greedy, N beam size (default -1)\n"
        "  --pause-ms N        silence before a sentence ends (default 700)\n"
        "  --force-ms N        silence before unfinished text is emitted (default 2000)\n"
        "  --keep-fillers      keep um/uh/you know\n";
}

bool parseInt(const char* s, int& out) {
    char* end = nullptr;
    long v = std::strtol(s, &end, 10);
    if (end == s || *end != '\0') return false;
    out = static_cast<int>(v);
    return true;
}

}  // namespace

int main(int argc, char** argv) {
    echo::SpeechListenerConfig config;
    config.use_microphone = false;
    config.backend = echo::ComputeBackend::Cpu;  // servers have no GPU; skip probing

    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        auto next = [&]() -> const char* {
            if (i + 1 >= argc) { usage(); std::exit(2); }
            return argv[++i];
        };
        auto nextInt = [&](int& out) {
            if (!parseInt(next(), out)) { usage(); std::exit(2); }
        };
        if (a == "--model") config.whisper_model_path = next();
        else if (a == "--vad-model") config.vad_model_path = next();
        else if (a == "--threads") nextInt(config.n_threads);
        else if (a == "--audio-ctx") nextInt(config.whisper_audio_ctx);
        else if (a == "--beam") nextInt(config.whisper_beam_size);
        else if (a == "--pause-ms") nextInt(config.endpointing.pause_ms);
        else if (a == "--force-ms") nextInt(config.endpointing.force_ms);
        else if (a == "--keep-fillers") config.remove_filler_words = false;
        else if (a == "-h" || a == "--help") { usage(); return 0; }
        else { std::cerr << "unknown option: " << a << '\n'; usage(); return 2; }
    }

    try {
        echo::SpeechListener listener(config);

        listener.setOnSentenceDetailed([](const echo::Sentence& s) {
            if (s.text.empty()) return;
            char nums[128];
            std::snprintf(nums, sizeof nums, ",\"forced\":%s,\"audio_ms\":%.0f,\"total_ms\":%.0f}",
                          s.forced ? "true" : "false", s.audio_ms, s.timings.total_ms);
            writeLine("{\"type\":\"sentence\",\"text\":\"" + jsonEscape(s.text) +
                      "\",\"raw\":\"" + jsonEscape(s.raw_text) + "\"" + nums);
            g_sentences.fetch_add(1);
        });

        listener.start();
        const echo::BackendInfo info = listener.backendInfo();
        writeLine("{\"type\":\"ready\",\"vad\":\"" + jsonEscape(info.vad) +
                  "\",\"transcription\":\"" + jsonEscape(info.transcription) + "\"}");

        // ---- Pump stdin into the pipeline ------------------------------------------
        std::vector<int16_t> pcm(kChunkSamples);
        std::vector<float> samples(kChunkSamples);
        std::size_t carry = 0;  // bytes of a half-read chunk
        auto* bytes = reinterpret_cast<unsigned char*>(pcm.data());
        const std::size_t chunk_bytes = kChunkSamples * sizeof(int16_t);

        for (;;) {
            const std::size_t got = std::fread(bytes + carry, 1, chunk_bytes - carry, stdin);
            if (got == 0) break;  // EOF or error
            carry += got;
            if (carry < chunk_bytes) continue;
            for (std::size_t k = 0; k < kChunkSamples; ++k) {
                // Little-endian on every platform we target (x86-64, ARM64).
                samples[k] = static_cast<float>(pcm[k]) / 32768.0f;
            }
            listener.pushAudio(samples.data(), kChunkSamples);
            carry = 0;
        }

        // ---- End of input: flush the last sentence with real-time silence ----------
        const long before = g_sentences.load();
        std::vector<float> silence(kChunkSamples, 0.0f);
        const int flush_ms = config.endpointing.force_ms + 500;
        for (int t = 0; t < flush_ms; t += 32) {
            listener.pushAudio(silence.data(), silence.size());
            std::this_thread::sleep_for(std::chrono::milliseconds(32));
            if (g_sentences.load() > before && t > config.endpointing.pause_ms) break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(200));
        listener.stop();
    } catch (const std::exception& e) {
        writeLine(std::string("{\"type\":\"error\",\"message\":\"") + jsonEscape(e.what()) + "\"}");
        std::cerr << "echo-stream: " << e.what() << '\n';
        return 1;
    }
    return 0;
}
