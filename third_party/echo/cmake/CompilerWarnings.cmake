# -----------------------------------------------------------------------------
#  CompilerWarnings.cmake
#
#  echo_set_warnings(<target>) turns on a sensible set of warnings for one of
#  OUR targets. We never apply these to third-party code (whisper.cpp, ...),
#  because we cannot fix warnings there.
# -----------------------------------------------------------------------------

function(echo_set_warnings target)
    if(MSVC)
        set(warnings /W4 /permissive- /utf-8)
        if(ECHO_WARNINGS_AS_ERRORS)
            list(APPEND warnings /WX)
        endif()
    else()
        set(warnings
            -Wall -Wextra -Wpedantic
            -Wshadow                # a variable hides another with the same name
            -Wconversion            # implicit conversions that may lose data
            -Wno-sign-conversion    # too noisy with size_t <-> int in practice
            -Wnon-virtual-dtor)
        if(ECHO_WARNINGS_AS_ERRORS)
            list(APPEND warnings -Werror)
        endif()
    endif()
    target_compile_options(${target} PRIVATE ${warnings})
endfunction()
