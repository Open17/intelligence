// =============================================================================
//  echo-bench - measure transcription accuracy (WER) and speed
// =============================================================================
//
//  Runs every clip of a manifest (see scripts/make-benchmark-set.sh) through
//  WhisperTranscriber and reports the word error rate and timing for each
//  combination of settings, so changes can be compared with numbers:
//
//    echo-bench --model models/ggml-base.en.bin,models/ggml-tiny.en.bin
//    echo-bench --audio-ctx 0,768,512 --condition clean,noise
//
//  WER (word error rate) = (substituted + deleted + inserted words) / words
//  in the reference. 0% is perfect. Text is normalised first (lower case, no
//  punctuation, "20" -> "twenty") so only real word mistakes count.
//
//  --pipeline also runs each clip through Silero VAD and the Endpointer first
//  (faster than real time) and transcribes only the segments they cut out,
//  exactly like the live pipeline. That shows how capture problems affect
//  speech DETECTION, not just recognition. "missed" counts clips in which no
//  speech was detected at all.
//
//  Capture conditioning (what the live pipeline can do to clean up input):
//    --highpass   80 Hz high-pass filter before VAD and whisper
//    --normalize  peak-normalise each utterance before whisper
//
//  Conditions simulate imperfect microphones:
//    clean   the clip as is
//    noise   white noise at 10 dB signal-to-noise ratio
//    quiet   speech 30 dB quieter (a far-away or low-gain mic)
//    hum     DC offset + 50 Hz mains hum (cheap USB mics, Raspberry Pi)
// =============================================================================
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <random>
#include <sstream>
#include <string>
#include <vector>

#include "core/audio_dsp.hpp"
#include "core/endpointer.hpp"
#include "echo/audio_file.hpp"
#include "stt/whisper_transcriber.hpp"
#include "util/ggml_utils.hpp"
#include "vad/silero_vad.hpp"

namespace {

struct Clip {
    std::string path;
    std::string reference;
    std::vector<float> audio;
};

// ---- Text normalisation and WER --------------------------------------------------

std::string numberToWords(int n) {
    static const char* kOnes[] = {"zero", "one", "two", "three", "four", "five", "six",
                                  "seven", "eight", "nine", "ten", "eleven", "twelve",
                                  "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
                                  "eighteen", "nineteen"};
    static const char* kTens[] = {"", "", "twenty", "thirty", "forty", "fifty",
                                  "sixty", "seventy", "eighty", "ninety"};
    if (n < 20) return kOnes[n];
    std::string s = kTens[n / 10];
    if (n % 10 != 0) s += std::string(" ") + kOnes[n % 10];
    return s;
}

std::vector<std::string> normalizeWords(const std::string& text) {
    std::string cleaned;
    for (unsigned char c : text) {
        if (std::isalnum(c) || c == '\'') {
            cleaned.push_back(static_cast<char>(std::tolower(c)));
        } else {
            cleaned.push_back(' ');  // punctuation and hyphens separate words
        }
    }
    std::vector<std::string> words;
    std::istringstream in(cleaned);
    std::string w;
    while (in >> w) {
        const bool numeric = std::all_of(w.begin(), w.end(), [](unsigned char c) { return std::isdigit(c); });
        if (numeric && w.size() <= 2) {
            std::istringstream num(numberToWords(std::stoi(w)));
            std::string part;
            while (num >> part) words.push_back(part);
        } else {
            words.push_back(w);
        }
    }
    return words;
}

// Word-level edit distance (Levenshtein): the minimum number of word
// substitutions, deletions and insertions that turn `hyp` into `ref`.
int editDistance(const std::vector<std::string>& ref, const std::vector<std::string>& hyp) {
    std::vector<int> prev(hyp.size() + 1), cur(hyp.size() + 1);
    for (std::size_t j = 0; j <= hyp.size(); ++j) prev[j] = static_cast<int>(j);
    for (std::size_t i = 1; i <= ref.size(); ++i) {
        cur[0] = static_cast<int>(i);
        for (std::size_t j = 1; j <= hyp.size(); ++j) {
            const int substitute = prev[j - 1] + (ref[i - 1] == hyp[j - 1] ? 0 : 1);
            cur[j] = std::min({prev[j] + 1, cur[j - 1] + 1, substitute});
        }
        std::swap(prev, cur);
    }
    return prev[hyp.size()];
}

// ---- Simulated capture conditions ------------------------------------------------

float rms(const std::vector<float>& x) {
    double sum = 0;
    for (float s : x) sum += static_cast<double>(s) * s;
    return x.empty() ? 0.0f : static_cast<float>(std::sqrt(sum / static_cast<double>(x.size())));
}

std::vector<float> applyCondition(const std::vector<float>& clean, const std::string& condition) {
    std::vector<float> x = clean;
    if (condition == "noise") {
        std::mt19937 rng(1234);
        const float noise_rms = rms(clean) / std::pow(10.0f, 10.0f / 20.0f);  // 10 dB SNR
        std::normal_distribution<float> dist(0.0f, noise_rms);
        for (float& s : x) s += dist(rng);
    } else if (condition == "quiet") {
        for (float& s : x) s *= 0.0316f;  // -30 dB
    } else if (condition == "hum") {
        for (std::size_t i = 0; i < x.size(); ++i) {
            const float t = static_cast<float>(i) / 16000.0f;
            x[i] += 0.1f + 0.05f * std::sin(2.0f * 3.14159265f * 50.0f * t);
        }
    } else if (condition != "clean") {
        throw std::invalid_argument("unknown condition: " + condition);
    }
    return x;
}

// ---- Helpers ---------------------------------------------------------------------

std::vector<std::string> splitList(const std::string& s) {
    std::vector<std::string> out;
    std::stringstream in(s);
    std::string item;
    while (std::getline(in, item, ',')) out.push_back(item);
    return out;
}

std::vector<Clip> loadManifest(const std::string& manifest_path) {
    std::vector<Clip> clips;
    std::ifstream in(manifest_path);
    if (!in) throw std::runtime_error("cannot open manifest " + manifest_path);
    const std::string dir = manifest_path.substr(0, manifest_path.find_last_of("/\\") + 1);
    std::string line;
    while (std::getline(in, line)) {
        const auto tab = line.find('\t');
        if (line.empty() || tab == std::string::npos) continue;
        Clip clip;
        clip.path = dir + line.substr(0, tab);
        clip.reference = line.substr(tab + 1);
        clip.audio = echo::loadAudioFile(clip.path);
        // The pipeline hands whisper the utterance plus ~700 ms of trailing
        // silence (the pause that ended it); mimic that.
        clip.audio.insert(clip.audio.end(), 16000 * 7 / 10, 0.0f);
        clips.push_back(std::move(clip));
    }
    return clips;
}

// Runs audio through VAD + endpointing like the live pipeline and transcribes
// each detected utterance. Returns the joined text; `detected` reports
// whether any speech was found.
struct Conditioning {
    bool highpass = false;
    bool normalize = false;
};

std::string transcribeViaPipeline(echo::SileroVad& vad, echo::WhisperTranscriber& whisper,
                                  const std::vector<float>& audio, const Conditioning& cond,
                                  bool& detected) {
    vad.reset();
    echo::EndpointerConfig cfg;  // library defaults: 700 ms pause etc.
    echo::Endpointer endpointer(cfg);
    constexpr std::size_t kFrame = echo::SileroVad::kWindowSamples;

    // Lead-in silence (as when you start talking into an open mic) and enough
    // trailing silence for the pause to be detected.
    std::vector<float> padded(16000 / 2, 0.0f);
    padded.insert(padded.end(), audio.begin(), audio.end());
    padded.insert(padded.end(), 16000, 0.0f);
    if (cond.highpass) {
        echo::HighPassFilter(80.0f, 16000.0f).process(padded.data(), padded.size());
    }

    std::string text;
    auto transcribe = [&](std::vector<float> segment) {
        if (cond.normalize) echo::normalizePeak(segment);
        const std::string t = whisper.transcribe(segment);
        if (!t.empty()) text += (text.empty() ? "" : " ") + t;
    };
    detected = false;
    for (std::size_t i = 0; i + kFrame <= padded.size(); i += kFrame) {
        const float p = vad.process(padded.data() + i);
        switch (endpointer.processFrame(padded.data() + i, kFrame, p)) {
            case echo::EndpointEvent::SpeechStarted: detected = true; break;
            case echo::EndpointEvent::PauseDetected:
                transcribe(endpointer.audio());
                endpointer.reset();
                break;
            case echo::EndpointEvent::SilenceTimeout:
            case echo::EndpointEvent::MaxLengthReached: transcribe(endpointer.takeAudio()); break;
            default: break;
        }
    }
    if (endpointer.inUtterance()) transcribe(endpointer.takeAudio());
    return text;
}

double percentile(std::vector<double> v, double p) {
    if (v.empty()) return 0;
    std::sort(v.begin(), v.end());
    return v[static_cast<std::size_t>(p * static_cast<double>(v.size() - 1) + 0.5)];
}

}  // namespace

int main(int argc, char** argv) {
    std::string manifest = "bench/manifest.tsv";
    std::string vad_model = "models/silero_vad.onnx";
    bool pipeline = false;
    Conditioning conditioning;
    std::vector<std::string> models = {"models/ggml-base.en.bin"};
    std::vector<std::string> audio_ctxs = {"0"};
    std::vector<std::string> beams = {"0"};
    std::vector<std::string> conditions = {"clean"};
    bool cpu = false;
    bool verbose = false;
    int threads = 0;

    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        auto value = [&]() -> std::string {
            if (i + 1 >= argc) throw std::invalid_argument(arg + " needs a value");
            return argv[++i];
        };
        if (arg == "--manifest") manifest = value();
        else if (arg == "--model") models = splitList(value());
        else if (arg == "--audio-ctx") audio_ctxs = splitList(value());
        else if (arg == "--beam") beams = splitList(value());
        else if (arg == "--condition") conditions = splitList(value());
        else if (arg == "--threads") threads = std::stoi(value());
        else if (arg == "--cpu") cpu = true;
        else if (arg == "--pipeline") pipeline = true;
        else if (arg == "--highpass") conditioning.highpass = true;
        else if (arg == "--normalize") conditioning.normalize = true;
        else if (arg == "--vad-model") vad_model = value();
        else if (arg == "--verbose") verbose = true;
        else {
            std::cerr << "usage: echo-bench [--manifest F] [--model A,B] [--audio-ctx 0,768] [--beam 0,5]\n"
                         "                  [--condition clean,noise,quiet,hum] [--cpu]\n"
                         "                  [--threads N] [--pipeline] [--highpass]\n"
                         "                  [--normalize] [--verbose]\n";
            return arg == "--help" ? 0 : 1;
        }
    }

    try {
        echo::configureGgmlLogging(false);
        const std::vector<Clip> clips = loadManifest(manifest);
        std::printf("%zu clips from %s\n\n", clips.size(), manifest.c_str());
        echo::SileroVad vad(vad_model);
        if (!pipeline && (conditioning.highpass || conditioning.normalize)) {
            throw std::invalid_argument("--highpass/--normalize need --pipeline");
        }
        std::printf("mode: %s%s%s\n", pipeline ? "pipeline (VAD + endpointing + whisper)" : "whisper only",
                    conditioning.highpass ? " +highpass" : "", conditioning.normalize ? " +normalize" : "");
        std::printf("%-28s %-9s %-4s %-6s %7s %9s %9s %7s\n", "model", "audio_ctx", "beam", "cond", "WER",
                    "mean ms", "p90 ms", "missed");

        for (const std::string& model : models) {
            for (const std::string& ctx : audio_ctxs) {
              for (const std::string& beam : beams) {
                echo::WhisperConfig config;
                config.beam_size = std::stoi(beam);
                config.model_path = model;
                config.use_gpu = !cpu;
                config.n_threads = threads;
                config.audio_ctx = std::stoi(ctx);
                echo::WhisperTranscriber whisper(config);
                whisper.transcribe(clips.front().audio);  // warm-up, not measured

                for (const std::string& condition : conditions) {
                    int errors = 0;
                    int ref_words = 0;
                    int missed = 0;
                    std::vector<double> times;
                    for (const Clip& clip : clips) {
                        const std::vector<float> audio = applyCondition(clip.audio, condition);
                        const auto t0 = std::chrono::steady_clock::now();
                        bool detected = true;
                        const std::string text = pipeline
                            ? transcribeViaPipeline(vad, whisper, audio, conditioning, detected)
                            : whisper.transcribe(audio);
                        if (!detected) ++missed;
                        times.push_back(std::chrono::duration<double, std::milli>(
                                            std::chrono::steady_clock::now() - t0).count());
                        const auto ref = normalizeWords(clip.reference);
                        const int e = editDistance(ref, normalizeWords(text));
                        errors += e;
                        ref_words += static_cast<int>(ref.size());
                        if (verbose && e > 0) {
                            std::printf("    [%d] %s\n      ref: %s\n      hyp: %s\n", e,
                                        clip.path.c_str(), clip.reference.c_str(), text.c_str());
                        }
                    }
                    double mean = 0;
                    for (double t : times) mean += t;
                    mean /= static_cast<double>(times.size());
                    const std::string name = model.substr(model.find_last_of("/\\") + 1);
                    std::printf("%-28s %-9s %-4s %-6s %6.1f%% %9.0f %9.0f %7d\n", name.c_str(), ctx.c_str(),
                                beam.c_str(), condition.c_str(), 100.0 * errors / std::max(1, ref_words),
                                mean, percentile(times, 0.9), missed);
                    std::fflush(stdout);
                }
              }
            }
        }
    } catch (const std::exception& e) {
        std::cerr << "error: " << e.what() << '\n';
        return 1;
    }
    return 0;
}
