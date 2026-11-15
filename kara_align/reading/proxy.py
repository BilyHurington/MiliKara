"""Chinese / Cantonese syllables rewritten as Japanese mora spellings ("proxy").

Why
---
The default acoustic model (``NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn``)
was fine-tuned on **Japanese** karaoke romaji: its CTC head knows the letter
sequences Japanese mora are spelled with (``ka``, ``shi``, ``cha``, ``ja``,
``tsu``, ``n``), and it learned them from Japanese singing.  Raw pinyin or
Jyutping letters are a different currency:

* ``zhuang`` is six letters for one sung syllable — the aligner has to spread
  ``z,h,u,a,n,g`` over one note, which no Japanese-trained model ever saw;
* ``x``, ``q``, ``c`` and the ``oe`` / ``eo`` / ``yu`` of Jyutping spell sounds
  the model associates with other letters entirely;
* Japanese does not end a syllable in a consonant except ``n``, so a final
  ``-ng`` becomes a vowel + ``n``.

So each syllable is rewritten as the *Japanese mora* a Japanese speaker would
hear: 我 wo -> ``o``, 想 xiang -> ``shan``, 光 guang -> ``gan``, 唔 m -> ``mu``,
香 hoeng -> ``hen``, 佢 keoi -> ``kui``.  Every output is decomposable into
:data:`kara_align.reading.profiles`'s mora romaji, i.e. nothing but sounds the
model was trained on; the input is still one unit per hanzi, so the alignment
still maps back to characters.

This is a **rough perceptual proxy**, not a phonemic transcription: tones (six
in Cantonese), the checked codas ``-p -t -k`` and Cantonese vowel length are
only approximated.  It exists to make the current weights usable; a model
fine-tuned on Chinese singing is the real fix (see docs/technical.md).
"""

from __future__ import annotations

import re

_TONE_RE = re.compile(r"[0-9]+$")

# ---------------------------------------------------------------------------
# Mandarin (pinyin, tone-less, ü written as pinyin's own "v": 女 nv)
# ---------------------------------------------------------------------------

_PINYIN_INITIALS = ("zh", "ch", "sh", "b", "p", "m", "f", "d", "t", "n", "l",
                    "g", "k", "h", "j", "q", "x", "r", "z", "c", "s", "y", "w")

# Japanese has no retroflex and no palatal sibilants: the closest mora are used
# (zh -> j as in 中国 ちゅうごく, q -> ch, x -> sh, r -> j as in 人 じん, z/c -> ts).
_PINYIN_INITIAL = {
    "b": "b", "p": "p", "m": "m", "f": "f", "d": "d", "t": "t", "n": "n", "l": "r",
    "g": "g", "k": "k", "h": "h", "j": "j", "q": "ch", "x": "sh", "r": "j",
    "zh": "j", "ch": "ch", "sh": "sh", "z": "ts", "c": "ts", "s": "s",
    "y": "", "w": "",
}

# whole syllables pinyin spelling hides: the apical vowel after zh/ch/sh/r/z/c/s (是 = shi, not
# sh + i) and the syllabic nasals (嗯, 呣)
_PINYIN_EXACT = {
    "zhi": "ji", "chi": "chi", "shi": "shi", "ri": "ji",
    "zi": "tsu", "ci": "tsu", "si": "su",
    "er": "a", "m": "mu", "n": "n", "ng": "n", "hm": "mu", "hng": "n",
}

# pinyin final -> vowel part of the mora; a nasal coda is already the trailing n
_PINYIN_FINAL = {
    "a": "a", "o": "o", "e": "e", "ai": "ai", "ei": "ei", "ao": "ao", "ou": "o",
    "an": "an", "en": "en", "ang": "an", "eng": "en", "ong": "on", "er": "a",
    "i": "i", "ia": "a", "ie": "e", "iao": "ao", "iu": "u", "ian": "en", "in": "in",
    "iang": "an", "ing": "in", "iong": "on",
    "u": "u", "ua": "a", "uo": "o", "uai": "ai", "ui": "ui", "uan": "an", "un": "un",
    "uang": "an", "ueng": "en",
    "v": "yu", "ve": "e", "van": "en", "vn": "un",
}

# j/q/x are written with u for ü (ju = jü): there the vowel is front rounded, the i of ia/ie is the
# glide itself (家 jia -> ja, 谢 xie -> she, 学 xue -> she, 军 jun -> jun)
_PALATAL = frozenset("jqx")
_PINYIN_PALATAL_FINAL = {
    "i": "i", "ia": "a", "ie": "e", "iao": "ao", "iu": "o", "ian": "en", "in": "in",
    "iang": "an", "ing": "in", "iong": "on",
    "u": "u", "ue": "e", "uan": "en", "un": "un",
    "v": "u", "ve": "e", "van": "en", "vn": "un",
}


def pinyin_to_mora(syllable: str) -> str:
    """One tone-less pinyin syllable -> a Japanese mora spelling (``""`` when nothing matches)."""
    s = _TONE_RE.sub("", syllable.strip().lower()).replace("ü", "v")
    if not s:
        return ""
    if s in _PINYIN_EXACT:
        return _PINYIN_EXACT[s]
    initial, final = "", s
    for ini in _PINYIN_INITIALS:
        if s.startswith(ini):
            initial, final = ini, s[len(ini):]
            break
    if final in _PINYIN_EXACT:  # yao / wan / ye: the y / w already carried the glide
        return _PINYIN_EXACT[final]
    if initial in _PALATAL:
        vowel = _PINYIN_PALATAL_FINAL.get(final, "")
    elif initial == "y" and final in ("u", "ue", "uan", "un", "v", "ve", "van", "vn"):
        vowel = "y" + _PINYIN_PALATAL_FINAL.get(final, "")  # 雨 yu -> yu, 元 yuan -> yen
    else:
        vowel = _PINYIN_FINAL.get(final, "")
        if initial in ("n", "l") and final == "iu":
            vowel = "yo"  # 六 liu -> ryo, 牛 niu -> nyo (the glide is audible)
    if not vowel:
        return ""
    if initial == "y" and vowel.startswith("y"):
        return vowel
    if initial == "h" and vowel.startswith("u"):
        initial = "f"  # 湖 hu -> fu, 会 hui -> fui, 换 huan -> fan: Japanese writes [hɯ] as ふ
    return _PINYIN_INITIAL.get(initial, "") + vowel


# ---------------------------------------------------------------------------
# Cantonese (Jyutping, tone-less: hoeng, ngo, keoi, m)
# ---------------------------------------------------------------------------

_JYUTPING_INITIALS = ("ng", "gw", "kw", "b", "p", "m", "f", "d", "t", "n", "l",
                      "g", "k", "h", "w", "j", "z", "c", "s")

# Cantonese z/c are [ts]/[tsʰ] (Japanese j/ch), j is [j], l is [ɾ] (a Japanese r)
_JYUTPING_INITIAL = {
    "b": "b", "p": "p", "m": "m", "f": "f", "d": "d", "t": "t", "n": "n", "l": "r",
    "g": "g", "k": "k", "h": "h", "ng": "ng", "gw": "gu", "kw": "ku",
    "w": "w", "j": "y", "z": "j", "c": "ch", "s": "sh",
}

# longest first: aa before a, oe / eo / yu before o / e / u
_JYUTPING_NUCLEI = ("aa", "oe", "eo", "yu", "a", "e", "i", "o", "u")
_JYUTPING_NUCLEUS = {"aa": "a", "a": "a", "e": "e", "i": "i", "o": "o", "u": "u",
                     "oe": "e", "eo": "u", "yu": "u"}

# -m / -ng are both ん; -p / -t / -k are unreleased stops, kept as their letter (the model has it)
_JYUTPING_CODAS = ("ng", "p", "t", "k", "m", "n", "i", "u")
_JYUTPING_CODA = {"ng": "n", "m": "n", "n": "n", "p": "p", "t": "t", "k": "k",
                  "i": "i", "u": "u"}

_JYUTPING_EXACT = {"m": "mu", "ng": "ngo", "hm": "mu", "hng": "n"}


def jyutping_to_mora(syllable: str) -> str:
    """One tone-less Jyutping syllable -> a Japanese mora spelling (``""`` when nothing matches)."""
    s = _TONE_RE.sub("", syllable.strip().lower())
    if not s:
        return ""
    if s in _JYUTPING_EXACT:
        return _JYUTPING_EXACT[s]
    initial, rest = "", s
    for ini in _JYUTPING_INITIALS:
        if s.startswith(ini):
            initial, rest = ini, s[len(ini):]
            break
    nucleus, coda = "", ""
    for nuc in _JYUTPING_NUCLEI:
        if rest.startswith(nuc):
            nucleus, coda = nuc, rest[len(nuc):]
            break
    if not nucleus:
        return ""
    if coda and coda not in _JYUTPING_CODA:
        return ""
    start = _JYUTPING_INITIAL.get(initial, "")
    body = _JYUTPING_NUCLEUS[nucleus] + _JYUTPING_CODA.get(coda, "")
    if start == "y" and body == "i":
        return "i"  # 兒 ji -> i: Japanese has no イ spelled "yi"
    if start == "w" and body.startswith("u"):
        return body  # 屋 wu -> u, 會 wui -> ui, 換 wun -> un: ウ is already [w]u
    return start + body
