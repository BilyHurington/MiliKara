"""Cantonese: one unit per hanzi with a tone-less Jyutping reading.

Cantonese shares its writing system with Mandarin, so nothing in the text tells
the two apart — reading a Cantonese lyric with pinyin gives *every* syllable the
wrong vowel (我 ngo -> wo, 唔 m -> wu, 嘅 ge -> ge), and the acoustic model then
has nothing in the audio to align to.  The language therefore comes from the
project (``LyricsDoc.language = "yue"``) or from :func:`looks_cantonese`, and
the reading is Jyutping.

Jyutping comes from ``ToJyutping`` (bundled dictionary, no download, no network)
and falls back to ``pycantonese``; with neither installed the readings fall back
to pinyin with a note, which aligns badly but never silently drops a character.
Tone digits are dropped (``hoeng1`` -> ``hoeng``): the aligner needs syllables,
and a digit would become a model token.

Polyphonic characters are marked uncertain with the alternatives as candidates,
exactly like the pinyin reader.
"""

from __future__ import annotations

import re
from typing import Any, Optional, Sequence

from ..models import Segment
from . import chinese

# Characters that are essentially only written in Cantonese (Mandarin lyrics do not use them):
# a lyric containing any of them is Cantonese with near certainty.
COLLOQUIAL = frozenset("嘅咗喺唔冇佢哋啲嚟嗰噉嚿嘢乜咁哋佢俾揾攞瞓嘥啱")
_TONE_RE = re.compile(r"[1-6]")

_source: Optional[str] = None
_loaded = False


def _load() -> Optional[str]:
    """Which Jyutping dictionary is usable: ``"ToJyutping"`` / ``"pycantonese"`` / None."""
    global _source, _loaded
    if _loaded:
        return _source
    _loaded = True
    for name in ("ToJyutping", "pycantonese"):
        try:
            __import__(name)
        except ImportError:
            continue
        _source = name
        break
    return _source


def source() -> Optional[str]:
    """The Jyutping dictionary in use, or None when neither is installed."""
    return _load()


def missing_hint() -> str:
    return "粤语注音需要 Jyutping 词典：pip install 'milikara[yue]'（ToJyutping 或 pycantonese）"


def toneless(jyutping: Optional[str]) -> Optional[str]:
    """``hoeng1`` -> ``hoeng`` (a reading as the aligner wants it: no tone)."""
    if not jyutping:
        return None
    return _TONE_RE.sub("", jyutping.strip()) or None


def looks_cantonese(texts: Sequence[str]) -> bool:
    """True when the lyric uses characters that are only written in Cantonese (嘅 咗 喺 唔 …).

    Standard written Chinese sung in Cantonese (富士山下) carries no such marker, so this only
    catches colloquial lyrics; the rest is the user's language setting.
    """
    return any(c in COLLOQUIAL for t in texts for c in t)


def _tojyutping_run(run: str) -> list[tuple[str, Optional[str], list[str]]]:
    import ToJyutping

    out: list[tuple[str, Optional[str], list[str]]] = []
    for ch, jy in ToJyutping.get_jyutping_list(run):
        r = toneless(jy)
        alts: list[str] = []
        if r:
            for _, cands in ToJyutping.get_jyutping_candidates(ch):
                alts.extend(c for c in (toneless(x) for x in cands) if c and c != r)
        out.append((ch, r, list(dict.fromkeys(alts))))
    return out


def _pycantonese_run(run: str) -> list[tuple[str, Optional[str], list[str]]]:
    import pycantonese

    out: list[tuple[str, Optional[str], list[str]]] = []
    for ch in run:
        pairs = pycantonese.characters_to_jyutping(ch)
        r = toneless(" ".join(v for _, v in pairs if v)) if pairs else None
        out.append((ch, r, []))
    return out


def jyutping_readings(run: str) -> list[tuple[str, Optional[str], list[str]]]:
    """Jyutping for every character of a hanzi run (pinyin for a character it does not know)."""
    src = _load()
    rows = _tojyutping_run(run) if src == "ToJyutping" else (
        _pycantonese_run(run) if src == "pycantonese" else [(c, None, []) for c in run])
    out: list[tuple[str, Optional[str], list[str]]] = []
    for i, (ch, r, alts) in enumerate(rows):
        if not r:
            # a character the dictionary does not know (rare / a name): pinyin is the wrong sound but
            # keeps it aligned and visible, and the segment is marked uncertain
            py = chinese.pinyin_readings(run[i:i + 1])
            ch2, r, alts = py[0]
            out.append((ch2, r, alts))
            continue
        out.append((ch, r, alts))
    return out


def rule_segments(text: str) -> list[Segment]:
    return chinese.segments_with(text, "yue", jyutping_readings)


def debug_info() -> dict[str, Any]:
    """What the Cantonese reading will use (shown by the diagnostics)."""
    return {"source": _load(), "hint": None if _load() else missing_hint()}
