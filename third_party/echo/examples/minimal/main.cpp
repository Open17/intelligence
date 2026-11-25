// =============================================================================
//  examples/minimal - the smallest useful program using echo
// =============================================================================
//
//  Listens to the default microphone and prints each finished sentence.
//  Replace the body of the callback with your own code, e.g. a request to an
//  AI API. Run from the repository root:  ./build/default/bin/echo-minimal
// =============================================================================
#include <echo/echo.hpp>

#include <iostream>
#include <string>

int main() {
    try {
        echo::SpeechListenerConfig config;  // defaults: base.en, Metal/CUDA/Vulkan if available
        // config.whisper_model_path = "models/ggml-tiny.en.bin";  // faster, e.g. on a Pi
        // config.enable_grammar_correction = true;                // remove "um", fix grammar

        echo::SpeechListener listener(config);

        // Called once per finished sentence, on a background thread.
        listener.setOnSentence([](const std::string& text) {
            std::cout << "You said: " << text << std::endl;
            // sendToAi(text);   <- your code goes here
        });

        listener.start();
        std::cout << "Speak into the microphone. Press Enter to quit." << std::endl;
        std::cin.get();
        listener.stop();
    } catch (const std::exception& e) {
        std::cerr << "error: " << e.what() << '\n';
        return 1;
    }
    return 0;
}
