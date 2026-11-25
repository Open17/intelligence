# -----------------------------------------------------------------------------
#  Llama.cmake - llama.cpp, used for the optional grammar-correction stage
#
#  Creates the target  llama  and the compile definition ECHO_HAS_GRAMMAR.
#  Turn the whole stage off at build time with  -DECHO_ENABLE_GRAMMAR=OFF
#  (faster builds, e.g. on a Raspberry Pi if you will not use it).
#
#  IMPORTANT: llama.cpp and whisper.cpp both bundle the "ggml" tensor library.
#  Linking two copies would clash, so this file must be included AFTER
#  Whisper.cmake: llama.cpp sees that a `ggml` target already exists and
#  reuses it. The pinned versions below ship byte-identical ggml sources, and
#  this also means llama.cpp automatically gets the same GPU backend.
# -----------------------------------------------------------------------------
include(FetchContent)

set(ECHO_LLAMA_VERSION "v1.0.0")

if(NOT TARGET ggml)
    message(FATAL_ERROR "Include Whisper.cmake before Llama.cmake (they must share ggml)")
endif()

# Only the core library: no tools, examples, server, tests or HTTP support.
set(LLAMA_BUILD_COMMON   OFF)
set(LLAMA_BUILD_TESTS    OFF)
set(LLAMA_BUILD_TOOLS    OFF)
set(LLAMA_BUILD_EXAMPLES OFF)
set(LLAMA_BUILD_SERVER   OFF)
set(LLAMA_BUILD_APP      OFF)
set(LLAMA_OPENSSL        OFF)
set(LLAMA_ALL_WARNINGS   OFF)

FetchContent_Declare(llama
    URL      https://github.com/ggml-org/llama.cpp/archive/refs/tags/${ECHO_LLAMA_VERSION}.tar.gz
    URL_HASH SHA256=3f5f750e36326aea4d65c7623e7d597a33e395d65f4d76e5e0532171f252532b)

message(STATUS "llama.cpp: ${ECHO_LLAMA_VERSION} (sharing whisper.cpp's ggml)")
FetchContent_MakeAvailable(llama)
