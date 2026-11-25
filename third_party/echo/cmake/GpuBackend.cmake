# -----------------------------------------------------------------------------
#  GpuBackend.cmake - choose which GPU backend ggml (whisper.cpp / llama.cpp)
#                     is compiled with
#
#    -DECHO_GPU_BACKEND=auto     (default) pick the best one for this machine
#    -DECHO_GPU_BACKEND=metal    Apple GPUs (macOS)
#    -DECHO_GPU_BACKEND=cuda     NVIDIA GPUs (needs the CUDA Toolkit)
#    -DECHO_GPU_BACKEND=vulkan   AMD / Intel / NVIDIA GPUs (needs the Vulkan SDK)
#    -DECHO_GPU_BACKEND=none     CPU only
#
#  "auto" means:
#    macOS                     -> metal
#    CUDA Toolkit installed    -> cuda
#    Vulkan SDK installed      -> vulkan   (not on ARM Linux, see below)
#    otherwise                 -> none
#
#  Whatever is compiled in, the program still checks at RUN time whether a
#  GPU is really there and falls back to the CPU if not. The CPU backend is
#  always built.
#
#  Sets ECHO_GPU_BACKEND_RESOLVED to the final choice and must be included
#  BEFORE whisper.cpp / llama.cpp are added.
# -----------------------------------------------------------------------------

set(ECHO_GPU_BACKEND "auto" CACHE STRING "GPU backend for whisper.cpp/llama.cpp: auto, metal, cuda, vulkan, none")
set_property(CACHE ECHO_GPU_BACKEND PROPERTY STRINGS auto metal cuda vulkan none)
string(TOLOWER "${ECHO_GPU_BACKEND}" _backend)

if(_backend STREQUAL "auto")
    include(CheckLanguage)
    string(TOLOWER "${CMAKE_SYSTEM_PROCESSOR}" _cpu)

    if(APPLE)
        set(_backend "metal")
    else()
        check_language(CUDA)  # finds nvcc without failing if it is missing
        if(CMAKE_CUDA_COMPILER)
            set(_backend "cuda")
        else()
            find_package(Vulkan QUIET COMPONENTS glslc)
            # The Raspberry Pi's Vulkan driver works, but for these models it is
            # slower than the Pi's CPU, so ARM Linux boards default to CPU.
            if(Vulkan_FOUND AND Vulkan_glslc_FOUND AND NOT _cpu MATCHES "aarch64|arm")
                set(_backend "vulkan")
            else()
                set(_backend "none")
            endif()
        endif()
    endif()
    message(STATUS "GPU backend: auto-selected '${_backend}'")
else()
    message(STATUS "GPU backend: '${_backend}' (requested)")
endif()

# Translate the choice into ggml's own options. Exactly one GPU backend is on.
set(GGML_METAL  OFF)
set(GGML_CUDA   OFF)
set(GGML_VULKAN OFF)

if(_backend STREQUAL "metal")
    if(NOT APPLE)
        message(FATAL_ERROR "ECHO_GPU_BACKEND=metal is only available on macOS")
    endif()
    set(GGML_METAL ON)
    set(GGML_METAL_EMBED_LIBRARY ON)  # bake the GPU shaders into the binary
    set(ECHO_GPU_BACKEND_NAME "Metal")
elseif(_backend STREQUAL "cuda")
    set(GGML_CUDA ON)
    set(ECHO_GPU_BACKEND_NAME "CUDA")
elseif(_backend STREQUAL "vulkan")
    set(GGML_VULKAN ON)
    set(ECHO_GPU_BACKEND_NAME "Vulkan")
elseif(_backend STREQUAL "none")
    set(ECHO_GPU_BACKEND_NAME "none")
else()
    message(FATAL_ERROR "Unknown ECHO_GPU_BACKEND '${ECHO_GPU_BACKEND}' (use auto, metal, cuda, vulkan or none)")
endif()

set(ECHO_GPU_BACKEND_RESOLVED "${_backend}")
