// Tests GrammarCorrector with the real Qwen2.5-0.5B-Instruct model.
// Skipped when models/qwen2.5-0.5b-instruct-q4_k_m.gguf is missing.
//
// Usage: test_grammar_corrector <models-dir>
#include "grammar/grammar_corrector.hpp"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <filesystem>
#include <memory>

#include "test_framework.hpp"
#include "util/ggml_utils.hpp"

namespace {
std::string g_model_path;
std::unique_ptr<echo::GrammarCorrector> g_corrector;  // loaded once, shared by tests
constexpr int kSkip = 77;

std::string lowercase(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return s;
}

bool containsWord(const std::string& text, const std::string& word) {
    const std::string t = " " + lowercase(text) + " ";
    for (const char* sep : {" ", ",", ".", "?", "!"}) {
        if (t.find(" " + word + sep) != std::string::npos) return true;
    }
    return false;
}

std::string correctTimed(const std::string& input) {
    const auto start = std::chrono::steady_clock::now();
    std::string out = g_corrector->correct(input);
    const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                        std::chrono::steady_clock::now() - start).count();
    std::cout << "  [" << ms << " ms] \"" << input << "\" -> \"" << out << "\"\n";
    return out;
}
}  // namespace

TEST_CASE(removes_filler_words) {
    const std::string out = correctTimed("um so I I was like thinking we could uh go to the beach");
    CHECK(!containsWord(out, "um"));
    CHECK(!containsWord(out, "uh"));
    CHECK(containsWord(out, "beach"));
}

TEST_CASE(does_not_answer_questions) {
    const std::string out = correctTimed("what is the uh weather going to be like tomorrow");
    CHECK(containsWord(out, "weather"));
    CHECK(containsWord(out, "tomorrow"));
    CHECK(!out.empty() && out.back() == '?');
}

TEST_CASE(keeps_who_the_sentence_is_about) {
    // Regression: the model once turned "we could" into "you could".
    const std::string out = correctTimed("So, I was like thinking we could go to the beach this weekend.");
    CHECK(containsWord(out, "we"));
    CHECK(!containsWord(out, "you"));
    CHECK(containsWord(out, "beach"));
}

TEST_CASE(keeps_clean_text) {
    CHECK_EQ(correctTimed("Turn off the lights in the bedroom."),
             std::string("Turn off the lights in the bedroom."));
}

TEST_CASE(empty_input_is_returned_unchanged) {
    CHECK_EQ(g_corrector->correct("   "), std::string("   "));
}

int main(int argc, char** argv) {
    const std::filesystem::path models = argc > 1 ? argv[1] : "models";
    g_model_path = (models / "qwen2.5-0.5b-instruct-q4_k_m.gguf").string();
    if (!std::filesystem::exists(g_model_path)) {
        std::cout << "skipping: " << g_model_path << " not found\n";
        return kSkip;
    }
    echo::configureGgmlLogging(/*verbose=*/false);
    echo::GrammarConfig config;
    config.model_path = g_model_path;
    g_corrector = std::make_unique<echo::GrammarCorrector>(config);
    std::cout << "backend: " << g_corrector->backendDescription() << '\n';
    const int result = echo::test::runAll();
    g_corrector.reset();
    return result;
}
