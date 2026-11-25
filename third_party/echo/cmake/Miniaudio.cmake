# -----------------------------------------------------------------------------
#  Miniaudio.cmake - microphone capture + audio file decoding
#
#  miniaudio (https://miniaud.io) is a single-header C library. We download a
#  pinned release and compile its implementation exactly once into a small
#  static library called `echo_miniaudio`.
# -----------------------------------------------------------------------------
include(FetchContent)

set(ECHO_MINIAUDIO_VERSION "0.11.26")

FetchContent_Declare(miniaudio
    URL      https://github.com/mackron/miniaudio/archive/refs/tags/${ECHO_MINIAUDIO_VERSION}.tar.gz
    URL_HASH SHA256=838ba55a21c9142adf7b76bdc4351e5cd8b7ae128f46b25ec25f94ae9969c600
    # Point SOURCE_SUBDIR at a folder without a CMakeLists.txt so FetchContent
    # only downloads the code and does not try to build miniaudio's own project.
    SOURCE_SUBDIR _download_only_)
FetchContent_MakeAvailable(miniaudio)

# miniaudio.c is just `#define MINIAUDIO_IMPLEMENTATION` + `#include "miniaudio.h"`.
add_library(echo_miniaudio STATIC ${miniaudio_SOURCE_DIR}/miniaudio.c)
target_include_directories(echo_miniaudio SYSTEM PUBLIC ${miniaudio_SOURCE_DIR})

# Turn off the parts of miniaudio we do not use (smaller, faster build).
target_compile_definitions(echo_miniaudio PUBLIC
    MA_NO_ENCODING        # we only read audio files, never write them
    MA_NO_GENERATION      # no tone/noise generators
    MA_NO_ENGINE          # no high-level playback engine
    MA_NO_NODE_GRAPH)

if(APPLE)
    # Link CoreAudio directly instead of loading it at runtime with dlopen().
    target_compile_definitions(echo_miniaudio PUBLIC MA_NO_RUNTIME_LINKING)
    target_link_libraries(echo_miniaudio PUBLIC
        "-framework CoreFoundation" "-framework CoreAudio" "-framework AudioToolbox")
elseif(UNIX)
    # Linux: ALSA/PulseAudio/JACK are loaded at runtime, which needs libdl.
    find_package(Threads REQUIRED)
    target_link_libraries(echo_miniaudio PUBLIC Threads::Threads ${CMAKE_DL_LIBS} m)
endif()
