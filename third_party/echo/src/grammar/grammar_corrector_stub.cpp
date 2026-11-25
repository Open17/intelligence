// =============================================================================
//  grammar_corrector_stub.cpp - stand-in used when ECHO_ENABLE_GRAMMAR=OFF
// =============================================================================
//
//  Defines GrammarCorrector without llama.cpp. Constructing one throws, so a
//  config with enable_grammar_correction = true fails with a clear message
//  instead of a confusing link error.
// =============================================================================
#include <stdexcept>

#include "grammar/grammar_corrector.hpp"

namespace echo {

GrammarCorrector::GrammarCorrector(const GrammarConfig& config) : config_(config) {
    throw std::runtime_error(
        "grammar correction was requested, but echo was built with "
        "ECHO_ENABLE_GRAMMAR=OFF (rebuild with -DECHO_ENABLE_GRAMMAR=ON)");
}

GrammarCorrector::~GrammarCorrector() = default;

std::string GrammarCorrector::correct(const std::string& text) { return text; }

std::string GrammarCorrector::backendDescription() const { return "unavailable"; }

}  // namespace echo
