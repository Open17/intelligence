# =============================================================================
#  echo-stream - stdin PCM -> echo speech-to-text -> JSON lines on stdout
# =============================================================================
#
#     cmake -S server/echo_stream -B build/echo-stream -G Ninja
#     cmake --build build/echo-stream
#
#  echo is vendored in third_party/echo (git subtree) and built as part of
#  this project with everything the server does not need switched off.
# =============================================================================
cmake_minimum_required(VERSION 3.21)
project(echo_stream VERSION 0.1.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

set(ECHO_SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../../third_party/echo"
    CACHE PATH "Path to the echo source tree")

# Only the library: no CLI, tests, examples, benchmark or grammar LLM.
set(ECHO_BUILD_CLI OFF CACHE BOOL "" FORCE)
set(ECHO_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(ECHO_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(ECHO_BUILD_TOOLS OFF CACHE BOOL "" FORCE)
set(ECHO_ENABLE_GRAMMAR OFF CACHE BOOL "" FORCE)
if(NOT APPLE)
    # Cloud VMs have no usable GPU; this skips CUDA/Vulkan detection entirely.
    set(ECHO_GPU_BACKEND none CACHE STRING "" FORCE)
endif()

add_subdirectory("${ECHO_SOURCE_DIR}" echo EXCLUDE_FROM_ALL)

add_executable(echo-stream main.cpp)
target_link_libraries(echo-stream PRIVATE echo::echo)
if(MSVC)
    target_compile_options(echo-stream PRIVATE /W4)
else()
    target_compile_options(echo-stream PRIVATE -Wall -Wextra -Wpedantic)
endif()

install(TARGETS echo-stream RUNTIME DESTINATION bin)
