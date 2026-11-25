"""Turning model output into something a text-to-speech voice can say.

Two jobs:

* ``clean_for_speech`` removes formatting a voice would read out literally
  (markdown, code blocks, URLs, emoji).
* ``SentenceChunker`` cuts a stream of text deltas into speakable pieces as
  early as possible. The first piece is cut at a clause boundary once it is
  long enough, so the voice can start talking before the model finishes its
  first sentence. That is the biggest single latency win in the pipeline.
"""

from __future__ import annotations

import re

_CODE_FENCE = re.compile(r"```.*?(```|$)", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)]+)\)")
_URL = re.compile(r"https?://([^/\s]+)\S*")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_BULLET = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+", re.MULTILINE)
_EMPHASIS = re.compile(r"(\*\*|__|\*|_)(?=\S)(.+?)(?<=\S)\1")
_TABLE_RULE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", re.MULTILINE)
_EMOJI = re.compile(
    "[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f000-\U0001f2ff\U0000fe0f\U0000200d]"
)
_SPACES = re.compile(r"[ \t]+")


def clean_for_speech(text: str) -> str:
    """Strips markdown and other things that should not be read aloud."""
    text = _CODE_FENCE.sub(" I've left the code out of what I'm saying. ", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _URL.sub(r"\1", text)
    text = _TABLE_RULE.sub("", text)
    text = _HEADING.sub("", text)
    text = _BULLET.sub("", text)
    text = _EMPHASIS.sub(r"\2", text)
    text = text.replace("|", ", ")
    text = _EMOJI.sub("", text)
    text = text.replace("&", " and ").replace("->", " to ").replace("=>", " to ")
    text = text.replace("~", "about ")
    return _SPACES.sub(" ", text).strip()


# A sentence ends at . ! ? (optionally followed by quotes/brackets) and then
# whitespace. Not after common abbreviations or a single capital (initials).
_ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "st", "vs", "etc", "e.g", "i.e", "approx", "no", "jr", "sr"}
_SENTENCE_END = re.compile(r"[.!?]+[\"')\]]*(?=\s)|\n+")
_CLAUSE_END = re.compile(r"[,;:—–](?=\s)")


class SentenceChunker:
    """Splits streamed text into speakable chunks.

    feed() returns the chunks that are complete; flush() returns whatever is
    left at the end of the response.
    """

    def __init__(self, first_chunk_min: int = 40, max_chunk: int = 220) -> None:
        self._buf = ""
        self._first_chunk_min = first_chunk_min
        self._max_chunk = max_chunk
        self._emitted_any = False

    def feed(self, delta: str) -> list[str]:
        self._buf += delta
        out: list[str] = []
        while True:
            cut = self._find_cut()
            if cut is None:
                break
            piece, self._buf = self._buf[:cut], self._buf[cut:]
            piece = clean_for_speech(piece)
            if piece:
                out.append(piece)
                self._emitted_any = True
        return out

    def flush(self) -> list[str]:
        piece = clean_for_speech(self._buf)
        self._buf = ""
        return [piece] if piece else []

    def _find_cut(self) -> int | None:
        buf = self._buf
        # Code blocks are spoken as one unit (replaced by a short notice), so
        # never cut inside one: text before a fence is cut at the fence, and a
        # block is only cut once its closing fence has arrived.
        fence = buf.find("```")
        if fence == 0 or (fence > 0 and not buf[:fence].strip()):
            close = buf.find("```", fence + 3)
            return close + 3 if close >= 0 else None
        if fence > 0:
            buf = buf[:fence]
            cut = self._sentence_cut(buf)
            return cut if cut is not None else fence
        return self._sentence_cut(buf)

    def _sentence_cut(self, buf: str) -> int | None:
        for m in _SENTENCE_END.finditer(buf):
            end = m.end()
            if m.group().startswith("\n"):
                if buf[:end].strip():
                    return end
                continue
            word = re.search(r"([\w.]+)[.!?]*[\"')\]]*$", buf[:end])
            if word:
                w = word.group(1).lower().rstrip(".")
                if w in _ABBREVIATIONS or (len(w) == 1 and w.isalpha()) or w.replace(".", "").isdigit():
                    continue
            if len(buf[:end].strip()) >= 2:
                return end
        # The first chunk may end at a comma once it is long enough: the voice
        # starts sooner. Later chunks only split at commas when very long.
        limit = self._first_chunk_min if not self._emitted_any else self._max_chunk
        if len(buf) >= limit:
            clauses = [m.end() for m in _CLAUSE_END.finditer(buf) if m.end() >= limit // 2]
            if clauses:
                return clauses[0] if not self._emitted_any else clauses[-1]
        if len(buf) >= self._max_chunk * 2:
            space = buf.rfind(" ", 0, self._max_chunk)
            return space if space > 0 else self._max_chunk
        return None
