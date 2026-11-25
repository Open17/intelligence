# -----------------------------------------------------------------------------
#  Whisper.cmake - whisper.cpp (speech recognition) and its ggml tensor library
#
#  Creates the targets  whisper  and  ggml  (from whisper.cpp's own CMake).
#  Any ggml option (GGML_NATIVE, GGML_METAL, GGML_CUDA, ...) can still be
#  passed on the command line, e.g.  -DGGML_NATIVE=OFF
# -----------------------------------------------------------------------------
include(FetchContent)

set(ECHO_WHISPER_VERSION "v1.9.6")

# Build whisper.cpp as a plain static library inside our project: no
# examples, tests or server, and no shared libraries (= no DLL juggling).
set(WHISPER_BUILD_TESTS    OFF)
set(WHISPER_BUILD_EXAMPLES OFF)
set(WHISPER_BUILD_SERVER   OFF)
set(WHISPER_ALL_WARNINGS   OFF)
set(BUILD_SHARED_LIBS      OFF)
set(GGML_BUILD_TESTS       OFF)
set(GGML_BUILD_EXAMPLES    OFF)

FetchContent_Declare(whisper
    URL      https://github.com/ggml-org/whisper.cpp/archive/refs/tags/${ECHO_WHISPER_VERSION}.tar.gz
    URL_HASH SHA256=7ab15dc88022d2425ad94dcffd8abbf1b6d7018089b4e48d6b40780738e0b445)

message(STATUS "whisper.cpp: ${ECHO_WHISPER_VERSION}")
FetchContent_MakeAvailable(whisper)

# whisper.cpp ships a short test recording; remember where it is so the tests
# can transcribe it.
set(ECHO_WHISPER_SAMPLES_DIR "${whisper_SOURCE_DIR}/samples" CACHE INTERNAL "")
