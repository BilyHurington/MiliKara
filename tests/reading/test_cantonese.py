"""Cantonese readings, the mora proxy and the mixed-mora profile.

The interesting parts are not "does ToJyutping return a reading" but the shape the
aligner sees: one unit per hanzi, no tones, and a proxy text the Japanese model
was actually trained on.
"""

import pytest

from kara_align.align.backends import BACKENDS
from kara_align.align.backends.wav2vec2_ctc import MMSSinophoneBackend
from kara_align.models import Line, LyricsDoc
from kara_align.reading import cantonese, proxy
from kara_align.reading.prepare import (capability_warnings, prepare_doc, set_doc_language,
                                       unsupported_languages)
from kara_align.reading.profiles import get_profile, is_mora_text

jyutping = pytest.mark.skipif(cantonese.source() is None, reason="需要 Jyutping 词典（ToJyutping / pycantonese）")


# --------------------------------------------------------------------------- proxy tables

@pytest.mark.parametrize(("pinyin", "want"), [
    ("wo", "o"),        # 我
    ("ni", "ni"),       # 你
    ("xiang", "shan"),  # 想
    ("guang", "gan"),   # 光
    ("shi", "shi"),     # 是
    ("zi", "tsu"),      # 字
    ("er", "a"),        # 二
    ("yu", "yu"),       # 雨
    ("yuan", "yen"),    # 元
    ("jun", "jun"),     # 军
    ("nv", "nyu"),      # 女
    ("m", "mu"),        # 呣
])
def test_pinyin_to_mora(pinyin, want):
    assert proxy.pinyin_to_mora(pinyin) == want


@pytest.mark.parametrize(("jyt", "want"), [
    ("ngo", "ngo"),     # 我
    ("hoeng", "hen"),   # 香
    ("keoi", "kui"),    # 佢
    ("m", "mu"),        # 唔
    ("zoeng", "jen"),   # 張
    ("gwaan", "guan"),  # 關
    ("mat", "mat"),     # 乜 (checked coda -t)
    ("sik", "shik"),    # 識 (checked coda -k)
])
def test_jyutping_to_mora(jyt, want):
    assert proxy.jyutping_to_mora(jyt) == want


def test_tones_never_reach_the_proxy():
    assert proxy.jyutping_to_mora("hoeng1") == proxy.jyutping_to_mora("hoeng2") == "hen"


def test_every_proxy_text_is_mora_spelled():
    """The whole point: whatever the proxy emits must be in the model's own inventory."""
    for s in ("wo", "xiang", "guang", "yuan", "jun", "zi", "er", "hai", "shui", "jiong"):
        text = proxy.pinyin_to_mora(s)
        assert text and is_mora_text(text), (s, text)
    for s in ("ngo", "hoeng", "keoi", "zoeng", "mat", "sik", "jyu", "leon"):
        text = proxy.jyutping_to_mora(s)
        assert text and is_mora_text(text), (s, text)


def test_is_mora_text_rejects_pinyin_letters():
    assert is_mora_text("hen")          # へん
    assert not is_mora_text("zhang")    # 后接 z 不是日语拍的拼法
    assert is_mora_text("mat")          # 尾字是塞音韵尾


# --------------------------------------------------------------------------- readings

def test_toneless_and_markers():
    assert cantonese.toneless("hoeng1 gong2") == "hoeng gong"
    assert cantonese.looks_cantonese(["我哋嘅歌"])
    assert not cantonese.looks_cantonese(["我们的歌"])


@jyutping
def test_cantonese_units_one_per_hanzi():
    segs = cantonese.rule_segments("我哋嘅歌")
    assert "".join(s.surface for s in segs) == "我哋嘅歌"
    readings = [u.reading for s in segs for u in s.units]
    assert len(readings) == 4
    assert readings[0] == "ngo"
    assert all(s.lang == "yue" for s in segs if s.units)
    assert all(r and not r[-1].isdigit() for r in readings)


def test_without_a_dictionary_the_reading_is_pinyin(monkeypatch):
    """No Jyutping dictionary installed: pinyin is the wrong sound but never a silent drop,
    and the diagnostics say what to install."""
    monkeypatch.setattr(cantonese, "_loaded", True)
    monkeypatch.setattr(cantonese, "_source", None)
    assert cantonese.source() is None
    assert "milikara[yue]" in cantonese.missing_hint()
    assert cantonese.jyutping_readings("我")[0][1] == "wo"  # pinyin fallback, not "ngo"


# --------------------------------------------------------------------------- document language

@jyutping
def test_cantonese_is_detected_and_switchable_back():
    doc = LyricsDoc(lines=[Line(text="我哋嘅歌")])
    doc.language = "zh"
    prepare_doc(doc)
    assert all(s.lang == "zh" for s in doc.lines[0].segments if s.units)
    assert set_doc_language(doc, "yue") > 0
    prepare_doc(doc)
    assert doc.language == "yue"
    assert all(s.lang == "yue" for s in doc.lines[0].segments if s.units)


def test_language_switch_keeps_manual_readings():
    doc = LyricsDoc(lines=[Line(text="你好")])
    prepare_doc(doc)
    seg = doc.lines[0].segments[0]
    seg.reading = "nay hou"
    seg.reading_source = "manual"
    seg.confirmed = True
    set_doc_language(doc, "yue")
    prepare_doc(doc)
    kept = [s for s in doc.lines[0].segments if s.reading_source == "manual"]
    assert kept and kept[0].reading == "nay hou"


# --------------------------------------------------------------------------- profile + backend

def test_mixed_profile_rewrites_only_the_sinophone_units():
    prof = get_profile("mixed-mora")
    # a Japanese kana unit, a pinyin unit, a Jyutping unit and an English word side by side
    texts = prof.unit_texts(["きみ", "wo", "hoeng", "love"], ["ja", "zh", "yue", "en"], [[], [], [], []])
    assert texts == ["kimi", "o", "hen", "love"]


def test_mixed_profile_keeps_the_japanese_hold_and_sokuon():
    prof = get_profile("mixed-mora")
    texts = prof.unit_texts(["き", "ー", "っ", "と"], ["ja"] * 4, [[], [], ["sokuon"], []])
    assert texts == ["ki", "", "t", "to"]


def test_ja_and_mixed_profiles_agree_on_japanese():
    ja, mixed = get_profile("ja-hepburn"), get_profile("mixed-mora")
    readings, langs, flags = ["きみ", "と", "ー", "っ", "た"], ["ja"] * 5, [[], [], [], ["sokuon"], []]
    assert ja.unit_texts(readings, langs, flags) == mixed.unit_texts(readings, langs, flags)


def test_mixed_backend_is_registered_with_the_ja_weights():
    assert "mms-mixed" in BACKENDS
    entry = BACKENDS["mms-mixed"]
    assert entry["languages"] == ["ja", "zh", "yue", "en"]
    assert entry["default_model"] == BACKENDS["mms-ja"]["default_model"]
    assert entry["default_revision"] == BACKENDS["mms-ja"]["default_revision"]
    backend = MMSSinophoneBackend(model_id=entry["default_model"])
    assert backend.profile == "mixed-mora" and backend.supports_language("yue")
    assert not MMSSinophoneBackend(model_id="x").supports_language("ko")


def test_the_ja_backend_keeps_its_own_languages():
    """Only the new backend claims Chinese; the ja one must keep warning about it."""
    assert BACKENDS["mms-ja"]["languages"] == ["ja"]
    assert MMSSinophoneBackend.profile == "mixed-mora" and MMSSinophoneBackend.languages == ("ja", "zh", "yue", "en")


# --------------------------------------------------------------------------- warnings / calibration

def test_unsupported_languages_names_what_the_model_cannot_hear():
    doc = LyricsDoc(lines=[Line(text="我哋嘅歌")], language="yue")
    prepare_doc(doc)
    assert unsupported_languages(doc, ["ja"]) == ["yue"]
    assert unsupported_languages(doc, ["ja", "zh", "yue", "en"]) == []
    warn = capability_warnings(doc, ["ja"])
    assert any("粤语" in w for w in warn)


# --------------------------------------------------------------------------- whole inventories

def test_every_pinyin_syllable_becomes_a_mora_spelling():
    """The proxy is a table, so the only way to know it has no holes is to try every syllable.

    Two used to fail: 湖 hu -> "hu" and 会 hui -> "hui" — Japanese spells [hɯ] as ふ "fu".
    """
    import re

    from pypinyin.pinyin_dict import pinyin_dict

    syllables = {re.sub(r"[1-5]", "", s) for s in pinyin_dict.values()}
    assert len(syllables) > 400
    bad = sorted((s, proxy.pinyin_to_mora(s)) for s in syllables if not is_mora_text(proxy.pinyin_to_mora(s) or ""))
    assert bad == []
    assert proxy.pinyin_to_mora("hu") == "fu" and proxy.pinyin_to_mora("hui") == "fui"


def test_every_jyutping_syllable_becomes_a_mora_spelling():
    """Same sweep over ToJyutping's bundled dictionary, when it is installed.

    兒 ji -> "i" and 屋 wu -> "u" used to fail (no イ spelled "yi", no ウ spelled "wu").
    """
    if cantonese.source() != "ToJyutping":
        pytest.skip("需要 ToJyutping 的词典")
    import os
    import re

    import ToJyutping

    path = os.path.join(os.path.dirname(ToJyutping.__file__), "trie.txt")
    syllables = {re.sub(r"\d$", "", s) for s in re.findall(r"[a-z]+\d", open(path, encoding="utf8").read())}
    assert len(syllables) > 300
    bad = sorted((s, proxy.jyutping_to_mora(s)) for s in syllables if not is_mora_text(proxy.jyutping_to_mora(s) or ""))
    assert bad == []
    assert proxy.jyutping_to_mora("ji") == "i" and proxy.jyutping_to_mora("wu") == "u"
