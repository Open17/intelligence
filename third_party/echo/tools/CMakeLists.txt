# -----------------------------------------------------------------------------
#  Developer tools (not needed to use the library).
#   echo-bench: transcription accuracy (WER) and speed benchmark.
# -----------------------------------------------------------------------------
add_executable(echo-bench echo-bench/main.cpp)
target_link_libraries(echo-bench PRIVATE echo::echo whisper onnxruntime::onnxruntime)
# The benchmark drives internal classes directly, so it needs src/ too.
target_include_directories(echo-bench PRIVATE ${PROJECT_SOURCE_DIR}/src)
echo_set_warnings(echo-bench)
echo_copy_runtime_dlls(echo-bench)
