// =============================================================================
//  text_utils.cpp - see text_utils.hpp
// =============================================================================
#include "core/text_utils.hpp"

#include <array>
#include <cctype>
#include <vector>

namespace echo {

namespace {

// std::isspace has undefined behaviour for negative char values (UTF-8 bytes
// above 127 are negative when char is signed), so cast to unsigned char first.
bool isSpace(char c) { return std::isspace(static_cast<unsigned char>(c)) != 0; }

}  // namespace

std::string trim(std::string_view text) {
    std::size_t begin = 0;
    std::size_t end = text.size();
    while (begin < end && isSpace(text[begin])) ++begin;
    while (end > begin && isSpace(text[end - 1])) --end;
    return std::string(text.substr(begin, end - begin));
}

std::string cleanTranscript(std::string_view raw) {
    std::string out;
    out.reserve(raw.size());

    // Walk the text once. When we meet an opening '[', '(' or '*', skip ahead
    // to its matching closing character - that whole span is an annotation.
    for (std::size_t i = 0; i < raw.size(); ++i) {
        const char c = raw[i];
        char closing = 0;
        if (c == '[') closing = ']';
        if (c == '(') closing = ')';
        if (c == '*') closing = '*';

        if (closing != 0) {
            const std::size_t end = raw.find(closing, i + 1);
            if (end != std::string_view::npos) {
                i = end;          // jump past the annotation
                out.push_back(' ');  // keep the words on either side apart
                continue;
            }
        }

        // Collapse runs of whitespace into one space.
        if (isSpace(c)) {
            if (!out.empty() && out.back() != ' ') out.push_back(' ');
            continue;
        }
        out.push_back(c);
    }

    // The loop may have left double spaces around removed annotations.
    std::string collapsed;
    collapsed.reserve(out.size());
    for (char c : out) {
        if (c == ' ' && (collapsed.empty() || collapsed.back() == ' ')) continue;
        collapsed.push_back(c);
    }
    return trim(collapsed);
}

bool looksFinished(std::string_view text) {
    std::string t = trim(text);

    // Ignore closing quotes / brackets: `He said "stop."` is finished.
    // (UTF-8 curly quotes are 3 bytes: E2 80 9D and E2 80 99.)
    for (;;) {
        if (!t.empty() && (t.back() == '"' || t.back() == '\'' || t.back() == ')' ||
                           t.back() == ']')) {
            t.pop_back();
        } else if (t.size() >= 3 && (t.compare(t.size() - 3, 3, "\xE2\x80\x9D") == 0 ||
                                     t.compare(t.size() - 3, 3, "\xE2\x80\x99") == 0)) {
            t.resize(t.size() - 3);
        } else {
            break;
        }
    }
    if (t.empty()) return false;

    // "..." or the single character "…" (UTF-8 E2 80 A6) = trailing off.
    if (t.size() >= 3 && t.compare(t.size() - 3, 3, "...") == 0) return false;
    if (t.size() >= 3 && t.compare(t.size() - 3, 3, "\xE2\x80\xA6") == 0) return false;

    const char last = t.back();
    if (last == '?' || last == '!') return true;
    if (last != '.') return false;

    // Look at the word before the period. Only '.' endings are checked:
    // questions often end on such words ("Who are you talking to?").
    std::size_t end = t.size() - 1;
    std::size_t begin = end;
    while (begin > 0 && std::isalpha(static_cast<unsigned char>(t[begin - 1]))) --begin;
    std::string word = t.substr(begin, end - begin);
    for (char& c : word) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));

    // Conjunctions, articles, possessives, most prepositions, some modals and
    // hesitation sounds. Mistaking a finished sentence for an unfinished one
    // only costs a little extra waiting (up to force_ms), so the list leans
    // towards "unfinished".
    static const char* const kDanglingWords[] = {
        "and", "or", "but", "because", "if", "the", "a", "an", "my", "your", "our",
        "their", "to", "of", "for", "with", "from", "into", "could", "would",
        "should", "might", "um", "uh", "er", "erm"};
    for (const char* dangling : kDanglingWords) {
        if (word == dangling) return false;
    }
    return true;
}

int countWords(std::string_view text) {
    int words = 0;
    bool in_word = false;
    for (char c : text) {
        if (isSpace(c)) {
            in_word = false;
        } else if (!in_word) {
            in_word = true;
            ++words;
        }
    }
    return words;
}

// --- removeFillerWords --------------------------------------------------------

namespace {

// One word split into its text and the punctuation stuck to its end:
// "beach," -> body "beach", trail ",", key "beach" (lower case, for comparing).
struct Word {
    std::string body;
    std::string trail;
    std::string key;
};

bool isTrailingPunct(char c) {
    return c == ',' || c == '.' || c == '?' || c == '!' || c == ';' || c == ':';
}

std::string toLower(std::string s) {
    for (char& c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return s;
}

bool isHesitation(const std::string& key) {
    static const std::array<const char*, 12> kSounds = {
        "um", "umm", "uh", "uhh", "uhm", "er", "erm", "ah", "hmm", "hm", "mm", "mhm"};
    for (const char* s : kSounds) {
        if (key == s) return true;
    }
    return false;
}

// True if the punctuation ends a sentence.
bool hasFinalPunct(const std::string& trail) {
    return trail.find_first_of(".?!") != std::string::npos;
}

// Called when a filler is dropped. A filler is usually set off by commas
// ("could, I mean, maybe"), so the comma in front of it goes too ("could
// maybe"). If the filler carried the sentence's final punctuation ("color,
// you know."), that punctuation moves onto the previous word ("color.").
void fixPunctAfterDrop(std::vector<Word>& kept, const std::string& dropped_trail) {
    if (kept.empty()) return;
    std::string& prev = kept.back().trail;
    while (!prev.empty() && (prev.back() == ',' || prev.back() == ';')) prev.pop_back();
    if (hasFinalPunct(dropped_trail) && !hasFinalPunct(prev)) {
        prev += dropped_trail.substr(dropped_trail.find_first_of(".?!"));
    }
}

}  // namespace

std::string removeFillerWords(std::string_view text) {
    // 1) Split into words.
    std::vector<Word> words;
    std::size_t i = 0;
    while (i < text.size()) {
        while (i < text.size() && isSpace(text[i])) ++i;
        std::size_t end = i;
        while (end < text.size() && !isSpace(text[end])) ++end;
        if (end > i) {
            std::string token(text.substr(i, end - i));
            std::size_t body_end = token.size();
            while (body_end > 0 && isTrailingPunct(token[body_end - 1])) --body_end;
            Word w;
            w.body = token.substr(0, body_end);
            w.trail = token.substr(body_end);
            w.key = toLower(w.body);
            words.push_back(std::move(w));
        }
        i = end;
    }

    // 2) Walk the words, keeping the ones that are not fillers.
    // Comma-delimited filler phrases: only removed when the phrase starts the
    // sentence or follows a comma, AND ends with a comma or the sentence end.
    static const std::vector<std::vector<std::string>> kPhrases = {
        {"you", "know"}, {"i", "mean"}, {"like"}};

    std::vector<Word> kept;
    for (std::size_t w = 0; w < words.size();) {
        const Word& word = words[w];

        // Hesitation sound ("um", "uh", ...).
        if (isHesitation(word.key)) {
            fixPunctAfterDrop(kept, word.trail);
            ++w;
            continue;
        }

        // Stutter: the same word twice in a row ("the the", "I, I").
        // "that that" and "had had" can be correct English, so they are kept.
        if (!kept.empty() && !word.key.empty() && word.key == kept.back().key &&
            !hasFinalPunct(kept.back().trail) && word.key != "that" && word.key != "had") {
            fixPunctAfterDrop(kept, word.trail);
            ++w;
            continue;
        }

        // Comma-delimited filler phrase.
        const bool after_comma_or_start =
            kept.empty() || kept.back().trail.find(',') != std::string::npos;
        bool removed_phrase = false;
        if (after_comma_or_start) {
            for (const auto& phrase : kPhrases) {
                if (w + phrase.size() > words.size()) continue;
                bool match = true;
                for (std::size_t k = 0; k < phrase.size(); ++k) {
                    const Word& candidate = words[w + k];
                    // Only the last word of the phrase may carry punctuation.
                    const bool last = k + 1 == phrase.size();
                    if (candidate.key != phrase[k] || (!last && !candidate.trail.empty())) {
                        match = false;
                        break;
                    }
                }
                const Word& last_word = words[w + phrase.size() - 1];
                const bool ends_cleanly = !last_word.trail.empty();  // ", " or ". "
                if (match && ends_cleanly) {
                    fixPunctAfterDrop(kept, last_word.trail);
                    w += phrase.size();
                    removed_phrase = true;
                    break;
                }
            }
        }
        if (removed_phrase) continue;

        kept.push_back(word);
        ++w;
    }

    // 3) Never end the text on a dangling comma.
    if (!kept.empty()) {
        std::string& last = kept.back().trail;
        while (!last.empty() && (last.back() == ',' || last.back() == ';')) last.pop_back();
    }

    // 4) Join and capitalise the first letter.
    std::string out;
    for (const Word& word : kept) {
        if (!out.empty()) out.push_back(' ');
        out += word.body;
        out += word.trail;
    }
    for (char& c : out) {
        if (std::isalpha(static_cast<unsigned char>(c))) {
            c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
            break;
        }
    }
    return out;
}

}  // namespace echo
