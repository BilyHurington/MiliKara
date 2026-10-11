import pytest

from kara_align.reading.japanese import rule_segments, split_morae, to_hiragana
from kara_align.reading.profiles import get_profile, kana_to_romaji


def morae(s):
    return [(m.text, m.flags) for m in split_morae(s)]


def test_yoon_merged():
    assert [m for m, _ in morae("きゃしゅちぇ")] == ["きゃ", "しゅ", "ちぇ"]
    assert [m for m, _ in morae("ふぁヴぁ")] == ["ふぁ", "ゔぁ"]


def test_sokuon_hatsuon_long():
    assert morae("かっぱ") == [("か", []), ("っ", ["sokuon"]), ("ぱ", [])]
    assert morae("さんぽ") == [("さ", []), ("ん", ["hatsuon"]), ("ぽ", [])]
    assert morae("らーめん〜") == [("ら", []), ("ー", ["long"]), ("め", []), ("ん", ["hatsuon"]), ("ー", ["long"])]


def test_katakana_normalized():
    assert to_hiragana("カタカナ") == "かたかな"
    assert [m for m, _ in morae("ティッシュ")] == ["てぃ", "っ", "しゅ"]


@pytest.mark.parametrize("text", [
    "君と歩いた道を、今日も", "ラブソング Love you 123回", "ちょっと待って！", "  空へ　飛べ ", "東京タワー",
])
def test_rule_segments_roundtrip(text):
    segs = rule_segments(text)
    assert "".join(s.surface for s in segs) == text
    for s in segs:
        if s.reading and s.lang == "ja":
            assert "".join(u.reading for u in s.units) == s.reading


def test_rule_segments_kanji_uncertain_and_kana_surface():
    segs = rule_segments("君と")
    kanji, kana = segs[0], segs[1]
    assert kanji.surface == "君" and kanji.uncertain and kanji.reading_source == "rule"
    assert all(u.surface == "" for u in kanji.units)
    assert kana.units[0].surface == "と"


def test_digits_get_the_usual_reading_to_check():
    segs = rule_segments("123回")
    s = segs[0]
    assert (s.surface, s.reading, s.reading_source) == ("123", "ひゃくにじゅうさん", "rule")
    assert s.uncertain and [u.reading for u in s.units] == ["ひゃ", "く", "に", "じゅ", "う", "さ", "ん"]
    segs = rule_segments("1、2、3で")
    assert [(x.surface, x.reading) for x in segs if x.units][:3] == [("1", "いち"), ("2", "に"), ("3", "さん")]
    assert segs[0].candidates == ["わん", "ひとつ"]  # other ways it is sung: tried when the line fits badly
    assert rule_segments("２４時")[0].reading == "にじゅうよん"  # full-width digits too


def test_latin_in_japanese():
    segs = rule_segments("Love you")
    assert [s.lang for s in segs if s.units] == ["en", "en"]


def ht(readings, langs=None):
    p = get_profile("ja-hepburn")
    units = [(m.text, m.flags) for r in readings for m in split_morae(r)] if langs is None else None
    if units is None:
        return p.unit_texts(readings, langs, [[] for _ in readings])
    return p.unit_texts([u for u, _ in units], ["ja"] * len(units), [f for _, f in units])


def test_hepburn_basic():
    assert [kana_to_romaji(k) for k in ["し", "ち", "つ", "ふ", "じ", "ぢ", "づ", "を", "は"]] == \
        ["shi", "chi", "tsu", "fu", "ji", "ji", "zu", "o", "ha"]
    assert [kana_to_romaji(k) for k in ["きゃ", "しゃ", "ちゃ", "じゃ", "ふぁ", "ゔ", "にょ"]] == \
        ["kya", "sha", "cha", "ja", "fa", "vu", "nyo"]


def test_hepburn_sokuon_and_long():
    assert ht(["かっぱ"]) == ["ka", "p", "pa"]
    assert ht(["まっちゃ"]) == ["ma", "t", "cha"]
    assert ht(["がんばっ"]) == ["ga", "n", "ba", ""]  # no following consonant -> empty, reported unaligned
    # a ー unit has no tokens of its own (the aligner times it from the unit before it);
    # inside a unit it is dropped
    assert ht(["らーめん"]) == ["ra", "", "me", "n"]
    assert get_profile().unit_texts(["あーる", "えむ"], ["ja", "ja"], [["long"], []]) == ["aru", "emu"]


def test_profile_other_langs_and_version():
    p = get_profile()
    assert p.version == "ja-hepburn/2"
    assert p.unit_texts(["love", "nv"], ["en", "zh"], [[], []]) == ["love", "nv"]
    with pytest.raises(KeyError):
        get_profile("nope")


def test_middle_dot_is_punctuation_not_kana():
    """・・・ (katakana middle dot) is common in lyrics and must not become units."""
    from kara_align.reading.japanese import is_kana, rule_segments

    assert not is_kana("・")
    segs = rule_segments("君に・・・")
    assert "".join(s.surface for s in segs) == "君に・・・"
    units = [u.reading for s in segs for u in s.units]
    assert "・" not in "".join(units)
    dots = [s for s in segs if "・" in s.surface]
    assert dots and all(not s.units for s in dots)
