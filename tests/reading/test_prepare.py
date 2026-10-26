import pytest

from kara_align.models import Line, LyricsDoc
from kara_align.reading import chinese, english
from kara_align.reading.prepare import (capability_warnings, prepare_doc, prepare_line, resegment_line,
                                        set_segment_reading)


def test_chinese_units():
    segs = chinese.rule_segments("我爱你，女孩")
    assert "".join(s.surface for s in segs) == "我爱你，女孩"
    units = [u.reading for s in segs for u in s.units]
    assert units == ["wo", "ai", "ni", "nv", "hai"]
    assert all(s.lang == "zh" for s in segs if s.units)


def test_english_units():
    segs = english.rule_segments("Don't stop, me now")
    assert [u.reading for s in segs for u in s.units] == ["don't", "stop", "me", "now"]
    assert "".join(s.surface for s in segs) == "Don't stop, me now"


def test_prepare_doc_and_protection():
    doc = LyricsDoc(lines=[Line(text="君と"), Line(text="作詞：someone", kind="meta", sing=False)])
    rep = prepare_doc(doc)
    assert doc.lines[0].segments and not doc.lines[1].segments
    ln = doc.lines[0]
    kimi = ln.segments[0]
    set_segment_reading(ln, kimi.id, "きみ")
    assert kimi.confirmed and kimi.reading_source == "manual"
    prepare_doc(doc)  # rules must not overwrite
    assert ln.segments[0].reading == "きみ" and ln.segments[0].id == kimi.id
    # text change re-derives and reports
    ln.text = "君を"
    rep = prepare_doc(doc)
    assert ln.id in rep.rederived and "".join(s.surface for s in ln.segments) == "君を"


def test_unit_ids_kept_only_when_grouping_unchanged():
    ln = Line(text="君")
    prepare_line(ln)
    seg = ln.segments[0]
    set_segment_reading(ln, seg.id, "きみ")
    ids = [u.id for u in seg.units]
    set_segment_reading(ln, seg.id, "きみ")  # same grouping
    assert [u.id for u in seg.units] == ids
    set_segment_reading(ln, seg.id, "きみ", units=["きみ"])  # regrouped
    assert len(seg.units) == 1 and seg.units[0].id not in ids


def test_set_segment_reading_bad_units():
    ln = Line(text="君")
    prepare_line(ln)
    with pytest.raises(ValueError):
        set_segment_reading(ln, ln.segments[0].id, "きみ", units=["き"])


def test_resegment_line():
    ln = Line(text="今日も")
    prepare_line(ln)
    resegment_line(ln, [{"surface": "今日", "reading": "きょう"}, {"surface": "も", "reading": "も"}])
    assert [u.reading for u in ln.units()] == ["きょ", "う", "も"]
    assert ln.segments[1].units[0].surface == "も"
    with pytest.raises(ValueError):
        resegment_line(ln, [{"surface": "今日"}])


def test_capability_warnings():
    doc = LyricsDoc(lines=[Line(text="我爱你"), Line(text="君と")])
    prepare_line(doc.lines[0], "zh")
    prepare_line(doc.lines[1], "ja")
    msgs = capability_warnings(doc, ["ja"])
    assert any("中文" in m for m in msgs)
    assert doc.lines[0].segments[0].lang == "zh"  # not forced to Japanese
    assert not capability_warnings(LyricsDoc(lines=[doc.lines[1]]), ["ja"])


def test_the_ruby_of_one_word_is_set_with_its_reading():
    from kara_align.models import Line, LyricsDoc
    from kara_align.reading.prepare import prepare_doc, set_segment_reading

    doc = LyricsDoc(lines=[Line(id="L1", text="君と")])
    prepare_doc(doc)
    ln = doc.lines[0]
    seg = ln.segments[0]
    set_segment_reading(ln, seg.id, "きみ", ruby="hide")
    assert seg.show_ruby is False
    set_segment_reading(ln, seg.id, "きみ")  # not given: unchanged
    assert seg.show_ruby is False
    set_segment_reading(ln, seg.id, "きみ", ruby="auto")
    assert seg.show_ruby is None
    import pytest
    with pytest.raises(ValueError):
        set_segment_reading(ln, seg.id, "きみ", ruby="sometimes")
