# Architecture

This document explains how echo turns microphone audio into finished sentences,
and points at the C++ techniques used along the way. Read it alongside the
code. Every file it mentions has comments explaining the details.

## 1. The pipeline

```
 ┌────────────────┐  lock-free   ┌─────────────────────────┐   jobs    ┌──────────────────────┐
 │ miniaudio      │ ring buffer  │ VAD thread              │ ────────► │ transcription thread │
 │ audio callback │ ───────────► │ Silero VAD (32 ms)      │           │ whisper.cpp          │
 │ (OS real-time  │  16 kHz mono │ + Endpointer            │ ◄──────── │ (GPU or CPU)         │
 │  thread)       │  float       │   state machine         │  results  └──────────────────────┘
 └────────────────┘              └───────────┬─────────────┘
                                             │ finished sentences
                                             ▼
                                 ┌─────────────────────────┐
                                 │ output thread           │
                                 │ optional grammar (LLM)  │ ──► your callback
                                 │ timings                 │
                                 └─────────────────────────┘
```

Why so many threads? Each stage has a different "can it wait?" rule:

| Thread | Owner | May it block? | Code |
|---|---|---|---|
| audio callback | OS / miniaudio | **Never.** A late callback means lost audio | `src/audio/audio_capture.cpp` |
| VAD | echo | briefly (sleeps 2 ms when no audio) | `SpeechListener::Impl::vadLoop` |
| transcription | echo | yes, whisper takes 50–1000 ms | `Impl::transcriptionLoop` |
| output | echo | yes, grammar and *your* callback | `Impl::outputLoop` |

Because whisper runs on its own thread, the VAD keeps tracking speech while a
sentence is being transcribed. That is how echo notices when you resume
talking and throws the out-of-date transcript away.

## 2. Getting audio off the real-time thread

`src/core/ring_buffer.hpp` is a **single-producer/single-consumer lock-free
ring buffer**. The audio callback only does a `push()` (a `memcpy` and one
atomic store); the VAD thread `pop()`s. There is no mutex, so the audio thread
can never wait for another thread.

C++ concepts used:

- `std::atomic<std::size_t>` with `memory_order_acquire/release`: the writer
  *releases* new data by publishing the index, and the reader *acquires* it, so
  it is guaranteed to see the samples.
- `alignas(64)` keeps the two indices on separate cache lines (no "false sharing").

The worker threads use `src/core/blocking_queue.hpp` instead: a classic
`std::mutex` + `std::condition_variable` queue. Waiting is fine there, and it
lets idle threads sleep instead of spinning.

## 3. Endpointing: when are you done?

`src/core/endpointer.hpp` is a **state machine** with no threads or models,
only frame counting. That is why `tests/test_endpointer.cpp` can test it with
hand-written probability sequences.

```
                                  silence 300 ms: SpeculativePause (no state change)
            prob ≥ 0.5                         silence 700 ms
   IDLE ───────────────► IN UTTERANCE ─────────────────────────► PAUSE DETECTED
    ▲   (SpeechStarted,    │  ▲  (speech while prob ≥ 0.35)       │   │
    │    + 300 ms pre-roll)│  └───────────────────────────────────┘   │ silence reaches 2 s
    │                      │        SpeechResumed (revision++)        ▼
    │                      │                                   SILENCE TIMEOUT
    │                      └── utterance ≥ 28 s ──► MAX LENGTH        │
    └────────────── reset() / takeAudio() ◄───────────────────────────┘
```

What `SpeechListener` does with each event (`Impl::handleEvent` / `handleResult`):

| Event | Action |
|---|---|
| `SpeechStarted` / `SpeechResumed` | drop any held transcript (it's out of date) |
| `SpeculativePause` (300 ms) | send a copy of the audio to whisper *early* |
| early transcript arrives (before 700 ms) | hold it; if it looks finished and grammar is on, start the LLM correction now |
| `PauseDetected` (700 ms) | judge the held transcript now, or wait for the running job, or submit one |
| transcript judged: looks finished (`looksFinished()`) | **emit**, reset the endpointer |
| transcript judged: unfinished (no `. ? !`, a trailing "…", or a period after "and"/"the"/"could"/…) | hold it |
| transcript arrives, but revision changed | stale: ignore |
| `SilenceTimeout` (2 s) | emit the held transcript (or emit when whisper finishes) |
| `MaxLengthReached` | transcribe and emit this chunk, keep listening |
| `TooShort` (< 200 ms) | ignore (cough, click) |

**Revisions.** Every time new speech is added to the utterance the endpointer
increments `revision()`. Each transcription job carries the revision of its
audio. When the result comes back, a different revision means "the speaker has
said more since": the result is stale. The transcription thread also checks
this before starting a job, and **while whisper runs**, via whisper.cpp's
abort callback. A job that goes stale part-way is cancelled, so speculative
work never delays the transcription that matters.

**Why speculation is safe.** A speculative transcript is only used if no
speech arrived between 300 ms and 700 ms (otherwise the revision changed). In
that case the audio differs only by extra silence, so the decision at 700 ms
is the same as transcribing at 700 ms, just without waiting for whisper.

## 3b. Capture conditioning

The VAD thread runs every frame through an 80 Hz high-pass filter
(`src/core/audio_dsp.hpp`, a Butterworth "biquad") before the VAD and
endpointer see it. This removes DC offset, mains hum and rumble. Each
utterance is then peak-normalised (gain capped at +30 dB) on the
transcription thread before whisper. miniaudio's resampler uses its steepest
anti-aliasing filter. `tools/echo-bench` measures the effect of all of these
on accuracy; see the README's *Latency and tuning* section.

## 4. Models and backends

| Class | Model | Backend selection |
|---|---|---|
| `SileroVad` (`src/vad/`) | `silero_vad.onnx` (RNN, 2 MB) | ONNX Runtime, **CPU**, 1 thread |
| `WhisperTranscriber` (`src/stt/`) | GGML whisper model | GPU via ggml if compiled in and present, else CPU |
| `GrammarCorrector` (`src/grammar/`) | GGUF chat model | same as whisper |

The GPU backend is chosen at **build** time (`cmake/GpuBackend.cmake`: Metal,
CUDA, Vulkan or none). At **run** time `findGpuDevice()`
(`src/util/ggml_utils.cpp`) asks ggml's device registry whether a GPU
actually exists. Loading is attempted on the GPU first and repeated on the CPU
if that fails.

whisper.cpp and llama.cpp both bundle the **ggml** tensor library. They must
share one copy, so `cmake/Llama.cmake` is included after `cmake/Whisper.cmake`
and llama.cpp reuses the existing `ggml` target. The pinned versions ship
identical ggml sources.

### Text clean-up: rules first, then (optionally) the LLM

On the output thread every sentence first goes through `removeFillerWords()`
(`src/core/text_utils.cpp`). It's a deterministic, instant pass that drops
"um/uh/er", stutters ("the the") and comma-delimited "you know", "I mean" and
"like". It's on by default (`remove_filler_words`). The LLM, if enabled, then
only has to fix what rules can't, which matters because a 0.5B model is
unreliable on messy input.

### Grammar correction details

`GrammarCorrector` builds a chat prompt: system instructions, five example
corrections, then your sentence. It formats it with the model's own chat
template (`llama_chat_apply_template`) and decodes greedily. Two details keep
it fast and safe:

- **Prompt caching.** The instructions and examples are identical every time,
  so their KV-cache entries are kept between calls. Only the new sentence is
  processed (`generate()` compares token prefixes).
- **Sanity checks.** Tiny models occasionally answer a question instead of
  correcting it, or change who a sentence is about ("we could" → "you
  could"). The input is returned unchanged if the output is empty, less than
  half as long, much longer, or mentions a person (I, we, you, he, she, they)
  more often than the input did.

## 5. C++ patterns you'll see

| Pattern | Where | Why |
|---|---|---|
| **RAII** (resource freed in a destructor) | `WhisperTranscriber`, `GrammarCorrector`, `AudioCapture`, `std::lock_guard` | No leaks, even when exceptions are thrown |
| **pimpl** (`struct Impl; std::unique_ptr<Impl>`) | `SpeechListener`, `AudioCapture` | Public headers stay small, with no third-party includes |
| **Deleted copy constructor** | classes owning threads/models | Copying a microphone or a loaded model makes no sense |
| **Lambdas** | thread bodies, callbacks, miniaudio callback | Short inline functions; a capture-less lambda converts to a C function pointer |
| **`std::function`** | `setOnSentence` | Accept any callable: lambda, function, functor |
| **`std::optional`** | held transcripts, in-flight revisions | "Maybe a value" without magic numbers |
| **Exceptions for setup errors** | constructors, `start()` | A missing model is reported at load time, not halfway through |
| **Stub implementation** | `grammar_corrector_stub.cpp` | Feature switched off at build time without `#ifdef`s everywhere |

## 6. Threading rules (summary)

- Only the VAD thread touches the `Endpointer` and the "held" state, so that
  logic needs no locks.
- Threads communicate only through queues (and two atomics for stale-job
  skipping).
- Callbacks are copied under a mutex and **called without holding it**, so a
  callback may call `setOnSentence()` itself without deadlocking.
- `stop()` closes the queues (waking any sleeping thread) and `join()`s every
  thread before returning.
