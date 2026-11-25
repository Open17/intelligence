import pytest

from intelligence.wake import match_wake


@pytest.mark.parametrize(
    ("heard", "expected"),
    [
        # Real whisper output for synthesized speech (see wake.py)
        ("Hey Claude, what time is it in Tokyo?", "What time is it in Tokyo?"),
        ("Cloud", ""),
        ("A-clod", ""),
        ("Hey Claude", ""),
        ("OK Claude, turn on the lights.", "Turn on the lights."),
        ("Claude. Build me a script that backs up my photos.", "Build me a script that backs up my photos."),
        ("Claude, build me a script.", "Build me a script."),
        # Variants
        ("Hey, Claude. What's up?", "What's up?"),
        ("hey cloud what's the weather", "What's the weather"),
        ("Cloud, set a timer.", "Set a timer."),
        ("Claude what's two plus two", "What's two plus two"),
        ("So, hey Claude, quick question.", "Quick question."),
        ("Claude.", ""),
        ("Clawed, are you there?", "Are you there?"),
    ],
)
def test_wake_phrases_are_detected(heard: str, expected: str) -> None:
    m = match_wake(heard)
    assert m is not None, heard
    assert m.request == expected


@pytest.mark.parametrize(
    "heard",
    [
        "Cloud storage is getting expensive.",
        "The cloud is down again.",
        "A cloud formed over the hill.",
        "I think Claude is great.",
        "What did you say?",
        "",
        "a clod of earth fell on the floor",
        "Hey, how are you?",
    ],
)
def test_ordinary_speech_does_not_wake(heard: str) -> None:
    assert match_wake(heard) is None


def test_extra_wake_words() -> None:
    assert match_wake("Jarvis, lights on.", extra_words=("jarvis",)).request == "Lights on."
