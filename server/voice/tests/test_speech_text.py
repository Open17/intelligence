from intelligence.speech_text import SentenceChunker, clean_for_speech


def chunk_all(deltas: list[str], **kw) -> list[str]:
    c = SentenceChunker(**kw)
    out: list[str] = []
    for d in deltas:
        out += c.feed(d)
    return out + c.flush()


def test_clean_strips_markdown() -> None:
    md = "## Steps\n- **Open** the [docs](https://x.y/z)\n- Run `make test`\n"
    assert clean_for_speech(md) == "Steps\nOpen the docs\nRun make test"


def test_clean_replaces_code_blocks_and_urls() -> None:
    text = "Here:\n```python\nprint(1)\n```\nSee https://example.com/a/b for more."
    out = clean_for_speech(text)
    assert "print" not in out
    assert "example.com" in out and "/a/b" not in out


def test_clean_removes_emoji() -> None:
    assert clean_for_speech("Done! 🎉 ✅") == "Done!"


def test_sentences_are_split_as_they_stream() -> None:
    c = SentenceChunker()
    assert c.feed("Sure. ") == ["Sure."]
    assert c.feed("It is 3 p") == []
    assert c.feed("m in Tokyo. And") == ["It is 3 pm in Tokyo."]
    assert c.flush() == ["And"]


def test_abbreviations_and_decimals_do_not_split() -> None:
    out = chunk_all(["Dr. Smith said version 3.5 is out, e.g. today. Bye."])
    assert out == ["Dr. Smith said version 3.5 is out, e.g. today.", "Bye."]


def test_first_chunk_cuts_at_a_comma_for_latency() -> None:
    out = chunk_all(["Well, the main thing you need to know about this, ", "is that it works fine."])
    assert out[0] == "Well, the main thing you need to know about this,"
    assert out[1] == "is that it works fine."


def test_newlines_split() -> None:
    assert chunk_all(["First line\nSecond line"]) == ["First line", "Second line"]


def test_code_block_is_not_cut_in_half() -> None:
    out = chunk_all(["Try this:\n```", "\nx = 1. y = 2.\n", "```\nDone."])
    assert out == ["Try this:", "I've left the code out of what I'm saying.", "Done."]


def test_very_long_text_without_punctuation_is_split() -> None:
    out = chunk_all(["word " * 200])
    assert len(out) > 1 and all(len(p) <= 440 for p in out)
