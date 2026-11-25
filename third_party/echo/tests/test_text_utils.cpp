// Unit tests for the transcript helpers (src/core/text_utils.hpp).
#include "core/text_utils.hpp"

#include "test_framework.hpp"

using namespace echo;

TEST_CASE(trim_removes_outer_whitespace) {
    CHECK_EQ(trim("  hello world \n"), std::string("hello world"));
    CHECK_EQ(trim(""), std::string(""));
    CHECK_EQ(trim("   "), std::string(""));
}

TEST_CASE(clean_removes_annotations) {
    CHECK_EQ(cleanTranscript(" [BLANK_AUDIO]"), std::string(""));
    CHECK_EQ(cleanTranscript("(music) Hello there."), std::string("Hello there."));
    CHECK_EQ(cleanTranscript("I mean *sighs* fine."), std::string("I mean fine."));
    CHECK_EQ(cleanTranscript(" And so,   my fellow Americans. "),
             std::string("And so, my fellow Americans."));
}

TEST_CASE(clean_keeps_unmatched_brackets) {
    CHECK_EQ(cleanTranscript("a [b"), std::string("a [b"));
}

TEST_CASE(finished_sentences) {
    CHECK(looksFinished("Hello."));
    CHECK(looksFinished("Is it ready?"));
    CHECK(looksFinished("Stop!  "));
    CHECK(looksFinished("He said \"stop.\""));
    CHECK(looksFinished("He said \xE2\x80\x9Cstop.\xE2\x80\x9D"));  // curly quotes
}

TEST_CASE(unfinished_sentences) {
    CHECK(!looksFinished("and then we"));
    CHECK(!looksFinished("so I was thinking,"));
    CHECK(!looksFinished("well..."));
    CHECK(!looksFinished("well\xE2\x80\xA6"));  // unicode ellipsis
    CHECK(!looksFinished(""));
}

TEST_CASE(period_after_dangling_word_is_unfinished) {
    CHECK(!looksFinished("So I was thinking that maybe we could."));
    CHECK(!looksFinished("I need to go to the."));
    CHECK(!looksFinished("Remind me about the meeting and."));
    CHECK(!looksFinished("I was thinking, um."));
    // Ordinary endings, and questions ending on a preposition, are finished.
    CHECK(looksFinished("Remind me to call."));
    CHECK(looksFinished("Yes, I can."));
    CHECK(looksFinished("Who are you talking to?"));
    CHECK(looksFinished("Turn the lights on."));
}

TEST_CASE(count_words) {
    CHECK_EQ(countWords(""), 0);
    CHECK_EQ(countWords("one"), 1);
    CHECK_EQ(countWords("  one two\tthree \n"), 3);
}

TEST_CASE(removes_hesitation_sounds) {
    CHECK_EQ(removeFillerWords("Um, so, uh, I was thinking."), std::string("So I was thinking."));
    CHECK_EQ(removeFillerWords("I think, um."), std::string("I think."));
    CHECK_EQ(removeFillerWords("Um."), std::string(""));
    CHECK_EQ(removeFillerWords("Hmm, what time is it?"), std::string("What time is it?"));
}

TEST_CASE(removes_stutters) {
    CHECK_EQ(removeFillerWords("Go to the the beach."), std::string("Go to the beach."));
    CHECK_EQ(removeFillerWords("I, I was there."), std::string("I was there."));
    // Legitimate doubles are kept.
    CHECK_EQ(removeFillerWords("I know that that is true."), std::string("I know that that is true."));
    CHECK_EQ(removeFillerWords("She had had enough."), std::string("She had had enough."));
}

TEST_CASE(removes_comma_delimited_fillers) {
    CHECK_EQ(removeFillerWords("So, I was like, you know, thinking we could go to the beach."),
             std::string("So, I was like thinking we could go to the beach."));
    CHECK_EQ(removeFillerWords("I was, like, really tired."), std::string("I was really tired."));
    CHECK_EQ(removeFillerWords("Like, I don't really like the color, you know."),
             std::string("I don't really like the color."));
    CHECK_EQ(removeFillerWords("We could, I mean, maybe try it next week."),
             std::string("We could maybe try it next week."));
    CHECK_EQ(removeFillerWords("I mean, can you, like, send me the report?"),
             std::string("Can you send me the report?"));
}

TEST_CASE(keeps_meaningful_like_and_you_know) {
    CHECK_EQ(removeFillerWords("I like pizza."), std::string("I like pizza."));
    CHECK_EQ(removeFillerWords("Do you know the way?"), std::string("Do you know the way?"));
    CHECK_EQ(removeFillerWords("What do you mean?"), std::string("What do you mean?"));
}

int main() { return echo::test::runAll(); }
