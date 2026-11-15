"""Chinese: one unit per hanzi with a tone-less pinyin reading (pypinyin).

``ü`` is written as ``v`` (pypinyin NORMAL style), e.g. 女 -> ``nv``.
Polyphonic characters are marked uncertain with the alternatives as candidates.

Cantonese (:mod:`kara_align.reading.cantonese`) is written in the same hanzi and
splits a line the same way, only its reading is Jyutping instead of pinyin, so
the run splitting lives here and both take a *reader* that turns a run of hanzi
into ``(surface, reading, alternatives)`` per character.
"""

from __future__ import annotations

import re
from typing import Callable, Optional, Sequence

from ..models import Segment, Unit
from .japanese import is_kanji

_RUN_RE = re.compile(r"(?P<han>[一-鿿㐀-䶿]+)|(?P<latin>[A-Za-z']+)|(?P<other>.)", re.S)

# reader(run) -> one (surface, reading | None, alternative readings) per character
HanziReader = Callable[[str], Sequence[tuple[str, Optional[str], list[str]]]]


def pinyin_readings(run: str) -> list[tuple[str, Optional[str], list[str]]]:
    """Tone-less pinyin for every character of a hanzi run, with the heteronyms."""
    from pypinyin import Style, lazy_pinyin, pinyin

    out: list[tuple[str, Optional[str], list[str]]] = []
    for ch, r in zip(run, lazy_pinyin(run, style=Style.NORMAL)):
        alts = [a for a in dict.fromkeys(pinyin(ch, style=Style.NORMAL, heteronym=True)[0]) if a != r]
        out.append((ch, r or None, alts))
    return out


def segments_with(text: str, lang: str, reader: HanziReader) -> list[Segment]:
    """Rule-segment a line: hanzi runs one segment per character (read by ``reader``), latin
    words as one english segment, everything else (punctuation, spaces) as its own segment."""
    segs: list[Segment] = []
    for m in _RUN_RE.finditer(text):
        kind, run = m.lastgroup, m.group()
        if kind == "han":
            for ch, r, alts in reader(run):
                if r:
                    segs.append(Segment(surface=ch, reading=r, lang=lang,  # type: ignore[arg-type]
                                        units=[Unit(reading=r, surface=ch)], reading_source="rule",
                                        uncertain=bool(alts), candidates=alts))
                else:
                    segs.append(Segment(surface=ch, reading=None, lang=lang, units=[],  # type: ignore[arg-type]
                                        reading_source="none"))
        elif kind == "latin":
            from .english import word_reading

            r = word_reading(run)
            segs.append(Segment(surface=run, reading=r or None, lang="en",
                                units=[Unit(reading=r, surface=run)] if r else [],
                                reading_source="rule" if r else "none"))
        else:
            if segs and not segs[-1].units and segs[-1].reading_source == "none":
                segs[-1].surface += run
            else:
                segs.append(Segment(surface=run, reading=None, lang=lang, units=[],  # type: ignore[arg-type]
                                    reading_source="none"))
    return segs


def rule_segments(text: str) -> list[Segment]:
    return segments_with(text, "zh", pinyin_readings)


def has_hanzi_only(text: str) -> bool:
    return any(is_kanji(c) for c in text)
