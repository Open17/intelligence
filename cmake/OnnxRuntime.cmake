# -----------------------------------------------------------------------------
#  OnnxRuntime.cmake - prebuilt ONNX Runtime (CPU) for the Silero VAD model
#
#  Creates the imported target  onnxruntime::onnxruntime
#
#  Two ways to get ONNX Runtime:
#   1. (default) Download Microsoft's official NuGet package. A .nupkg is just
#      a zip file, and it contains prebuilt native libraries for every
#      platform we support plus the C/C++ headers.
#   2. Use a copy you already have:  -DONNXRUNTIME_ROOT=/path/to/onnxruntime
#      (a folder with include/ and lib/, e.g. Homebrew's
#      /opt/homebrew/opt/onnxruntime or an extracted release archive).
#
#  We only use the CPU execution provider: Silero VAD is tiny (~2 MB) and
#  runs in well under a millisecond per 32 ms frame, so a GPU would only add
#  overhead.
# -----------------------------------------------------------------------------
include(FetchContent)

set(ECHO_ONNXRUNTIME_VERSION "1.32.0" CACHE STRING "ONNX Runtime version to download")
set(ONNXRUNTIME_ROOT "" CACHE PATH "Use an existing ONNX Runtime install instead of downloading one")

# echo_copy_runtime_dlls(<target>): on Windows, copy onnxruntime.dll (and any
# other DLLs the target needs) next to the .exe after every build. On macOS
# and Linux CMake records an RPATH instead, so nothing needs copying.
function(echo_copy_runtime_dlls target)
    if(WIN32)
        add_custom_command(TARGET ${target} POST_BUILD
            COMMAND ${CMAKE_COMMAND} -E copy_if_different
                    $<TARGET_RUNTIME_DLLS:${target}> $<TARGET_FILE_DIR:${target}>
            COMMAND_EXPAND_LISTS)
    endif()
endfunction()

# --- Option 2: a local install ---------------------------------------------------
if(ONNXRUNTIME_ROOT)
    find_path(ORT_INCLUDE_DIR onnxruntime_cxx_api.h
        PATHS "${ONNXRUNTIME_ROOT}/include" "${ONNXRUNTIME_ROOT}/include/onnxruntime"
        NO_DEFAULT_PATH REQUIRED)
    find_library(ORT_LIBRARY NAMES onnxruntime
        PATHS "${ONNXRUNTIME_ROOT}/lib" NO_DEFAULT_PATH REQUIRED)

    add_library(onnxruntime::onnxruntime UNKNOWN IMPORTED GLOBAL)
    set_target_properties(onnxruntime::onnxruntime PROPERTIES
        IMPORTED_LOCATION "${ORT_LIBRARY}"
        INTERFACE_INCLUDE_DIRECTORIES "${ORT_INCLUDE_DIR}")
    message(STATUS "ONNX Runtime: using ${ORT_LIBRARY}")
    return()
endif()

# --- Option 1: download the NuGet package ------------------------------------------
# Work out the NuGet "runtime identifier" (RID) for this machine.
string(TOLOWER "${CMAKE_SYSTEM_PROCESSOR}" _ort_cpu)
if(APPLE AND _ort_cpu MATCHES "arm64|aarch64")
    set(_ort_rid "osx-arm64")
elseif(WIN32 AND _ort_cpu MATCHES "arm64")
    set(_ort_rid "win-arm64")
elseif(WIN32)
    set(_ort_rid "win-x64")
elseif(UNIX AND NOT APPLE AND _ort_cpu MATCHES "aarch64|arm64")
    set(_ort_rid "linux-arm64")             # Raspberry Pi 5, Jetson, ...
elseif(UNIX AND NOT APPLE AND _ort_cpu MATCHES "x86_64|amd64")
    set(_ort_rid "linux-x64")
else()
    message(FATAL_ERROR
        "No prebuilt ONNX Runtime for ${CMAKE_SYSTEM_NAME}/${CMAKE_SYSTEM_PROCESSOR}. "
        "Install it yourself and pass -DONNXRUNTIME_ROOT=<path>.")
endif()

FetchContent_Declare(onnxruntime
    URL https://api.nuget.org/v3-flatcontainer/microsoft.ml.onnxruntime/${ECHO_ONNXRUNTIME_VERSION}/microsoft.ml.onnxruntime.${ECHO_ONNXRUNTIME_VERSION}.nupkg
    URL_HASH SHA256=a9150bc2992cbbb6722070da9910e1ffa989a8bfc67bdd5bb8e745ca755641db
    DOWNLOAD_NAME onnxruntime.zip          # tell CMake it is a zip archive
    SOURCE_SUBDIR _download_only_)          # nothing to build, just unpack
message(STATUS "ONNX Runtime: downloading ${ECHO_ONNXRUNTIME_VERSION} (${_ort_rid}) - large, one-time")
FetchContent_MakeAvailable(onnxruntime)

set(_ort_native "${onnxruntime_SOURCE_DIR}/runtimes/${_ort_rid}/native")
set(_ort_include "${onnxruntime_SOURCE_DIR}/build/native/include")

# The shared library must be found again when the program RUNS, under the
# name recorded inside it (its "install name" on macOS / "SONAME" on Linux).
# The NuGet files use a different name, so we copy them to the expected one.
set(_ort_libdir "${CMAKE_BINARY_DIR}/onnxruntime/lib")
file(MAKE_DIRECTORY "${_ort_libdir}")

add_library(onnxruntime::onnxruntime SHARED IMPORTED GLOBAL)
set_target_properties(onnxruntime::onnxruntime PROPERTIES
    INTERFACE_INCLUDE_DIRECTORIES "${_ort_include}")

if(WIN32)
    # Windows: link against the .lib, ship the .dll next to the executable.
    set_target_properties(onnxruntime::onnxruntime PROPERTIES
        IMPORTED_IMPLIB   "${_ort_native}/onnxruntime.lib"
        IMPORTED_LOCATION "${_ort_native}/onnxruntime.dll")
elseif(APPLE)
    file(COPY_FILE "${_ort_native}/libonnxruntime.dylib"
                   "${_ort_libdir}/libonnxruntime.1.dylib" ONLY_IF_DIFFERENT)
    set_target_properties(onnxruntime::onnxruntime PROPERTIES
        IMPORTED_LOCATION "${_ort_libdir}/libonnxruntime.1.dylib")
else()
    file(COPY_FILE "${_ort_native}/libonnxruntime.so"
                   "${_ort_libdir}/libonnxruntime.so.1" ONLY_IF_DIFFERENT)
    set_target_properties(onnxruntime::onnxruntime PROPERTIES
        IMPORTED_LOCATION "${_ort_libdir}/libonnxruntime.so.1"
        IMPORTED_SONAME   "libonnxruntime.so.1")
endif()
