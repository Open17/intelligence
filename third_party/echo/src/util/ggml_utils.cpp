// =============================================================================
//  ggml_utils.cpp - see ggml_utils.hpp
// =============================================================================
#include "util/ggml_utils.hpp"

#include <ggml-backend.h>
#include <ggml.h>
#include <whisper.h>

#if defined(ECHO_HAS_GRAMMAR)
#include <llama.h>
#endif

#include <atomic>
#include <cstdio>
#include <thread>

namespace echo {

namespace {

std::atomic<bool> g_verbose{false};
std::atomic<bool> g_suppress_errors{false};

// Signature required by ggml_log_set / whisper_log_set. It is a plain C
// function pointer, so it cannot capture anything - hence the global flag.
void logFilter(ggml_log_level level, const char* text, void* /*user_data*/) {
    if (g_suppress_errors.load(std::memory_order_relaxed)) return;
    if (level == GGML_LOG_LEVEL_ERROR || g_verbose.load(std::memory_order_relaxed)) {
        std::fputs(text, stderr);
    }
}

}  // namespace

const char* compiledGpuBackend() {
#if defined(ECHO_GPU_BACKEND_NAME)
    return ECHO_GPU_BACKEND_NAME;  // a string literal defined by CMake
#else
    return "none";
#endif
}

std::string findGpuDevice() {
    // ggml keeps a registry of every compute device its backends found
    // (the CPU is always one of them). Same search whisper.cpp itself does.
    for (std::size_t i = 0; i < ggml_backend_dev_count(); ++i) {
        ggml_backend_dev_t dev = ggml_backend_dev_get(i);
        const auto type = ggml_backend_dev_type(dev);
        if (type == GGML_BACKEND_DEVICE_TYPE_GPU || type == GGML_BACKEND_DEVICE_TYPE_IGPU) {
            return std::string(ggml_backend_dev_name(dev)) + " (" +
                   ggml_backend_dev_description(dev) + ")";
        }
    }
    return {};
}

int resolveThreadCount(int requested) {
    if (requested > 0) {
        return requested;
    }
    // hardware_concurrency() may return 0 if it cannot tell.
    const unsigned cores = std::thread::hardware_concurrency();
    return cores > 0 ? static_cast<int>(cores) : 4;
}

void configureGgmlLogging(bool verbose) {
    g_verbose.store(verbose, std::memory_order_relaxed);
    ggml_log_set(logFilter, nullptr);
    whisper_log_set(logFilter, nullptr);
#if defined(ECHO_HAS_GRAMMAR)
    llama_log_set(logFilter, nullptr);
#endif
}

void suppressGgmlErrors(bool suppress) {
    g_suppress_errors.store(suppress, std::memory_order_relaxed);
}

}  // namespace echo
