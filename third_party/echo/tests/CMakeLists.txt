# -----------------------------------------------------------------------------
#  Unit tests. Run them with:   ctest --preset default
#  They only test pure logic (no models or microphone needed).
# -----------------------------------------------------------------------------

# echo_add_test(<name>) builds tests/<name>.cpp into an executable and
# registers it with CTest.
function(echo_add_test name)
    add_executable(${name} ${name}.cpp)
    target_link_libraries(${name} PRIVATE echo::core)
    target_include_directories(${name} PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
    echo_set_warnings(${name})
    add_test(NAME ${name} COMMAND ${name})
endfunction()

echo_add_test(test_ring_buffer)
echo_add_test(test_text_utils)
echo_add_test(test_endpointer)
echo_add_test(test_blocking_queue)
echo_add_test(test_audio_dsp)

# -----------------------------------------------------------------------------
#  Model tests: these load real model files from models/. When a model has not
#  been downloaded the test exits with code 77, which CTest reports as
#  "skipped" rather than "failed".
# -----------------------------------------------------------------------------
function(echo_add_model_test name)
    add_executable(${name} ${name}.cpp)
    target_link_libraries(${name} PRIVATE echo::echo)
    target_include_directories(${name} PRIVATE ${CMAKE_CURRENT_SOURCE_DIR} ${PROJECT_SOURCE_DIR}/src)
    echo_set_warnings(${name})
    echo_copy_runtime_dlls(${name})
    add_test(NAME ${name} COMMAND ${name} ${PROJECT_SOURCE_DIR}/models ${ARGN})
    set_tests_properties(${name} PROPERTIES SKIP_RETURN_CODE 77 LABELS models)
endfunction()

echo_add_model_test(test_silero_vad)
target_link_libraries(test_silero_vad PRIVATE onnxruntime::onnxruntime)  # includes a private header

# Transcribes the JFK sample that ships with whisper.cpp.
echo_add_model_test(test_whisper_transcriber ${ECHO_WHISPER_SAMPLES_DIR}/jfk.wav)
target_link_libraries(test_whisper_transcriber PRIVATE whisper)

# Full pipeline: VAD -> endpointing -> whisper -> callback, fed via pushAudio().
echo_add_model_test(test_speech_listener ${ECHO_WHISPER_SAMPLES_DIR}/jfk.wav)

# Grammar correction with the Qwen2.5 model (only when llama.cpp is built).
if(ECHO_ENABLE_GRAMMAR)
    echo_add_model_test(test_grammar_corrector)
    target_link_libraries(test_grammar_corrector PRIVATE llama)
    target_compile_definitions(test_grammar_corrector PRIVATE ECHO_HAS_GRAMMAR=1)
endif()
