// =============================================================================
//  audio_capture.hpp - microphone input via miniaudio
// =============================================================================
//
//  AudioCapture opens a capture device that delivers 16 kHz, mono, 32-bit
//  float samples (miniaudio converts/resamples from whatever the hardware
//  provides). Every block the OS hands us is pushed into a lock-free ring
//  buffer; the callback does nothing else, so it can never stall the audio
//  thread.
//
//  The miniaudio types are hidden behind a "pimpl" (pointer to implementation)
//  so that this header stays tiny and does not drag the ~97k-line miniaudio.h
//  into every file that includes it.
// =============================================================================
#pragma once

#include <atomic>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include "core/ring_buffer.hpp"

namespace echo {

class AudioCapture {
public:
    /// @param sink          where captured samples are written (must outlive us)
    /// @param sample_rate   requested rate in Hz (echo always uses 16000)
    /// @param device_index  index from listDevices(), or -1 for the system default
    AudioCapture(SpscRingBuffer<float>& sink, int sample_rate, int device_index = -1);
    ~AudioCapture();

    AudioCapture(const AudioCapture&) = delete;
    AudioCapture& operator=(const AudioCapture&) = delete;

    /// Opens the device and starts streaming. Throws std::runtime_error on failure.
    void start();
    /// Stops streaming. Safe to call more than once.
    void stop();

    /// Human-readable name of the device in use (valid after start()).
    std::string deviceName() const;

    /// Samples dropped because the ring buffer was full (the consumer fell behind).
    std::uint64_t droppedSamples() const { return dropped_.load(std::memory_order_relaxed); }

    /// Names of all capture devices, in the order expected by `device_index`.
    static std::vector<std::string> listDevices();

private:
    struct Impl;                  // defined in audio_capture.cpp
    std::unique_ptr<Impl> impl_;

    SpscRingBuffer<float>& sink_;
    int sample_rate_;
    int device_index_;
    std::atomic<std::uint64_t> dropped_{0};
};

}  // namespace echo
