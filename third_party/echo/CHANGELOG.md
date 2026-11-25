# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-11-24

### Added
- Speculative transcription: whisper starts after 300 ms of silence
  (`endpointing.speculative_ms`, CLI `--speculative-ms`) while the decision
  still waits for 700 ms. End-to-end latency ~0.70 s instead of ~0.84 s on an
  M4 with base.en.
- Grammar correction is precomputed on the early transcript during the
  pause: ~0.70 s instead of ~0.90 s with `--grammar`.
- Cancellable transcriptions: stale speculative jobs are aborted mid-run.
- Beam search (`whisper_beam_size`, CLI `--beam`), automatic on GPU (beam 5)
  and greedy on CPU: noisy-audio WER 4.4% -> 2.5% for +12 ms (base.en, Metal).
- Capture conditioning: 80 Hz high-pass filter (`highpass_hz`), utterance
  level normalisation (`normalize_volume`), and miniaudio's steepest
  resampling anti-alias filter.
- `echo-bench` tool and `scripts/make-benchmark-set.sh`: WER and latency per
  model / decoding / audio_ctx / simulated capture condition, whisper-only or
  through the full VAD + endpointing pipeline.
- Whisper warm-up at load so the first sentence is as fast as the rest.

### Changed
- `SentenceTimings::grammar_ms` now measures the delay grammar correction
  adds after a sentence is accepted (≈0 when precomputed).
- README recommends `small.en-q5_1` on GPUs (noisy-audio WER 1.4% vs 4.4%).

## [0.1.1] - 2026-11-24

### Added
- Rule-based filler removal (`remove_filler_words`, on by default; CLI
  `--keep-fillers`): drops um/uh/er, stutters and comma-delimited "you know",
  "I mean", "like" instantly, before the optional LLM stage.

### Fixed
- A period after a dangling word ("maybe we could.", "go to the.") no longer
  counts as a finished sentence, so a thinking pause no longer splits a
  sentence in two; such text is held until the 2 s timeout.
- Grammar correction could change meaning ("we could" -> "you could") and
  missed comma-delimited fillers. Few-shot examples now match whisper's
  style, and corrections that introduce a new person are rejected.

## [0.1.0] - 2026-11-24

### Added
- `SpeechListener` API: `start()`, `stop()`, `setOnSentence()`,
  `setOnSentenceDetailed()` with per-stage timings, `pushAudio()` for
  non-microphone input, backend reporting.
- Microphone capture with miniaudio (16 kHz mono float) through a lock-free
  ring buffer.
- Silero VAD (v6.2.3 model) on ONNX Runtime 1.32.0, CPU.
- Endpointing on 32 ms frames: 700 ms pause → transcribe and emit if the text
  ends in `. ? !`; forced emit after 2 s of silence; hysteresis, pre-roll,
  minimum speech length and maximum utterance length. All configurable.
- whisper.cpp v1.9.6 transcription (full and quantized models) with Metal,
  CUDA or Vulkan selected by `ECHO_GPU_BACKEND`, and automatic CPU fallback.
- Optional grammar correction with llama.cpp v1.0.0 and
  Qwen2.5-0.5B-Instruct (off by default; `ECHO_ENABLE_GRAMMAR` build option).
- `echo-cli` demo with startup backend report and timing breakdown; `--wav`
  file replay; `echo-minimal` example.
- Model download scripts for bash and PowerShell.
- Unit tests and model-backed integration tests (CTest).
