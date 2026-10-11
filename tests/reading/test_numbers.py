"""Readings of numbers written in digits: the usual reading, sound changes, other ways they are sung."""

import pytest

from kara_align.reading.numbers import digits_reading, number_reading


@pytest.mark.parametrize("n, reading", [
    (0, "ぜろ"), (4, "よん"), (10, "じゅう"), (11, "じゅういち"), (24, "にじゅうよん"), (100, "ひゃく"),
    (300, "さんびゃく"), (600, "ろっぴゃく"), (800, "はっぴゃく"), (1000, "せん"), (3000, "さんぜん"),
    (8000, "はっせん"), (1999, "せんきゅうひゃくきゅうじゅうきゅう"), (10000, "いちまん"),
    (10_000_000, "いっせんまん"), (120_000_000, "いちおくにせんまん"),
])
def test_number_readings(n, reading):
    assert number_reading(n) == reading


def test_digit_runs():
    assert digits_reading("4") == ("よん", ["よ", "し", "ふぉー"])
    assert digits_reading("24") == ("にじゅうよん", ["にじゅうよ", "にじゅうし"])
    assert digits_reading("17") == ("じゅうなな", ["じゅうしち"])
    assert digits_reading("１００") == ("ひゃく", [])  # full width
    assert digits_reading("0120") == ("ぜろいちにぜろ", [])  # a leading 0: digit by digit
    with pytest.raises(ValueError):
        digits_reading("1a")
