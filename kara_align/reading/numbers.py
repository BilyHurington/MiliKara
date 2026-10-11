"""Readings of numbers written in digits (24 → にじゅうよん).

The usual reading comes first; other ways a number is often sung (よ / し for 4, ワン for 1, ひとつ …)
are offered as candidates.  Which one a song uses depends on the song, so these readings stay marked
for checking.  Numbers starting with 0 (0120) and very long ones are read digit by digit.
"""

from __future__ import annotations

_DIGITS = ["ぜろ", "いち", "に", "さん", "よん", "ご", "ろく", "なな", "はち", "きゅう"]
_HUNDREDS = {1: "ひゃく", 3: "さんびゃく", 6: "ろっぴゃく", 8: "はっぴゃく"}
_THOUSANDS = {1: "せん", 3: "さんぜん", 8: "はっせん"}
# a single digit: other readings heard in songs (counted, English, the older on-readings)
_SINGLE_ALTS = {
    0: ["れい", "まる"], 1: ["わん", "ひとつ"], 2: ["つー", "ふたつ"], 3: ["すりー", "みっつ"],
    4: ["よ", "し", "ふぉー"], 5: ["ふぁいぶ", "いつつ"], 6: ["しっくす", "むっつ"],
    7: ["しち", "せぶん", "ななつ"], 8: ["えいと", "やっつ"], 9: ["く", "ないん", "ここのつ"],
}
_FULLWIDTH = str.maketrans("０１２３４５６７８９", "0123456789")
MAX_CANDIDATES = 3


def _below_10000(n: int, before_unit: bool = False) -> str:
    out = ""
    th, n = divmod(n, 1000)
    if th:
        # 1000 alone is せん; 10,000,000 is いっせんまん
        out += "いっせん" if th == 1 and before_unit else _THOUSANDS.get(th, _DIGITS[th] + "せん")
    h, n = divmod(n, 100)
    if h:
        out += _HUNDREDS.get(h, _DIGITS[h] + "ひゃく")
    t, n = divmod(n, 10)
    if t:
        out += ("" if t == 1 else _DIGITS[t]) + "じゅう"
    if n:
        out += _DIGITS[n]
    return out


def number_reading(n: int) -> str:
    """The usual reading of a whole number (0 ≤ n < 10^12)."""
    if n == 0:
        return _DIGITS[0]
    parts = []
    for unit, size in (("おく", 10 ** 8), ("まん", 10 ** 4)):
        q, n = divmod(n, size)
        if q:
            parts.append(_below_10000(q, before_unit=True) + unit)
    if n:
        parts.append(_below_10000(n))
    return "".join(parts)


def digits_reading(run: str) -> tuple[str, list[str]]:
    """A run of digits (half- or full-width) → (reading, candidates), all hiragana."""
    s = run.translate(_FULLWIDTH)
    if not s.isdigit():
        raise ValueError(f"not digits: {run!r}")
    if (len(s) > 1 and s[0] == "0") or len(s) > 12:
        return "".join(_DIGITS[int(c)] for c in s), []
    n = int(s)
    reading = number_reading(n)
    alts: list[str] = []
    if n < 10:
        alts = list(_SINGLE_ALTS[n])
    else:
        last = n % 10
        stem = reading[: -len(_DIGITS[last])] if last else ""
        if last == 4:
            alts = [stem + "よ", stem + "し"]
        elif last == 7:
            alts = [stem + "しち"]
        elif last == 9:
            alts = [stem + "く"]
    alts = [a for a in dict.fromkeys(alts) if a != reading]
    return reading, alts[:MAX_CANDIDATES]
