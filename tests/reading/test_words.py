"""Word-level Japanese segmentation (MeCab) and regrouping old segmentations."""

import pytest

from kara_align.karaoke.ass import _split_affixes
from kara_align.models import Line, LyricsDoc
from kara_align.reading import japanese as J
from kara_align.reading.ai import build_prompt
from kara_align.reading.prepare import prepare_doc, regroup_words

pytestmark = pytest.mark.skipif(J._tagger() is None, reason="needs fugashi + unidic-lite")


def split(text):
    return [(s.surface, s.reading) for s in J.rule_segments(text)]


def test_kanji_keeps_okurigana_and_particles_stand_alone():
    assert split("好きになってく") == [("好き", "すき"), ("に", "に"), ("なってく", "なってく")]
    assert split("始まりの季節") == [("始まり", "はじまり"), ("の", "の"), ("季節", "きせつ")]
    assert split("震えるその肩") == [("震える", "ふるえる"), ("その", "その"), ("肩", "かた")]
    # auxiliaries / conjunctive particles stay with their verb, the copula does not
    assert split("どうしてだろう") == [("どう", "どう"), ("して", "して"), ("だろう", "だろう")]
    assert split("咲いてみたい") == [("咲いて", "さいて"), ("みたい", "みたい")]


def test_context_readings_and_sung_particles():
    segs = J.rule_segments("君は空へ")
    assert [(s.surface, s.reading) for s in segs] == [("君", "きみ"), ("は", "わ"), ("空", "そら"), ("へ", "え")]
    ha = segs[1].units[0]
    assert (ha.reading, ha.surface) == ("わ", "は")
    assert segs[0].uncertain and segs[0].reading_source == "rule"


@pytest.mark.parametrize("text", ["  空へ　飛べ ", "ラブソング Love you 123回", "「何が可笑しいの?」と聞いた", "君に・・・"])
def test_surfaces_still_concatenate_exactly(text):
    segs = J.rule_segments(text)
    assert "".join(s.surface for s in segs) == text
    assert "・" not in "".join(u.reading for s in segs for u in s.units)


def test_falls_back_to_character_classes_without_mecab(monkeypatch):
    monkeypatch.setitem(J._tagger_state, "t", None)
    assert [s.surface for s in J.rule_segments("好きになってく")] == ["好", "きになってく"]


def _old_style_line(text, source="ai"):
    """A line segmented the old way (by character class), e.g. copied by an AI reply."""
    ln = Line(text=text, segments=J._class_segments(text))
    for s in ln.segments:
        if s.units:
            s.reading_source = source
    return ln


def test_regroup_merges_okurigana_keeping_units_and_readings():
    ln = _old_style_line("好きになってく")
    ln.segments[0].candidates = ["この"]
    units = [(u.id, u.reading) for u in ln.units()]
    assert regroup_words(ln)
    assert [(s.surface, s.reading) for s in ln.segments] == [("好き", "すき"), ("に", "に"), ("なってく", "なってく")]
    assert [(u.id, u.reading) for u in ln.units()] == units  # alignment results stay valid
    assert ln.segments[0].reading_source == "ai"
    assert ln.segments[0].candidates == ["このき"]  # alternative reading carries its okurigana
    assert not regroup_words(ln)  # idempotent


def test_regroup_keeps_a_katakana_reading_and_a_line_break():
    ln = _old_style_line("好きになってく")
    ln.segments[0].katakana = True  # (as typed: スキ)
    ln.segments[0].wrap_before = True
    assert regroup_words(ln)
    assert ln.segments[0].surface == "好き" and ln.segments[0].katakana and ln.segments[0].wrap_before


def test_regroup_never_touches_confirmed_or_splits_kanji_readings():
    ln = _old_style_line("今君へと")
    assert [s.surface for s in ln.segments][:2] == ["今", "君"]
    # 今 / 君 were read separately: they keep their own readings even though MeCab joins them
    regroup_words(ln)
    assert [s.surface for s in ln.segments] == ["今", "君", "へと"]
    ln2 = _old_style_line("好きになってく")
    ln2.segments[1].confirmed = True
    regroup_words(ln2)
    assert ln2.segments[1].surface == "きになってく"  # confirmed: left exactly as it is


def test_prepare_reports_regrouped_lines():
    doc = LyricsDoc(lines=[_old_style_line("窓に舞う桜")])
    rep = prepare_doc(doc)
    assert rep.regrouped == [doc.lines[0].id]
    assert [s.surface for s in doc.lines[0].segments] == ["窓", "に", "舞う", "桜"]


def test_ruby_goes_over_each_kanji_of_a_word():
    seg = J.rule_segments("笑い合えた")[0]
    assert seg.surface == "笑い合えた"
    assert [(p, "".join(u.reading for u in us)) for p, us in _split_affixes(seg)] == [
        ("笑", "わら"), ("い", "い"), ("合", "あ"), ("えた", "えた")]


def test_ai_prompt_asks_for_word_segments():
    doc = LyricsDoc(lines=[Line(text="好きになってく")])
    prepare_doc(doc)
    prompt = build_prompt(doc).prompt
    assert "按词切分" in prompt and "真新＝まっさら" in prompt and "可以合并或重新切分" in prompt
