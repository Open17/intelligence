// =============================================================================
//  audio_file.cpp - see include/echo/audio_file.hpp
// =============================================================================
#include "echo/audio_file.hpp"

#include <miniaudio.h>

#include <stdexcept>

namespace echo {

std::vector<float> loadAudioFile(const std::string& path, int sample_rate) {
    // Ask the decoder to convert to exactly the format we want; miniaudio
    // handles channel mixing and resampling for us.
    ma_decoder_config config =
        ma_decoder_config_init(ma_format_f32, 1, static_cast<ma_uint32>(sample_rate));
    config.resampling.linear.lpfOrder = MA_MAX_FILTER_ORDER;  // same quality as live capture

    ma_decoder decoder;
    if (ma_decoder_init_file(path.c_str(), &config, &decoder) != MA_SUCCESS) {
        throw std::runtime_error("cannot open audio file: " + path);
    }

    std::vector<float> samples;
    float chunk[4096];
    for (;;) {
        ma_uint64 frames_read = 0;
        const ma_result result = ma_decoder_read_pcm_frames(&decoder, chunk, 4096, &frames_read);
        samples.insert(samples.end(), chunk, chunk + frames_read);
        if (result != MA_SUCCESS || frames_read == 0) {
            break;  // MA_AT_END (or an error) - either way we are done
        }
    }

    ma_decoder_uninit(&decoder);
    return samples;
}

}  // namespace echo
