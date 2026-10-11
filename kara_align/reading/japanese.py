"""Japanese kana handling: normalisation, mora splitting and rule readings.

Morae (拍) rules:

* 拗音 / small vowels merge with the preceding kana (きゃ, しゅ, ふぁ, ヴぁ, てぃ).
* 促音 ``っ`` is its own mora, flag ``sokuon``.
* 撥音 ``ん`` is its own mora, flag ``hatsuon``.
* long-vowel marks ``ー`` / ``〜`` / ``～`` are their own mora, flag ``long``; an ASCII
  ``~`` right after kana (``ラララ~``) is one too.  All of them read as ``ー``.

Segmentation and rule readings come from MeCab (fugashi + UniDic) when it is
installed: one segment per word, a kanji word keeps its okurigana (好き, 始まり,
震える) and readings use the context (君 → きみ).  Without it, lines are split
by character class and read by pykakasi (context free).  Either way every kanji
segment is marked ``uncertain`` – manual / AI readings take precedence.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

from ..models import Segment, Unit

SMALL_MERGE = set("ゃゅょぁぃぅぇぉゎ")
# wave dash 〜 (U+301C) and full-width tilde ～ (U+FF5E) are written for a long vowel as often as ー
LONG_MARKS = set("ー〜～")
# the ASCII tilde is only a long vowel after kana (``ラララ~``); elsewhere it is punctuation
ASCII_TILDE = "~"
SOKUON = "っ"
HATSUON = "ん"


@dataclass
class Mora:
    text: str
    flags: list[str] = field(default_factory=list)


def to_hiragana(s: str) -> str:
    """Katakana -> hiragana (ヴ -> ゔ); long-vowel marks -> ``ー``; other characters unchanged.

    〜 / ～ always become ``ー`` (NFKC would fold ～ to an ASCII ``~`` that nothing reads as a
    long vowel); an ASCII ``~`` does when it follows kana, or when the string is only tildes
    (a single long-vowel unit such as ``["ら", "~"]``).
    """
    s = unicodedata.normalize("NFKC", s.replace("～", "ー").replace("〜", "ー"))
    only_marks = bool(s) and all(c in "ー" + ASCII_TILDE for c in s)
    out: list[str] = []
    for ch in s:
        code = ord(ch)
        if 0x30A1 <= code <= 0x30F6:
            out.append(chr(code - 0x60))
        elif ch == ASCII_TILDE and (only_marks or (out and _kana_code(out[-1]))):
            out.append("ー")
        else:
            out.append(ch)
    return "".join(out)


def _kana_code(ch: str) -> bool:
    code = ord(ch)
    return (0x3041 <= code <= 0x309F or 0x30A0 <= code <= 0x30FF) and ch not in _NON_KANA


# characters inside the kana Unicode blocks that are punctuation / marks,
# not pronounced: ゠ (double hyphen), ・ (middle dot, e.g. "・・・"),
# standalone ゛ ゜
_NON_KANA = {"\u30a0", "\u30fb", "\u309b", "\u309c"}


def is_kana(ch: str) -> bool:
    code = ord(ch)
    if ch in _NON_KANA:
        return False
    return 0x3041 <= code <= 0x309F or 0x30A0 <= code <= 0x30FF or ch in LONG_MARKS


def is_kanji(ch: str) -> bool:
    code = ord(ch)
    return (0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF or 0xF900 <= code <= 0xFAFF
            or ch in "々〆ヶ")


def is_kana_text(s: str) -> bool:
    """Only kana and long-vowel marks (an ASCII ``~`` counts after kana: ``ラララ~``)."""
    if not s:
        return False
    for i, c in enumerate(s):
        if not (is_kana(c) or (c == ASCII_TILDE and i > 0 and (is_kana(s[i - 1]) or s[i - 1] == ASCII_TILDE))):
            return False
    return True


def is_long_mark_text(s: str) -> bool:
    """Only long-vowel marks (ー 〜 ～ ~): a separate "word" that lengthens the one before it."""
    return bool(s) and all(c in LONG_MARKS or c == ASCII_TILDE for c in s)


def split_morae(kana: str) -> list[Mora]:
    """Split a kana string (hiragana or katakana) into morae."""
    h = to_hiragana(kana)
    morae: list[Mora] = []
    for ch in h:
        if ch in SMALL_MERGE and morae and not morae[-1].flags:
            morae[-1].text += ch
        elif ch == SOKUON:
            morae.append(Mora(ch, ["sokuon"]))
        elif ch == HATSUON:
            morae.append(Mora(ch, ["hatsuon"]))
        elif ch in LONG_MARKS or ch == ASCII_TILDE:
            morae.append(Mora("ー", ["long"]))
        else:
            morae.append(Mora(ch, []))
    return morae


def kana_units(kana_surface: str, *, with_surface: bool = True) -> list[Unit]:
    """One Unit per mora.  The unit reading is hiragana; surface keeps the original kana."""
    units: list[Unit] = []
    pos = 0
    for m in split_morae(kana_surface):
        n = len(m.text)
        surf = kana_surface[pos:pos + n] if with_surface else ""
        pos += n
        units.append(Unit(reading=m.text, surface=surf, flags=list(m.flags)))
    return units


def reading_units(reading: str) -> list[Unit]:
    """Units for a kanji reading (empty surfaces; the segment owns the surface)."""
    return [Unit(reading=m.text, surface="", flags=list(m.flags)) for m in split_morae(reading)]


_kakasi = None


def _kks():
    global _kakasi
    if _kakasi is None:
        import pykakasi

        _kakasi = pykakasi.kakasi()
    return _kakasi


_CLASS_RE = re.compile(
    # kana without the marks in _NON_KANA (゛゜゠・ are punctuation)
    # (an ASCII ~ continues a kana run: ラララ~)
    r"(?P<kana>[\u3041-\u3096\u3099\u309a\u309d-\u309f\u30a1-\u30fa\u30fc-\u30ffー〜～]"
    r"[\u3041-\u3096\u3099\u309a\u309d-\u309f\u30a1-\u30fa\u30fc-\u30ffー〜～~]*)"
    r"|(?P<kanji>[一-鿿㐀-䶿豈-﫿々〆ヶ]+)"
    r"|(?P<latin>[A-Za-zＡ-Ｚａ-ｚ']+)"
    r"|(?P<digit>[0-9０-９]+)"
    r"|(?P<other>.)",
    re.S,
)


def _kanji_reading(orig: str) -> str:
    return "".join(to_hiragana(r["hira"]) for r in _kks().convert(orig))


def _split_okurigana(orig: str, hira: str) -> list[tuple[str, str]]:
    """Split a pykakasi token like ('歩い', 'あるい') into kanji / kana parts."""
    runs = [(m.lastgroup, m.group()) for m in _CLASS_RE.finditer(orig)]
    if len(runs) <= 1:
        return [(orig, hira)]
    # strip kana prefix / suffix that literally match the reading
    out_pre, out_suf = [], []
    h = hira
    while runs and runs[0][0] == "kana" and h.startswith(to_hiragana(runs[0][1])):
        k = to_hiragana(runs[0][1])
        out_pre.append((runs[0][1], k))
        h = h[len(k):]
        runs.pop(0)
    while runs and runs[-1][0] == "kana" and h.endswith(to_hiragana(runs[-1][1])) and len(runs) > 1:
        k = to_hiragana(runs[-1][1])
        out_suf.insert(0, (runs[-1][1], k))
        h = h[: len(h) - len(k)]
        runs.pop()
    middle = "".join(t for _, t in runs)
    return out_pre + ([(middle, h)] if middle else []) + out_suf


_tagger_state: dict = {}


def _tagger():
    """A shared fugashi Tagger, or None when MeCab / UniDic is not installed."""
    if "t" not in _tagger_state:
        try:
            import fugashi

            _tagger_state["t"] = fugashi.Tagger()
        except Exception:  # optional dependency: fall back to character classes
            _tagger_state["t"] = None
    return _tagger_state["t"]


def _is_ja(s: str) -> bool:
    return any(is_kana(c) or is_kanji(c) for c in s)


def word_spans(text: str) -> Optional[list[tuple[int, int, str, str]]]:
    """Words of ``text`` as (start, end, sung reading in hiragana, pos1), or None
    without MeCab.

    MeCab's short units are grouped the way lyrics are read: auxiliary verbs
    (except the copula だ), suffixes and conjunctive particles stay with the
    word before them (なっ+てく → なってく, 聞い+た → 聞いた, 咲い+て → 咲いて)
    and consecutive particles form one word (で+も → でも).  The reading is "" when a word
    has no dictionary reading.  Particles written は / へ are read わ / え.
    Whitespace is not part of any word.  None is also returned when the
    analyser's surfaces cannot be mapped back onto ``text`` exactly.
    """
    t = _tagger()
    if t is None or not text:
        return None
    out: list[list] = []  # [start, end, reading, pos1, last_pos1]
    pos = 0
    for w in t(text):
        surf = w.surface
        start = text.find(surf, pos)
        if not surf or start < 0 or text[pos:start].strip():
            return None
        f = w.feature
        pos1 = getattr(f, "pos1", "") or ""
        pos2 = getattr(f, "pos2", "") or ""
        kana = getattr(f, "kana", None) or ""
        reading = "" if kana in ("", "*") else to_hiragana(kana)
        if pos1 == "助詞" and surf in _PARTICLE_SOUND:
            reading = _PARTICLE_SOUND[surf]
        if not _is_ja(surf):
            reading = ""
        prev = out[-1] if out else None
        lemma = getattr(f, "lemma", "") or ""
        joins = (
            (pos1 == "助動詞" and lemma != "だ")  # ない, た, てく, たい … (not the copula だ / に / で)
            or pos1 == "接尾辞"
            or (pos1 == "助詞" and (pos2 == "接続助詞" or prev is not None and prev[4] == "助詞"))
        )
        attach = (prev is not None and joins and prev[1] == start
                  and _is_ja(surf) and _is_ja(text[prev[0]:prev[1]]))
        if attach:
            prev[1] = start + len(surf)
            prev[2] = prev[2] + reading if (prev[2] and reading) else ""
            prev[4] = pos1
        else:
            out.append([start, start + len(surf), reading, pos1, pos1])
        pos = start + len(surf)
    if text[pos:].strip():
        return None
    return [(a, b, r, p1) for a, b, r, p1, _ in out]


_LATIN_RE = re.compile(r"^[A-Za-zＡ-Ｚａ-ｚ']+$")
_DIGIT_RE = re.compile(r"^[0-9０-９]+$")
# particles written は / へ but sung わ / え
_PARTICLE_SOUND = {"は": "わ", "へ": "え"}


def _long_after(segs: list[Segment], run: str) -> bool:
    """Long-vowel marks written as their own word right after a sung Japanese segment
    (空～, 空~, MeCab's 補助記号): one ``ー`` unit per mark, lengthening the vowel before."""
    if not is_long_mark_text(run) or not segs or not segs[-1].units or segs[-1].lang != "ja":
        return False
    segs.append(Segment(surface=run, reading="ー" * len(run), lang="ja",
                        units=[Unit(reading="ー", surface=c, flags=["long"]) for c in run],
                        reading_source="rule"))
    return True


def _punct(segs: list[Segment], run: str) -> None:
    if segs and not segs[-1].units and segs[-1].reading_source == "none" and not segs[-1].uncertain:
        segs[-1].surface += run  # merge consecutive punctuation / spaces
    else:
        segs.append(Segment(surface=run, reading=None, lang="ja", units=[], reading_source="none"))


def _word_segments(text: str, words: list[tuple[int, int, str, str]]) -> list[Segment]:
    segs: list[Segment] = []
    pos = 0
    for a, b, kana, pos1 in words:
        if a > pos:
            _punct(segs, text[pos:a])
        pos = b
        surf = text[a:b]
        if any(is_kanji(c) for c in surf):
            reading = kana
            if not is_kana_text(reading):
                reading = _kanji_reading(surf)
            if not is_kana_text(reading):
                segs.extend(_class_segments(surf))  # unknown word: old method for this word
                continue
            alt = _kanji_reading(surf)  # a context-free second opinion, offered as a candidate
            segs.append(Segment(surface=surf, reading=reading, lang="ja", units=reading_units(reading),
                                reading_source="rule", uncertain=True,
                                candidates=[alt] if alt != reading and is_kana_text(alt) else []))
        elif _long_after(segs, surf):
            pass
        elif is_kana_text(surf):
            units = kana_units(surf)
            sung = split_morae(kana) if is_kana_text(kana) else []
            if len(sung) == len(units):  # particles は / へ sung わ / え
                for u, m in zip(units, sung):
                    u.reading = m.text
            segs.append(Segment(surface=surf, reading="".join(u.reading for u in units), lang="ja",
                                units=units, reading_source="rule"))
        elif _LATIN_RE.match(surf) or _DIGIT_RE.match(surf) or any(is_kana(c) for c in surf):
            segs.extend(_class_segments(surf))
        else:
            _punct(segs, surf)
    if pos < len(text):
        _punct(segs, text[pos:])
    return segs


def rule_segments(text: str) -> list[Segment]:
    """Rule-based segmentation of a Japanese line (by word when MeCab is available).

    Surfaces of the returned segments concatenate exactly to ``text``.
    """
    words = word_spans(text)
    if words is not None:
        return _word_segments(text, words)
    return _class_segments(text)


def _class_segments(text: str) -> list[Segment]:
    """Fallback: split by character class (kanji run / kana run / latin / digits)."""
    segs: list[Segment] = []
    for m in _CLASS_RE.finditer(text):
        kind, run = m.lastgroup, m.group()
        if kind == "kana":
            hira = to_hiragana(run)
            segs.append(Segment(surface=run, reading=hira, lang="ja", units=kana_units(run),
                                reading_source="rule"))
        elif kind == "kanji":
            # use pykakasi on the kanji run with trailing context for better readings
            for piece, reading in _kanji_pieces(text, m.start(), run):
                segs.append(Segment(surface=piece, reading=reading, lang="ja",
                                    units=reading_units(reading) if reading else [],
                                    reading_source="rule" if reading else "none",
                                    uncertain=True))
        elif kind == "latin" and letter_name(run):
            # a letter spelled out (R O M A …): its name as sung, the whole letter one unit
            name, alts = letter_name(run)  # type: ignore[misc]
            segs.append(Segment(surface=run, reading=name, lang="ja", units=[Unit(reading=name, surface=run)],
                                reading_source="rule", candidates=alts))
        elif kind == "latin":
            from .english import word_reading

            r = word_reading(run)
            segs.append(Segment(surface=run, reading=r or None, lang="en",
                                units=[Unit(reading=r, surface=run)] if r else [],
                                reading_source="rule" if r else "none"))
        elif kind == "digit":
            # the usual reading, to be checked: songs often sing numbers otherwise (よ, ワン, ひとつ …)
            from .numbers import digits_reading

            reading, alts = digits_reading(run)
            segs.append(Segment(surface=run, reading=reading, lang="ja", units=reading_units(reading),
                                reading_source="rule", uncertain=True, candidates=alts,
                                note="数字：按常见读法注音，请按歌里的唱法确认"))
        elif not _long_after(segs, run):
            _punct(segs, run)
    return segs


# names of the Latin letters as sung in Japanese; the first one is used, the others are offered as
# candidates (tried by the aligner's retry when the line fits badly)
LETTER_NAMES: dict[str, list[str]] = {
    "A": ["えー", "えい"], "B": ["びー"], "C": ["しー"], "D": ["でぃー", "でー"], "E": ["いー"], "F": ["えふ"],
    "G": ["じー"], "H": ["えいち", "えっち"], "I": ["あい"], "J": ["じぇー", "じぇい"], "K": ["けー", "けい"],
    "L": ["える"], "M": ["えむ"], "N": ["えぬ"], "O": ["おー"], "P": ["ぴー"], "Q": ["きゅー"], "R": ["あーる"],
    "S": ["えす"], "T": ["てぃー"], "U": ["ゆー"], "V": ["ぶい", "ゔぃー"], "W": ["だぶりゅー", "だぶる"],
    "X": ["えっくす"], "Y": ["わい"], "Z": ["ぜっと", "ずぃー"],
}


def letter_name(run: str) -> Optional[tuple[str, list[str]]]:
    """(reading, other readings) of a single capital letter written on its own (a spelled-out word:
    R O M A N T I C); None for anything else (words, lowercase letters such as the article a)."""
    c = unicodedata.normalize("NFKC", run)
    if len(c) != 1 or not ("A" <= c <= "Z"):
        return None
    names = LETTER_NAMES[c]
    return names[0], names[1:]


def _kanji_pieces(text: str, start: int, run: str) -> list[tuple[str, str]]:
    """Readings for a kanji run using pykakasi on the run plus following okurigana."""
    # include following kana so pykakasi can pick a reading (歩い -> あるい)
    tail = ""
    j = start + len(run)
    while j < len(text) and is_kana(text[j]) and len(tail) < 4:
        tail += text[j]
        j += 1
    pieces: list[tuple[str, str]] = []
    consumed = 0
    for tok in _kks().convert(run + tail):
        orig, hira = tok["orig"], to_hiragana(tok["hira"])
        for part, reading in _split_okurigana(orig, hira):
            if consumed >= len(run):
                break
            if any(is_kanji(c) for c in part):
                take = part[: len(run) - consumed]
                if take != part:  # kanji token extends past the run – should not happen
                    reading = _kanji_reading(take)
                pieces.append((take, reading if is_kana_text(reading) else ""))
                consumed += len(take)
            else:
                # kana belonging to the run cannot happen; it is the tail – stop
                if consumed >= len(run):
                    break
    if consumed < len(run):
        rest = run[consumed:]
        r = _kanji_reading(rest)
        pieces.append((rest, r if is_kana_text(r) else ""))
    return pieces
