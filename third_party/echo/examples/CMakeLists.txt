# -----------------------------------------------------------------------------
#  Small example programs showing how to use the library.
# -----------------------------------------------------------------------------
add_executable(echo-minimal minimal/main.cpp)
target_link_libraries(echo-minimal PRIVATE echo::echo)
echo_set_warnings(echo-minimal)
echo_copy_runtime_dlls(echo-minimal)

if(APPLE)
    # Same microphone-permission Info.plist as echo-cli.
    target_link_options(echo-minimal PRIVATE
        "LINKER:-sectcreate,__TEXT,__info_plist,${PROJECT_SOURCE_DIR}/apps/echo-cli/Info.plist")
endif()
