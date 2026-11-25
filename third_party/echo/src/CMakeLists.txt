# -----------------------------------------------------------------------------
#  Libraries built from src/
#
#   echo::core  - pure C++17 building blocks (no third-party dependencies).
#                 Small and fast to compile, so the unit tests link only this.
#   echo::echo  - the full speech-to-text library (audio, VAD, whisper, ...).
#                 Its public headers live in include/echo/.
# -----------------------------------------------------------------------------

# --- echo::core ------------------------------------------------------------------
add_library(echo_core STATIC
    core/audio_dsp.cpp
    core/endpointer.cpp
    core/text_utils.cpp)
add_library(echo::core ALIAS echo_core)
target_include_directories(echo_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_compile_features(echo_core PUBLIC cxx_std_17)
echo_set_warnings(echo_core)

# --- echo::echo ------------------------------------------------------------------
add_library(echo STATIC
    speech_listener.cpp
    audio/audio_capture.cpp
    audio/audio_file.cpp
    vad/silero_vad.cpp
    stt/whisper_transcriber.cpp
    util/ggml_utils.cpp)
add_library(echo::echo ALIAS echo)

target_include_directories(echo
    PUBLIC  ${PROJECT_SOURCE_DIR}/include   # #include "echo/..."  (public API)
    PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})    # #include "audio/..." (internals)

# PUBLIC dependencies are visible to users of echo; PRIVATE ones are
# implementation details (still linked, but their headers are not exposed).
target_link_libraries(echo
    PUBLIC  echo::core
    PRIVATE echo_miniaudio
            onnxruntime::onnxruntime
            whisper
            ggml)

# Optional grammar-correction stage (llama.cpp), see cmake/Llama.cmake.
# When it is switched off, a stub with the same class is compiled instead, so
# the rest of the code needs no #ifdefs.
if(ECHO_ENABLE_GRAMMAR)
    target_sources(echo PRIVATE grammar/grammar_corrector.cpp)
    target_link_libraries(echo PRIVATE llama)
    target_compile_definitions(echo PRIVATE ECHO_HAS_GRAMMAR=1)
else()
    target_sources(echo PRIVATE grammar/grammar_corrector_stub.cpp)
endif()

# Tell the code which GPU backend was compiled in (shown at startup).
target_compile_definitions(echo PRIVATE ECHO_GPU_BACKEND_NAME="${ECHO_GPU_BACKEND_NAME}")

echo_set_warnings(echo)
