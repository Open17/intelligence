# =============================================================================
#  echo - low-latency, cross-platform speech-to-text
# =============================================================================
#
#  Quick start:
#     cmake --preset default        # configure (downloads dependencies)
#     cmake --build --preset default
#
#  Everything that the project downloads is pulled in with CMake's
#  FetchContent module - see the files in cmake/ for the details.
# =============================================================================

cmake_minimum_required(VERSION 3.21)

project(echo
    VERSION 0.2.0
    DESCRIPTION "Low-latency speech-to-text: mic -> VAD -> whisper.cpp -> callback"
    LANGUAGES C CXX)

# --- Language standard -------------------------------------------------------
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)          # use -std=c++17, not -std=gnu++17

# Default to an optimised build: speech models are painfully slow in Debug.
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

# Put every executable and DLL in one folder so Windows can find the DLLs.
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")

# Let editors (VS Code, clangd) understand the project.
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# Our own CMake helper modules live in cmake/.
list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")

# Downloaded archives get "now" as their file timestamps, so a changed
# dependency version always triggers a rebuild.
if(POLICY CMP0135)
    cmake_policy(SET CMP0135 NEW)
endif()

# Let plain `set(SOME_OPTION OFF)` configure third-party projects, even ones
# written for old CMake versions whose option() would otherwise ignore it.
set(CMAKE_POLICY_DEFAULT_CMP0077 NEW)

# --- Project options -----------------------------------------------------------
#  Change these on the command line, e.g.  cmake -B build -DECHO_BUILD_TESTS=OFF
option(ECHO_BUILD_CLI   "Build the echo-cli demo program"  ON)
option(ECHO_BUILD_TESTS "Build the unit tests"              ON)
option(ECHO_BUILD_EXAMPLES "Build the example programs in examples/" ON)
option(ECHO_BUILD_TOOLS "Build developer tools such as echo-bench" ON)
option(ECHO_ENABLE_GRAMMAR "Build the optional llama.cpp grammar-correction stage" ON)
option(ECHO_WARNINGS_AS_ERRORS "Treat compiler warnings in echo's own code as errors" OFF)

include(CompilerWarnings)

# --- Third-party dependencies (downloaded at configure time) -----------------------
include(Miniaudio)       # microphone capture
include(OnnxRuntime)     # runs the Silero VAD model
include(GpuBackend)      # Metal / CUDA / Vulkan / CPU (must come before whisper)
include(Whisper)         # whisper.cpp speech recognition
if(ECHO_ENABLE_GRAMMAR)
    include(Llama)       # llama.cpp grammar correction (must come after whisper)
endif()

# --- Sub-projects ----------------------------------------------------------------
add_subdirectory(src)

if(ECHO_BUILD_CLI)
    add_subdirectory(apps/echo-cli)
endif()

if(ECHO_BUILD_EXAMPLES)
    add_subdirectory(examples)
endif()

if(ECHO_BUILD_TOOLS)
    add_subdirectory(tools)
endif()

if(ECHO_BUILD_TESTS)
    enable_testing()
    add_subdirectory(tests)
endif()
