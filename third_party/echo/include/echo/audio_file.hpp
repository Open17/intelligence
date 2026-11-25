// =============================================================================
//  echo/audio_file.hpp - load an audio file as 16 kHz mono float samples
// =============================================================================
//
//  Handy for testing without a microphone: decode a WAV/MP3/FLAC file and feed
//  it to SpeechListener::pushAudio() (see the --wav option of echo-cli).
// =============================================================================
#pragma once

#include <string>
#include <vector>

namespace echo {

/// Decodes `path` (WAV, MP3 or FLAC), mixing down to mono and resampling to
/// `sample_rate`. Throws std::runtime_error if the file cannot be read.
std::vector<float> loadAudioFile(const std::string& path, int sample_rate = 16000);

}  // namespace echo
