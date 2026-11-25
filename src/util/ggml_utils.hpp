// =============================================================================
//  ggml_utils.hpp - helpers shared by everything built on ggml
//                   (whisper.cpp now, llama.cpp for the grammar stage)
// =============================================================================
#pragma once

#include <string>

namespace echo {

/// Name of the GPU backend compiled into this build: "Metal", "CUDA",
/// "Vulkan" or "none" (chosen with the ECHO_GPU_BACKEND CMake option).
const char* compiledGpuBackend();

/// Describes the first GPU that ggml can use, e.g. "MTL0 (Apple M4)".
/// Returns an empty string when there is no usable GPU (or no GPU support
/// was compiled in) - in that case ggml runs everything on the CPU.
std::string findGpuDevice();

/// Threads to use for CPU inference: `requested` if > 0, otherwise the number
/// of CPU cores reported by the OS.
int resolveThreadCount(int requested);

/// whisper.cpp / llama.cpp / ggml are chatty by default. This routes their log
/// output through a filter: errors are always shown, everything else only
/// when `verbose` is true.
void configureGgmlLogging(bool verbose);

/// Temporarily hides ggml/whisper error messages too (used while a
/// deliberately cancelled run reports its expected "failure").
void suppressGgmlErrors(bool suppress);

}  // namespace echo
