# echo

**Low-latency, on-device speech-to-text in C++17.** echo listens to your microphone,
works out when you have finished a sentence, and hands the text to your code,
typically about **0.7 s** after you stop talking. Nothing leaves your machine.

```
> Turn on the kitchen lights.
  silence wait 322 ms | transcription 146 ms | until pause 237 ms | grammar off | total 705 ms
```

| Stage | Library | Runs on |
|---|---|---|
| Microphone capture | [miniaudio](https://miniaud.io) (16 kHz mono float) | audio thread |
| Speech detection | [Silero VAD](https://github.com/snakers4/silero-vad) via ONNX Runtime | CPU |
| Transcription | [whisper.cpp](https://github.com/ggml-org/whisper.cpp) | Metal / CUDA / Vulkan, CPU fallback |
| Grammar clean-up (optional) | [llama.cpp](https://github.com/ggml-org/llama.cpp) + Qwen2.5-0.5B-Instruct | same as whisper |

Targets: **macOS** (Apple Silicon), **Linux** (x86-64 and ARM64, including **Raspberry Pi 5**)
and **Windows** (x64).

---

## Contents

- [Quick start (macOS)](#quick-start-macos)
- [Building](#building): [macOS](#macos) · [Linux](#linux) · [Raspberry Pi 5](#raspberry-pi-5) · [Windows](#windows)
- [Models](#models)
- [The demo CLI](#the-demo-cli)
- [Using the library](#using-the-library)
- [How it decides you're done talking](#how-it-decides-youre-done-talking)
- [Latency and tuning](#latency-and-tuning)
- [Project layout](#project-layout)
- [Tests](#tests)
- [Troubleshooting](#troubleshooting)

---

## Quick start (macOS)

```bash
brew install cmake ninja                 # one-time
./scripts/download-models.sh             # Silero VAD + whisper base.en (~150 MB)
cmake --preset default                   # configure (downloads dependencies, one-time)
cmake --build --preset default           # build
./build/default/bin/echo-cli             # talk!
```

The first time, macOS asks whether your terminal may use the microphone. Allow it.

---

## Building

Requirements on every platform: a C++17 compiler, **CMake ≥ 3.21**, Git, and an
internet connection for the first configure. All C++ dependencies (miniaudio,
ONNX Runtime, whisper.cpp, llama.cpp) are downloaded by CMake's `FetchContent`
at pinned versions with checksums. You don't install them yourself.

Build presets (in `CMakePresets.json`):

| Preset | What it builds |
|---|---|
| `default` | Release, GPU backend picked automatically (see below) |
| `debug` | Debug build (slow inference, for stepping through code) |
| `cpu` | Release, CPU only |
| `cuda` / `vulkan` | Release, forced CUDA / Vulkan |
| `rpi5` | Release, CPU only, tuned for the Pi 5's Cortex-A76 |
| `windows` | Visual Studio 2022, x64 |

**GPU backend.** `-DECHO_GPU_BACKEND=auto|metal|cuda|vulkan|none`. `auto` (the
default) picks **Metal** on macOS, **CUDA** if the CUDA Toolkit is installed,
**Vulkan** if the Vulkan SDK (with `glslc`) is installed, and the **CPU**
otherwise. On ARM Linux boards like the Pi it picks the CPU, because the CPU is
faster than the Pi's GPU for these models. Whatever is compiled in, echo checks
at startup whether a GPU really exists and **falls back to the CPU
automatically**. On the CPU it uses one thread per core.

Other options: `-DECHO_ENABLE_GRAMMAR=OFF` (skip llama.cpp: faster build),
`-DECHO_BUILD_TESTS=OFF`, `-DECHO_BUILD_EXAMPLES=OFF`, `-DONNXRUNTIME_ROOT=<dir>`
(use an ONNX Runtime you already have instead of downloading one).

### macOS

Apple Silicon (M1 to M4). Uses Metal.

```bash
xcode-select --install                    # compiler, if you don't have it
brew install cmake ninja
./scripts/download-models.sh
cmake --preset default
cmake --build --preset default
./build/default/bin/echo-cli
```

### Linux

Ubuntu 22.04+/Debian 12+ shown; other distros need the equivalent packages.

```bash
sudo apt install build-essential cmake ninja-build git curl
./scripts/download-models.sh
cmake --preset default        # CUDA/Vulkan are used automatically if installed
cmake --build --preset default
./build/default/bin/echo-cli
```

- **NVIDIA GPU:** install the [CUDA Toolkit](https://developer.nvidia.com/cuda-downloads)
  so `nvcc` is on your `PATH`, or use `cmake --preset cuda`.
- **AMD/Intel GPU:** `sudo apt install libvulkan-dev glslc` (or the LunarG
  Vulkan SDK), then `cmake --preset vulkan`.
- Audio goes through PulseAudio/PipeWire or ALSA. These are loaded at run time,
  so no `-dev` packages are needed.

### Raspberry Pi 5

Raspberry Pi OS **64-bit** (Bookworm or newer). A Pi 5 with 4 GB+ RAM and a USB
microphone works well. Use the smaller `tiny.en` model.

```bash
sudo apt install build-essential cmake ninja-build git curl
./scripts/download-models.sh --pi                     # Silero + tiny.en + tiny.en-q5_1
cmake --preset rpi5 -DECHO_ENABLE_GRAMMAR=OFF         # drop the flag if you want grammar
cmake --build --preset rpi5                           # takes a while on a Pi
./build/rpi5/bin/echo-cli --model models/ggml-tiny.en-q5_1.bin
```

On the Pi, transcription is the bottleneck. See [Latency and tuning](#latency-and-tuning)
for `--audio-ctx`, which speeds it up a lot. Building with
`-DECHO_ENABLE_GRAMMAR=OFF` skips compiling llama.cpp, which saves a lot of time
on the Pi.

### Windows

Windows 10/11 x64 with **Visual Studio 2022** (workload *"Desktop development
with C++"*, which includes CMake) and Git. Open a **Developer PowerShell for
VS 2022**:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\download-models.ps1
cmake --preset windows                    # add -DECHO_GPU_BACKEND=cuda or vulkan for a GPU
cmake --build --preset windows
.\build\windows\bin\Release\echo-cli.exe
```

`onnxruntime.dll` is copied next to the `.exe` automatically. If Windows blocks
the microphone, enable *Settings → Privacy & security → Microphone → Let
desktop apps access your microphone*.

---

## Models

Downloaded into `models/` (which git ignores) by `scripts/download-models.sh`
(macOS/Linux) or `scripts/download-models.ps1` (Windows):

| File | Size | Purpose |
|---|---|---|
| `silero_vad.onnx` | 2 MB | speech detection (always needed) |
| `ggml-base.en.bin` | 148 MB | **default** transcription model |
| `ggml-base.en-q5_1.bin` | 60 MB | quantized base.en: smaller, similar accuracy |
| `ggml-tiny.en.bin` | 78 MB | fastest; **recommended for Raspberry Pi** |
| `ggml-tiny.en-q5_1.bin` | 32 MB | quantized tiny.en |
| `ggml-small.en-q5_1.bin` | 190 MB | **most accurate within the latency budget on a GPU** |
| `qwen2.5-0.5b-instruct-q4_k_m.gguf` | 490 MB | grammar correction (optional, `--grammar`) |

```bash
./scripts/download-models.sh --whisper base.en-q5_1 --whisper small.en-q5_1
./scripts/download-models.sh --grammar     # adds the Qwen model
./scripts/download-models.sh --all
```

Quantized models (`-q5_1`, `-q8_0`) store weights in fewer bits: smaller files,
less memory and usually faster, with a small accuracy cost. Any whisper.cpp
GGML model works with `--model`, and any GGUF chat model works with
`--grammar-model`.

---

## The demo CLI

```bash
./build/default/bin/echo-cli                         # base.en, mic, GPU if available
./build/default/bin/echo-cli --grammar               # + LLM grammar fixes
./build/default/bin/echo-cli --keep-fillers          # don't remove um/uh/"you know"
./build/default/bin/echo-cli --model models/ggml-tiny.en.bin --cpu
./build/default/bin/echo-cli --wav recording.wav     # replay a file in real time
./build/default/bin/echo-cli --list-devices          # then --device N
./build/default/bin/echo-cli --help                  # everything else
```

At startup it prints where each model runs:

```
  Input          : MacBook Air Microphone
  Speech detect  : silero_vad.onnx -> CPU (ONNX Runtime 1.32.0)
  Transcription  : ggml-base.en.bin -> GPU - Metal: MTL0 (Apple M4), beam 5
  Grammar        : qwen2.5-0.5b-instruct-q4_k_m.gguf -> GPU - Metal: MTL0 (Apple M4)
  Endpointing    : pause 700 ms, force 2000 ms
```

Then, for each sentence, it prints the text and where the time went (example):

```
> I was thinking that maybe we could go to the beach on Saturday.
  (heard: So I was thinking that maybe we could go to the beach on Saturday.)
  silence wait 319 ms | transcription 226 ms | until pause 159 ms | grammar done during pause | total 704 ms
```

- **silence wait**: from your last word until transcription started (≈ 300 ms: it starts early, see below)
- **transcription**: whisper.cpp
- **until pause**: transcription finished before the 700 ms pause was over, so this is the
  rest of the pause. Your sentence is never accepted before 700 ms of silence.
- **hold**: shown instead when the text looked unfinished and was emitted by the 2 s timeout
- **grammar**: delay added by the optional LLM clean-up ("done during pause" = none)
- **total**: from your last word until the callback received the text

Run from the repository root so the default `models/...` paths resolve, or pass
`--model`/`--vad-model` explicitly.

---

## Using the library

Link the CMake target `echo::echo` and include one header:

```cpp
#include <echo/echo.hpp>
#include <iostream>

int main() {
    echo::SpeechListenerConfig config;
    config.whisper_model_path = "models/ggml-base.en.bin";
    config.enable_grammar_correction = false;   // the default

    echo::SpeechListener listener(config);      // loads the models (throws on error)

    listener.setOnSentence([](const std::string& text) {
        std::cout << "You said: " << text << std::endl;
        // sendToAi(text);   <- e.g. call your AI API here
    });

    listener.start();       // opens the mic, starts the worker threads
    std::cin.get();         // ... your program runs ...
    listener.stop();
}
```

This exact program is in [`examples/minimal`](examples/minimal/main.cpp) and builds as `echo-minimal`.

**API summary** (see [`include/echo/speech_listener.hpp`](include/echo/speech_listener.hpp)):

| Method | |
|---|---|
| `SpeechListener(config)` | loads models; throws `std::runtime_error` if one is missing |
| `setOnSentence(std::function<void(const std::string&)>)` | text of each finished sentence |
| `setOnSentenceDetailed(std::function<void(const Sentence&)>)` | text + raw text + timings + `forced` flag |
| `start()` / `stop()` | start/stop listening; can be repeated |
| `pushAudio(samples, count)` | feed your own 16 kHz mono audio (`config.use_microphone = false`) |
| `backendInfo()` | which device each model runs on |
| `listInputDevices()` | microphone names for `config.capture_device` |

Callbacks run on the library's **output thread**. A slow callback (such as a
network request) never causes dropped audio, but it delays the next sentence's
callback. Hand long-running work off to your own thread if that matters.

**Configuration** ([`include/echo/config.hpp`](include/echo/config.hpp)). All fields have defaults:

| Field | Default | Meaning |
|---|---|---|
| `vad_model_path` | `models/silero_vad.onnx` | |
| `whisper_model_path` | `models/ggml-base.en.bin` | full or quantized GGML model |
| `language` | `"en"` | for multilingual (non-`.en`) models |
| `backend` | `Auto` | `Auto` (GPU, else CPU), `Gpu` (fail without GPU), `Cpu` |
| `n_threads` | `0` | CPU threads; 0 = all cores |
| `whisper_audio_ctx` | `0` | 0 = full; smaller = faster (see tuning) |
| `whisper_beam_size` | `-1` | -1 = auto (beam 5 on GPU, greedy on CPU), 0 = greedy |
| `endpointing.pause_ms` | `700` | silence before transcribing |
| `endpointing.force_ms` | `2000` | silence before emitting text that looks unfinished |
| `endpointing.speech_threshold` | `0.5` | VAD probability that starts speech |
| `endpointing.silence_threshold` | `0.35` | VAD probability below which speech stops |
| `endpointing.min_speech_ms` | `200` | ignore shorter sounds |
| `endpointing.speculative_ms` | `300` | start transcribing early (0 = off) |
| `endpointing.pre_roll_ms` | `300` | audio kept from before speech started |
| `endpointing.max_utterance_ms` | `28000` | split very long monologues |
| `remove_filler_words` | `true` | rule-based removal of um/uh, stutters, ", you know," (instant) |
| `enable_grammar_correction` | `false` | run the LLM clean-up stage |
| `grammar_model_path` | `models/qwen2.5-0.5b-instruct-q4_k_m.gguf` | any GGUF chat model |
| `use_microphone` / `capture_device` | `true` / `-1` | input selection |
| `highpass_hz` | `80` | remove DC offset / hum / rumble below this (0 = off) |
| `normalize_volume` | `true` | level each utterance before whisper |

---

## How it decides you're done talking

The VAD scores every **32 ms frame** for speech probability. Silero VAD needs
exactly 512 samples per call at 16 kHz, which is 32 ms. All thresholds are in
milliseconds, so the frame size only affects rounding: 700 ms becomes 22 frames
(704 ms).

```
talking ... last word │◄──── 700 ms silence ────►│ transcribe
                      │                          ├─ looks finished → emit now
                      │                          └─ unfinished     → hold it
                      │                             more speech?  → stale, keep listening
                      │◄──────────── 2 s silence ──────────────►│ emit the held text anyway
```

Hysteresis prevents flicker: speech **starts** when the probability reaches 0.5
but only **stops** when it falls below 0.35. 300 ms of pre-roll keeps the
start of the first word from being clipped. Sounds shorter than 200 ms are
dropped.

"Looks finished" means the text ends in `.`, `?` or `!`, with two exceptions
that count as **unfinished**: a trailing "…", and a period straight after a
word a sentence can't really end on ("maybe we could.", "go to the.",
"and um."). Whisper puts a period on almost every fragment, so without the
second rule a one-second thinking pause would cut your sentence in two.

**Speculative transcription.** whisper starts after only 300 ms of silence
(`speculative_ms`), but the decision still waits for the full 700 ms. If you
keep talking, the early transcript is out of date and is discarded (a running
whisper job is cancelled). If you don't, the text is already done when the
pause ends, so transcription time no longer adds to your latency. With
`--grammar`, the LLM correction also runs during the pause.

There's a longer walk-through with diagrams in [docs/architecture.md](docs/architecture.md).

---

## Latency and tuning

### Latency

Measured on an Apple M4 MacBook Air (Metal), base.en, replaying recordings with `--wav`:

| Setup | total after last word |
|---|---|
| default (speculative transcription) | **~0.70 s** |
| default + `--grammar` | **~0.70–0.76 s** |
| `--speculative-ms 0` (transcribe only after the pause) | ~0.83–0.84 s |
| `--speculative-ms 0 --grammar` | ~0.89–0.90 s |

The floor is the 700 ms pause itself. Lower it with `--pause-ms 500` if you
like, at the cost of more mid-sentence cut-offs when you pause to think (text
that looks unfinished is still held until `--force-ms`).

### Accuracy: choosing a model

Word error rate on the built-in benchmark (40 clips, 5 voices; noise = 10 dB
signal-to-noise ratio), M4 with Metal, high-pass and normalisation on. Lower is better:

| Model | decoding | WER clean | WER noisy | transcription time |
|---|---|---|---|---|
| tiny.en | greedy | 0.7% | 5.3% | 62 ms |
| base.en (default) | greedy | 0.9% | 4.4% | 98 ms |
| base.en (default) | **beam 5 (auto on GPU)** | 0.9% | 2.5% | 111 ms |
| base.en-q5_1 | beam 5 | 0.9% | 2.8% | 109 ms |
| small.en-q5_1 | greedy | 0.9% | 1.9% | 266 ms |
| **small.en-q5_1** | **beam 5** | 0.9% | **1.4%** | ~300 ms |
| large-v3-turbo-q5_0 | greedy | 0.2% | 0.9% | 1114 ms |

Because transcription now overlaps the pause, anything that finishes within
about **380 ms** costs no latency. **On a GPU, small.en-q5_1 is the best
choice:** errors in noisy audio fall by about two thirds. Short sentences
still arrive in ~0.70 s; long ones (5 s+ of speech) take ~450 ms to
transcribe, so expect up to ~0.8 s. large-v3-turbo is the most accurate, but
too slow for sub-second use.

```bash
./scripts/download-models.sh --whisper small.en-q5_1
./build/default/bin/echo-cli --model models/ggml-small.en-q5_1.bin
```

The test voices are synthetic (macOS `say`), so real-world error rates are
higher. Use the table to *compare* settings.

### Tips

- **Raspberry Pi / slow CPU:** use `tiny.en`, `tiny.en-q5_1` or `base.en-q5_1`.
  Beam search is off on the CPU by default (it costs ~20% more time). Try
  `--audio-ctx 768`: whisper normally processes a fixed 30 s window even for a
  2 s sentence, and a smaller context is much faster. On small.en it raised
  noisy-audio WER from 1.9% to 2.5%, so test it with your voice.
- **Speculation on a busy CPU:** it costs an extra transcription whenever you
  pause for 300–700 ms mid-sentence (cancelled as soon as you resume).
  `--speculative-ms 0` turns it off.
- **Threads:** the default is all cores. On machines with efficiency cores,
  `--threads` set to the number of performance cores is sometimes faster.
- **Grammar stage:** one LLM call per sentence, normally hidden inside the
  pause. The instruction prompt is cached after warm-up.

### Capture quality

Before anything else sees the audio, echo:
- resamples with miniaudio's steepest anti-aliasing filter;
- removes everything below 80 Hz, i.e. DC offset, mains hum and rumble (`--highpass-hz`);
- levels each utterance before whisper, with gain capped at +30 dB (`--no-normalize` to disable).

On the synthetic benchmark these changed WER by less than noise. Whisper
already copes well with level and hum. They're there for real hardware, such
as cheap USB mics with a DC offset or distant microphones. Background noise
is the condition that really hurts, and the remedy is a bigger model (see the table).

### Benchmarking your own changes

```bash
./scripts/make-benchmark-set.sh                       # macOS: speaks bench/sentences.txt
./build/default/bin/echo-bench --pipeline --highpass --normalize \
    --model models/ggml-base.en.bin,models/ggml-small.en-q5_1.bin \
    --beam 0,5 --condition clean,noise,quiet,hum
```

Add your own recordings as `file<TAB>reference text` lines in
`bench/manifest.tsv` for real-voice numbers.

---|---|---|---|
| base.en, Metal | 105–211 ms | (off) | ~0.91 s |
| base.en-q5_1, Metal | ~160 ms | (off) | ~0.87 s |
| tiny.en, CPU (10 threads) | 137–332 ms | (off) | ~0.88–1.03 s |
| base.en + grammar, Metal | 105–175 ms | 87–229 ms | ~0.97–1.11 s |

The 700 ms pause is the largest share of the total.

- **Want it snappier?** `--pause-ms 500`. You'll get more mid-sentence cut-offs
  when you pause to think. Text that looks unfinished is still held until
  `--force-ms`.
- **Slow CPU (Raspberry Pi)?** Use `tiny.en` or `tiny.en-q5_1`, and try
  `--audio-ctx 768` (or 512). Whisper normally processes a fixed 30 s window
  even for a 2 s sentence; a smaller audio context processes less of that
  window, which is much faster. Accuracy can drop, so test it with your voice.
- **Threads:** the default is all cores. On machines with efficiency cores,
  `--threads` set to the number of performance cores is sometimes faster.
- **Grammar stage:** adds one LLM call per sentence. The instruction prompt is
  cached after warm-up, so each call only processes the new sentence.

---

## Project layout

```
echo/
├── include/echo/          public API: echo.hpp, speech_listener.hpp, config.hpp, audio_file.hpp
├── src/
│   ├── speech_listener.cpp   the pipeline: threads, queues, endpointing decisions
│   ├── core/              pure C++ (no dependencies, unit-tested): ring buffer, blocking
│   │                      queue, endpointer state machine, text helpers, DSP filters
│   ├── audio/             miniaudio capture + audio-file loading
│   ├── vad/               Silero VAD on ONNX Runtime
│   ├── stt/               whisper.cpp transcriber
│   ├── grammar/           llama.cpp grammar corrector (+ stub when disabled)
│   └── util/              ggml helpers: GPU discovery, threads, log filtering
├── apps/echo-cli/         the demo CLI
├── examples/minimal/      smallest possible program using the library
├── tests/                 unit tests + model tests (CTest)
├── tools/echo-bench/      accuracy (WER) + speed benchmark
├── bench/                 benchmark sentences (generated audio is git-ignored)
├── cmake/                 one module per dependency + GPU backend selection
├── scripts/               model download scripts (bash + PowerShell)
├── docs/architecture.md   how the pieces fit together
└── models/                downloaded models (git-ignored)
```

---

## Tests

```bash
ctest --preset default                 # Windows: ctest --preset windows
```

- **Unit tests** (`test_ring_buffer`, `test_blocking_queue`, `test_endpointer`,
  `test_text_utils`, `test_audio_dsp`) need no models.
- **Model tests** (`test_silero_vad`, `test_whisper_transcriber`,
  `test_speech_listener`, `test_grammar_corrector`) use the files in `models/`
  and whisper.cpp's sample recording. They are reported as *skipped* if a model
  is missing. `test_speech_listener` replays audio in real time and takes about 20 s.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `whisper model not found` | Run the download script, and run the program from the repo root (or pass `--model`). |
| Nothing is ever transcribed on macOS | *System Settings → Privacy & Security → Microphone*: allow your terminal app. |
| Wrong microphone | `echo-cli --list-devices`, then `--device N`. |
| Sentences get cut off when you pause | Raise `--pause-ms` (e.g. 900). |
| Background noise triggers transcripts | Raise `--vad-threshold` (e.g. 0.6) or `--min-speech-ms`. |
| `Transcription : CPU` although you have a GPU | Check the configure output for `GPU backend: ...`. Install CUDA/Vulkan SDK and reconfigure, or use `--preset cuda` / `--preset vulkan`. |
| ONNX Runtime download fails / unsupported platform | Install ONNX Runtime yourself and configure with `-DONNXRUNTIME_ROOT=/path` (folder containing `include/` and `lib/`). Homebrew: `/opt/homebrew/opt/onnxruntime`. |
| Need model/ggml logs | `--verbose` also prints endpointing events (`PauseDetected`, …). |

**Note on ONNX Runtime:** the build downloads ONNX Runtime from Microsoft's
official NuGet package (`Microsoft.ML.OnnxRuntime`), which contains prebuilt
libraries for every supported platform. It's a large one-time download per build
folder, about 180 MB.

---

## License

This repository does not include a license for echo's own code yet. Add one
(e.g. MIT) before publishing it. Third-party components keep their own licenses: whisper.cpp, llama.cpp,
miniaudio, Silero VAD and ONNX Runtime are MIT (miniaudio is also public domain).
Model files have their own licenses (whisper: MIT; Qwen2.5-0.5B-Instruct:
Apache 2.0).
