// =============================================================================
//  echo-cli - demo program for the echo speech-to-text library
// =============================================================================
//
//  Listens to the microphone and prints every finished sentence together with
//  a timing breakdown:
//
//      > Turn on the kitchen lights.
//        silence wait 706 ms | transcription 112 ms | grammar off | total 821 ms
//
//  With --grammar a small LLM also removes filler words and fixes grammar.
//
//  Run `echo-cli --help` for all options. Run it from the repository root so
//  the default model paths (models/...) resolve.
// =============================================================================
#include <echo/echo.hpp>

#include <algorithm>
#include <chrono>
#include <csignal>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#ifndef ECHO_VERSION
#define ECHO_VERSION "dev"
#endif

namespace {

// Set from the Ctrl+C signal handler. Only a `volatile std::sig_atomic_t` is
// guaranteed safe to write from a signal handler.
volatile std::sig_atomic_t g_quit = 0;

void onSignal(int /*signal*/) { g_quit = 1; }

struct Options {
    echo::SpeechListenerConfig config;
    std::string wav_path;       // non-empty: read this file instead of the mic
    bool list_devices = false;
};

void printUsage() {
    std::cout <<
R"(echo-cli - low-latency speech-to-text demo

Usage: echo-cli [options]

Models:
  --model PATH         whisper model        (default: models/ggml-base.en.bin)
  --vad-model PATH     Silero VAD model     (default: models/silero_vad.onnx)
  --language CODE      language for multilingual models (default: en)

Compute:
  --cpu                run whisper on the CPU even if a GPU is available
  --gpu                require the GPU (fail instead of falling back to CPU)
  --threads N          CPU threads (default: all cores)
  --audio-ctx N        whisper encoder context, e.g. 768 for speed (default: full)
  --beam N             beam search width; 0 = greedy (default: 5 on GPU, greedy on CPU)

Text clean-up:
  --keep-fillers       keep "um", "uh", stutters, "you know"... (removed by default)

Grammar correction (off by default):
  --grammar            remove filler words and fix grammar with a small LLM
  --grammar-model PATH GGUF model (default: models/qwen2.5-0.5b-instruct-q4_k_m.gguf)

Endpointing (milliseconds):
  --pause-ms N         silence before transcribing (default: 700)
  --force-ms N         silence before emitting unpunctuated text (default: 2000)
  --min-speech-ms N    ignore sounds shorter than this (default: 200)
  --speculative-ms N   start transcribing early after this silence, 0 = off (default: 300)
  --vad-threshold P    speech probability to start speech, 0..1 (default: 0.5)

Input:
  --device N           microphone index (see --list-devices)
  --highpass-hz N      high-pass filter cut-off, 0 = off (default: 80)
  --no-normalize       don't level quiet/loud speech before transcription
  --list-devices       list microphones and exit
  --wav PATH           transcribe an audio file (WAV/MP3/FLAC) in simulated real time

Other:
  --verbose            show model loading logs and endpointing events
  -h, --help           show this help
)";
}

// Small helpers that turn "--threads 4" into numbers with a clear error.
int toInt(const std::string& flag, const std::string& value) {
    try {
        return std::stoi(value);
    } catch (const std::exception&) {
        throw std::invalid_argument(flag + " expects a whole number, got '" + value + "'");
    }
}

float toFloat(const std::string& flag, const std::string& value) {
    try {
        return std::stof(value);
    } catch (const std::exception&) {
        throw std::invalid_argument(flag + " expects a number, got '" + value + "'");
    }
}

Options parseArgs(int argc, char** argv) {
    Options opt;
    auto& cfg = opt.config;

    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        // Returns the value after a flag, e.g. the "4" in "--threads 4".
        auto value = [&]() -> std::string {
            if (i + 1 >= argc) throw std::invalid_argument(arg + " needs a value");
            return argv[++i];
        };

        if (arg == "-h" || arg == "--help") { printUsage(); std::exit(0); }
        else if (arg == "--model")          cfg.whisper_model_path = value();
        else if (arg == "--vad-model")      cfg.vad_model_path = value();
        else if (arg == "--language")       cfg.language = value();
        else if (arg == "--cpu")            cfg.backend = echo::ComputeBackend::Cpu;
        else if (arg == "--gpu")            cfg.backend = echo::ComputeBackend::Gpu;
        else if (arg == "--keep-fillers")   cfg.remove_filler_words = false;
        else if (arg == "--grammar")        cfg.enable_grammar_correction = true;
        else if (arg == "--grammar-model")  cfg.grammar_model_path = value();
        else if (arg == "--threads")        cfg.n_threads = toInt(arg, value());
        else if (arg == "--audio-ctx")      cfg.whisper_audio_ctx = toInt(arg, value());
        else if (arg == "--beam")           cfg.whisper_beam_size = toInt(arg, value());
        else if (arg == "--pause-ms")       cfg.endpointing.pause_ms = toInt(arg, value());
        else if (arg == "--force-ms")       cfg.endpointing.force_ms = toInt(arg, value());
        else if (arg == "--min-speech-ms")  cfg.endpointing.min_speech_ms = toInt(arg, value());
        else if (arg == "--speculative-ms") cfg.endpointing.speculative_ms = toInt(arg, value());
        else if (arg == "--vad-threshold")  cfg.endpointing.speech_threshold = toFloat(arg, value());
        else if (arg == "--device")         cfg.capture_device = toInt(arg, value());
        else if (arg == "--highpass-hz")    cfg.highpass_hz = toInt(arg, value());
        else if (arg == "--no-normalize")   cfg.normalize_volume = false;
        else if (arg == "--list-devices")   opt.list_devices = true;
        else if (arg == "--wav")            opt.wav_path = value();
        else if (arg == "--verbose")        cfg.verbose = true;
        else throw std::invalid_argument("unknown option '" + arg + "' (try --help)");
    }

    // Keep the "stay in speech" threshold below the "start speech" one.
    cfg.endpointing.silence_threshold =
        std::min(cfg.endpointing.silence_threshold, cfg.endpointing.speech_threshold - 0.15f);
    cfg.use_microphone = opt.wav_path.empty();
    return opt;
}

std::string fileName(const std::string& path) {
    const auto slash = path.find_last_of("/\\");
    return slash == std::string::npos ? path : path.substr(slash + 1);
}

// Prints one finished sentence and its timing breakdown.
void printSentence(const echo::Sentence& s, bool grammar_enabled) {
    const auto ms = [](double v) { return std::to_string(static_cast<long>(v + 0.5)) + " ms"; };
    const auto& t = s.timings;

    std::string line = "  silence wait " + ms(t.silence_wait_ms) +
                       " | transcription " + ms(t.transcription_ms);
    if (s.forced) {
        // Emitted by the silence timeout because the text looked unfinished.
        line += " | hold " + ms(t.hold_ms) + " (forced: looked unfinished)";
    } else if (t.hold_ms >= 5.0) {
        // Transcribed early (speculatively); waited for the full pause.
        line += " | until pause " + ms(t.hold_ms);
    }
    if (!grammar_enabled) {
        line += " | grammar off";
    } else if (t.grammar_ms < 2.0) {
        line += " | grammar done during pause";  // precomputed on the early transcript
    } else {
        line += " | grammar " + ms(t.grammar_ms);
    }
    line += " | total " + ms(t.total_ms);

    std::cout << "> " << s.text << '\n';
    if (s.raw_text != s.text) {
        std::cout << "  (heard: " << s.raw_text << ")\n";  // whisper's exact words
    }
    std::cout << line << "\n\n" << std::flush;
}

int run(const Options& opt) {
    if (opt.list_devices) {
        const auto devices = echo::SpeechListener::listInputDevices();
        std::cout << "Microphones:\n";
        for (std::size_t i = 0; i < devices.size(); ++i) {
            std::cout << "  [" << i << "] " << devices[i] << '\n';
        }
        return 0;
    }

    const auto& cfg = opt.config;
    std::cout << "echo-cli " << ECHO_VERSION << " - loading models...\n" << std::flush;

    echo::SpeechListener listener(cfg);  // loads the models (may throw)
    // The callback runs on the listener's output thread for every sentence.
    listener.setOnSentenceDetailed([&cfg](const echo::Sentence& s) {
        printSentence(s, cfg.enable_grammar_correction);
    });

    // You would plug in your own code here, e.g. sending the text to an AI API:
    //   listener.setOnSentence([](const std::string& text) { sendToAi(text); });

    listener.start();

    const echo::BackendInfo backends = listener.backendInfo();
    std::cout << "  Input          : "
              << (opt.wav_path.empty() ? listener.inputDeviceName() : opt.wav_path) << '\n'
              << "  Speech detect  : " << fileName(cfg.vad_model_path) << " -> " << backends.vad << '\n'
              << "  Transcription  : " << fileName(cfg.whisper_model_path) << " -> "
              << backends.transcription << '\n'
              << "  Grammar        : "
              << (cfg.enable_grammar_correction ? fileName(cfg.grammar_model_path) + " -> " + backends.grammar
                                                : std::string("disabled (enable with --grammar)"))
              << '\n'
              << "  Endpointing    : pause " << cfg.endpointing.pause_ms << " ms, force "
              << cfg.endpointing.force_ms << " ms\n\n";

    if (!opt.wav_path.empty()) {
        // File mode: feed the file at real-time speed, exactly like a mic.
        std::vector<float> audio = echo::loadAudioFile(opt.wav_path);
        audio.insert(audio.end(),
                     static_cast<std::size_t>(16 * (cfg.endpointing.force_ms + 1000)), 0.0f);
        std::cout << "Playing " << opt.wav_path << " into the pipeline...\n\n" << std::flush;
        constexpr std::size_t kChunk = 512;  // 32 ms
        const auto start = std::chrono::steady_clock::now();
        for (std::size_t i = 0; i < audio.size() && !g_quit; i += kChunk) {
            listener.pushAudio(audio.data() + i, std::min(kChunk, audio.size() - i));
            // Sleep until this chunk's real-time deadline (no drift build-up).
            std::this_thread::sleep_until(start + std::chrono::microseconds((i + kChunk) * 1000000 / 16000));
        }
        std::this_thread::sleep_for(std::chrono::seconds(1));  // let the last sentence finish
    } else {
        std::cout << "Listening... speak into the microphone. Press Ctrl+C to quit.\n\n" << std::flush;
        while (!g_quit) {
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
        std::cout << "\nStopping...\n";
    }

    listener.stop();
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    std::signal(SIGINT, onSignal);
    std::signal(SIGTERM, onSignal);

    try {
        return run(parseArgs(argc, argv));
    } catch (const std::exception& e) {
        std::cerr << "error: " << e.what() << '\n';
        return 1;
    }
}
