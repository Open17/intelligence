# -----------------------------------------------------------------------------
#  echo-cli: the demo program. Built into build/<preset>/bin/echo-cli
# -----------------------------------------------------------------------------
add_executable(echo-cli main.cpp)
target_link_libraries(echo-cli PRIVATE echo::echo)
target_compile_definitions(echo-cli PRIVATE ECHO_VERSION="${PROJECT_VERSION}")
echo_set_warnings(echo-cli)
echo_copy_runtime_dlls(echo-cli)   # Windows: put onnxruntime.dll next to the .exe

if(APPLE)
    # macOS asks for microphone permission per app. Embedding an Info.plist
    # with a usage description makes the permission prompt explain itself.
    target_link_options(echo-cli PRIVATE
        "LINKER:-sectcreate,__TEXT,__info_plist,${CMAKE_CURRENT_SOURCE_DIR}/Info.plist")
endif()
