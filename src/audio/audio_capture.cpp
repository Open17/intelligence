// =============================================================================
//  audio_capture.cpp - see audio_capture.hpp
// =============================================================================
#include "audio/audio_capture.hpp"

#include <miniaudio.h>

#include <stdexcept>

namespace echo {

// Everything miniaudio-specific lives here, invisible to users of the header.
struct AudioCapture::Impl {
    ma_context context{};
    ma_device device{};
    bool context_ready = false;
    bool device_ready = false;
};

namespace {

// Throws a readable error if a miniaudio call failed.
void check(ma_result result, const char* what) {
    if (result != MA_SUCCESS) {
        throw std::runtime_error(std::string(what) + ": " + ma_result_description(result));
    }
}

}  // namespace

AudioCapture::AudioCapture(SpscRingBuffer<float>& sink, int sample_rate, int device_index)
    : impl_(std::make_unique<Impl>()),
      sink_(sink),
      sample_rate_(sample_rate),
      device_index_(device_index) {}

AudioCapture::~AudioCapture() {
    stop();
    if (impl_->context_ready) {
        ma_context_uninit(&impl_->context);
    }
}

void AudioCapture::start() {
    if (impl_->device_ready) {
        return;  // already running
    }

    if (!impl_->context_ready) {
        // A context talks to the OS audio system (CoreAudio, WASAPI, ALSA, ...).
        check(ma_context_init(nullptr, 0, nullptr, &impl_->context), "audio context init failed");
        impl_->context_ready = true;
    }

    ma_device_config config = ma_device_config_init(ma_device_type_capture);
    config.capture.format = ma_format_f32;  // 32-bit float, range [-1, 1]
    config.capture.channels = 1;            // mono
    config.sampleRate = static_cast<ma_uint32>(sample_rate_);
    config.performanceProfile = ma_performance_profile_low_latency;
    // Most microphones run at 44.1/48 kHz and miniaudio resamples to 16 kHz.
    // Use the steepest anti-aliasing filter it offers (8th order instead of the
    // default 4th) so high-frequency noise cannot fold down into speech range.
    config.resampling.linear.lpfOrder = MA_MAX_FILTER_ORDER;
    config.pUserData = this;  // handed back to us in the callback below

    // The data callback. It runs on miniaudio's real-time thread, so it ONLY
    // copies samples into the ring buffer: no locks, no allocation, no printing.
    // A lambda without captures converts to a plain C function pointer.
    config.dataCallback = [](ma_device* device, void* /*output*/, const void* input,
                             ma_uint32 frame_count) {
        auto* self = static_cast<AudioCapture*>(device->pUserData);
        const auto* samples = static_cast<const float*>(input);
        const std::size_t written = self->sink_.push(samples, frame_count);
        if (written < frame_count) {
            self->dropped_.fetch_add(frame_count - written, std::memory_order_relaxed);
        }
    };

    // Pick a specific device if the caller asked for one.
    ma_device_info* capture_infos = nullptr;
    ma_uint32 capture_count = 0;
    if (device_index_ >= 0) {
        check(ma_context_get_devices(&impl_->context, nullptr, nullptr, &capture_infos,
                                     &capture_count),
              "listing audio devices failed");
        if (static_cast<ma_uint32>(device_index_) >= capture_count) {
            throw std::runtime_error("audio device index " + std::to_string(device_index_) +
                                     " is out of range (found " + std::to_string(capture_count) +
                                     " capture devices)");
        }
        config.capture.pDeviceID = &capture_infos[device_index_].id;
    }

    check(ma_device_init(&impl_->context, &config, &impl_->device), "opening microphone failed");
    impl_->device_ready = true;

    const ma_result started = ma_device_start(&impl_->device);
    if (started != MA_SUCCESS) {
        ma_device_uninit(&impl_->device);
        impl_->device_ready = false;
        check(started, "starting microphone failed");
    }
}

void AudioCapture::stop() {
    if (impl_->device_ready) {
        // ma_device_uninit() stops the device and waits for the callback to finish.
        ma_device_uninit(&impl_->device);
        impl_->device_ready = false;
    }
}

std::string AudioCapture::deviceName() const {
    if (!impl_->device_ready) {
        return "(not started)";
    }
    char name[256] = {};
    ma_device_get_name(const_cast<ma_device*>(&impl_->device), ma_device_type_capture, name,
                       sizeof(name), nullptr);
    return name;
}

std::vector<std::string> AudioCapture::listDevices() {
    ma_context context;
    check(ma_context_init(nullptr, 0, nullptr, &context), "audio context init failed");

    ma_device_info* infos = nullptr;
    ma_uint32 count = 0;
    std::vector<std::string> names;
    if (ma_context_get_devices(&context, nullptr, nullptr, &infos, &count) == MA_SUCCESS) {
        for (ma_uint32 i = 0; i < count; ++i) {
            names.emplace_back(infos[i].name);
        }
    }
    ma_context_uninit(&context);
    return names;
}

}  // namespace echo
