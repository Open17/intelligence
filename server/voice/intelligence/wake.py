"""Wake phrase detection on transcribed text.

Whisper often writes "Claude" as something else, for example (measured with
echo's tiny.en and base.en models):

    "Claude."      -> "Cloud"
    "Hey Claude"   -> "A-clod"  (tiny.en)

So the matcher accepts common mis-spellings. "Cloud" is a real word, though,
so it only counts as the wake word where nobody would say "cloud": after a
greeting ("hey cloud"), on its own, or followed by punctuation ("Cloud, what
time is it?"). "Cloud storage is expensive" does not wake the assistant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Spellings that are always the wake word, and ones that are only the wake
# word in a wake-like position.
STRONG_WORDS = ("claude", "claud", "clawed", "clod", "clode", "klaud", "klod", "claudie")
WEAK_WORDS = ("cloud", "clouds", "clot", "plod")

GREETINGS = ("hey", "hi", "hay", "hello", "okay", "ok", "yo", "oi")
WEAK_GREETINGS = ("a", "ay", "eh", "uh")  # "A-clod": only with a strong word
LEADING_FILLERS = ("so", "um", "uh", "oh", "and", "well")

_WORD = r"[a-z']+"
_PATTERN = re.compile(
    rf"""^\W*
        (?:(?:{"|".join(LEADING_FILLERS)})\W+)?           # "so, hey claude"
        (?:(?P<greet>{"|".join(GREETINGS + WEAK_GREETINGS)})\W+)?
        (?P<wake>{_WORD})
        (?P<after>[^a-z0-9']*)
        (?P<rest>.*)$""",
    re.VERBOSE | re.DOTALL,
)


@dataclass(frozen=True)
class WakeMatch:
    """A detected wake phrase. ``request`` is what was said after it ("" if nothing)."""

    request: str


def match_wake(text: str, extra_words: tuple[str, ...] = ()) -> WakeMatch | None:
    """Returns a WakeMatch if ``text`` starts with the wake phrase, else None."""
    norm = text.strip().lower().replace("-", " ")
    m = _PATTERN.match(norm)
    if not m:
        return None
    wake, greet = m.group("wake"), m.group("greet")
    after, rest = m.group("after"), m.group("rest").strip()

    strong = wake in STRONG_WORDS or wake in extra_words
    if not strong:
        if wake not in WEAK_WORDS:
            return None
        punctuated = any(c in after for c in ",.!?:;")
        real_greeting = greet is not None and greet not in WEAK_GREETINGS
        if not (real_greeting or punctuated or not rest):
            return None
    elif greet in WEAK_GREETINGS and rest and not any(c in after for c in ",.!?"):
        # "a clod of earth fell" - a weak greeting needs the wake word to stand alone
        return None

    return WakeMatch(request=_restore_case(text, rest))


def _restore_case(original: str, rest_lower: str) -> str:
    """Returns the tail of ``original`` matching ``rest_lower`` with its original casing."""
    if not rest_lower:
        return ""
    idx = original.lower().replace("-", " ").rfind(rest_lower)
    tail = original[idx:] if idx >= 0 else rest_lower
    tail = tail.strip().lstrip(",.;:!? ")
    return tail[:1].upper() + tail[1:] if tail else ""
