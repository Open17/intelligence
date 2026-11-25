// =============================================================================
//  text_utils.hpp - small string helpers for transcripts
// =============================================================================
#pragma once

#include <string>
#include <string_view>

namespace echo {

/// Removes leading and trailing whitespace.
std::string trim(std::string_view text);

/// Cleans raw whisper output:
///  * drops non-speech annotations such as "[BLANK_AUDIO]", "(music)", "*sighs*"
///  * collapses runs of whitespace into a single space
///  * trims the ends
/// Returns an empty string if nothing but annotations was transcribed.
std::string cleanTranscript(std::string_view raw);

/// True if the text ends like a finished sentence: its last character is
/// '.', '?' or '!' (closing quotes/brackets after it are ignored).
/// NOT finished despite the punctuation:
///  * a trailing ellipsis ("..." or "…"): the speaker trailed off;
///  * a '.' straight after a word a sentence can't really end on
///    ("maybe we could.", "I went to the.", "and um."). Whisper puts a period
///    on almost every fragment, so this check is what makes the endpointer
///    wait for the rest of a sentence after a thinking pause.
bool looksFinished(std::string_view text);

/// Number of whitespace-separated words.
int countWords(std::string_view text);

/// Removes speech disfluencies with simple, predictable rules (no AI model):
///  * hesitation sounds: "um", "uh", "er", "erm", "hmm", ...
///  * stutters: "the the beach" -> "the beach", "I, I was" -> "I was"
///  * "you know", "I mean" and "like" when set off by commas, the way whisper
///    writes them as fillers: "I was, like, tired, you know." -> "I was tired."
/// Sentence-ending punctuation is kept and the first letter is capitalised.
/// Returns an empty string if only fillers were said ("Um.").
std::string removeFillerWords(std::string_view text);

}  // namespace echo
