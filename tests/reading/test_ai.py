import json

import pytest

from kara_align.models import FMT_READING_PATCH, Line, LyricsDoc
from kara_align.reading.ai import PatchParseError, apply_patch, build_prompt, extract_json, validate_patch
from kara_align.reading.prepare import prepare_doc, set_segment_reading


def make_doc():
    doc = LyricsDoc(lines=[
        Line(id="L1", text="君と", imported_start_ms=1000),
        Line(id="L2", text="空へ", imported_start_ms=2000),
        Line(id="L3", text="君と", imported_start_ms=3000),  # repeated chorus instance
    ])
    prepare_doc(doc)
    return doc


def good_patch(snap, ids=("L1", "L2", "L3")):
    lines = {
        "L1": {"id": "L1", "text": "君と", "segments": [
            {"surface": "君", "reading": "きみ", "units": ["き", "み"]}, {"surface": "と", "reading": "と", "units": ["と"]}]},
        "L2": {"id": "L2", "text": "空へ", "segments": [
            {"surface": "空", "reading": "そら", "units": ["そ", "ら"]}, {"surface": "へ", "reading": "え", "units": ["え"]}]},
        "L3": {"id": "L3", "text": "君と", "segments": [
            {"surface": "君", "reading": "きみ", "units": ["き", "み"], "candidates": ["くん"], "uncertain": True},
            {"surface": "と", "reading": "と"}]},
    }
    return {"format": FMT_READING_PATCH, "version": 1, "snapshot": snap, "lines": [lines[i] for i in ids]}


def test_prompt_contents():
    doc = make_doc()
    b = build_prompt(doc)
    for s in ["L1", "L2", "L3", "君と", b.snapshot_id, "同上", "surface", "units", "时间"]:
        assert s in b.prompt
    assert b.roundtrip.line_ids == ["L1", "L2", "L3"]
    set_segment_reading(doc.lines[0], doc.lines[0].segments[0].id, "きみ")
    assert '"locked": true' in build_prompt(doc).prompt


@pytest.mark.parametrize("wrap", [
    "{j}", "```json\n{j}\n```", "好的，结果如下：\n```\n{j}\n```\n希望有帮助", "Here you go: {j} thanks",
])
def test_extract_json_variants(wrap):
    j = json.dumps({"a": 1, "lines": []})
    assert extract_json(wrap.format(j=j))["a"] == 1


def test_extract_json_rejects():
    with pytest.raises(PatchParseError):
        extract_json("no json here")
    with pytest.raises(PatchParseError):
        extract_json("x" * 3_000_000)


def test_valid_patch_and_idempotent_apply():
    doc = make_doc()
    b = build_prompt(doc)
    rep = validate_patch(doc, good_patch(b.snapshot_id), [b.roundtrip])
    assert rep.ok and rep.applicable_ids() == ["L1", "L2", "L3"], rep
    new, summary = apply_patch(doc, rep)
    assert [u.reading for u in new.lines[0].units()] == ["き", "み", "と"]
    assert new.lines[1].segments[1].reading == "え"
    assert new.lines[2].segments[0].candidates == ["くん"] and new.lines[2].segments[0].uncertain
    assert new.lines[0].segments[0].reading_source == "ai" and not new.lines[0].segments[0].confirmed
    # repeated chorus instances keep distinct unit ids
    assert {u.id for u in new.lines[0].units()}.isdisjoint({u.id for u in new.lines[2].units()})
    # original doc untouched; timing fields untouched
    assert [ln.imported_start_ms for ln in new.lines] == [1000, 2000, 3000]
    rep2 = validate_patch(new, good_patch(b.snapshot_id), [b.roundtrip])
    new2, summary2 = apply_patch(new, rep2)
    assert summary2["applied"] == [] and new2.model_dump() == new.model_dump()


def test_unit_ids_preserved_when_grouping_same():
    from kara_align.reading.japanese import reading_units

    doc = make_doc()
    kimi = doc.lines[0].segments[0]
    kimi.reading, kimi.units = "くん", reading_units("くん")  # a context-free rule reading the AI corrects
    b = build_prompt(doc)
    to_ids = [u.id for u in doc.lines[0].segments[1].units]  # と unchanged grouping
    new, _ = apply_patch(doc, validate_patch(doc, good_patch(b.snapshot_id), [b.roundtrip]))
    assert [u.id for u in new.lines[0].segments[1].units] == to_ids
    old_kimi = {u.id for u in doc.lines[0].segments[0].units}  # くん -> きみ same count; readings changed
    assert not old_kimi & {u.id for u in new.lines[0].segments[0].units}


def test_changed_text_is_stale():
    doc = make_doc()
    b = build_prompt(doc)
    doc.lines[1].text = "空を"
    prepare_doc(doc)
    rep = validate_patch(doc, good_patch(b.snapshot_id), [b.roundtrip])
    st = {lr.line_id: lr.status for lr in rep.lines}
    assert st["L2"] == "stale_text" and st["L1"] == "ok"
    assert any("变更" in w for w in rep.warnings)


def test_omitted_lines_and_doujou():
    doc = make_doc()
    b = build_prompt(doc)
    p = good_patch(b.snapshot_id, ids=("L1", "L2"))
    rep = validate_patch(doc, p, [b.roundtrip])
    assert rep.missing_line_ids == ["L3"]
    p = good_patch(b.snapshot_id)
    p["lines"][2] = {"id": "L3", "text": "同上", "segments": [{"surface": "同上", "reading": "どうじょう"}]}
    rep = validate_patch(doc, p, [b.roundtrip])
    assert {lr.line_id: lr.status for lr in rep.lines}["L3"] == "invalid"


def test_time_keys_rejected():
    doc = make_doc()
    b = build_prompt(doc)
    p = good_patch(b.snapshot_id)
    p["lines"][0]["start_ms"] = 1234
    rep = validate_patch(doc, p, [b.roundtrip])
    assert not rep.ok and rep.errors
    p = good_patch(b.snapshot_id)
    p["offset"] = 100
    assert validate_patch(doc, p, [b.roundtrip]).errors


def test_broken_concatenation_and_bad_units():
    doc = make_doc()
    b = build_prompt(doc)
    p = good_patch(b.snapshot_id)
    p["lines"][0]["segments"][1]["surface"] = "が"
    p["lines"][1]["segments"][0]["units"] = ["そ"]
    rep = validate_patch(doc, p, [b.roundtrip])
    st = {lr.line_id: lr.status for lr in rep.lines}
    assert st["L1"] == "invalid" and st["L2"] == "invalid" and st["L3"] == "ok"


def test_added_vowels_rejected():
    doc = LyricsDoc(lines=[Line(id="A", text="そら")])
    prepare_doc(doc)
    b = build_prompt(doc)
    p = {"format": FMT_READING_PATCH, "version": 1, "snapshot": b.snapshot_id,
         "lines": [{"id": "A", "text": "そら", "segments": [{"surface": "そら", "reading": "そおら"}]}]}
    assert validate_patch(doc, p, [b.roundtrip]).lines[0].status == "invalid"


def test_locked_segment_not_overwritten():
    doc = make_doc()
    kimi = doc.lines[0].segments[0]
    set_segment_reading(doc.lines[0], kimi.id, "きみ")
    locked_ids = [u.id for u in kimi.units]
    b = build_prompt(doc)
    p = good_patch(b.snapshot_id)
    p["lines"][0]["segments"][0] = {"surface": "君", "reading": "くん", "units": ["く", "ん"]}
    rep = validate_patch(doc, p, [b.roundtrip])
    new, _ = apply_patch(doc, rep)
    s = new.lines[0].segments[0]
    assert s.reading == "きみ" and s.confirmed and [u.id for u in s.units] == locked_ids
    # conflicting segmentation over a locked span -> locked_skipped
    p["lines"][0]["segments"] = [{"surface": "君と", "reading": "きみと"}]
    rep = validate_patch(doc, p, [b.roundtrip])
    assert rep.lines[0].status == "locked_skipped"


def test_offset_change_does_not_block():
    doc = make_doc()
    b = build_prompt(doc)
    doc.embedded_shift_ms = -500  # only LRC offset / calibration changes
    doc.embedded_offset_raw = "500"
    rep = validate_patch(doc, good_patch(b.snapshot_id), [b.roundtrip])
    assert rep.applicable_ids() == ["L1", "L2", "L3"] and not rep.warnings
    new, _ = apply_patch(doc, rep)
    assert new.embedded_shift_ms == -500 and new.embedded_offset_raw == "500"


def test_reading_changed_since_prompt_is_flagged():
    doc = make_doc()
    b = build_prompt(doc)
    set_segment_reading(doc.lines[1], doc.lines[1].segments[0].id, "から", confirm=False, source="rule")
    rep = validate_patch(doc, good_patch(b.snapshot_id), [b.roundtrip])
    st = {lr.line_id: lr.status for lr in rep.lines}
    assert st["L2"] == "stale_reading" and st["L1"] == "ok"
    new, summ = apply_patch(doc, rep)
    assert "L2" in summ["skipped"]


def test_unknown_line_and_duplicate():
    doc = make_doc()
    b = build_prompt(doc)
    p = good_patch(b.snapshot_id)
    p["lines"].append({"id": "ZZ", "text": "x", "segments": []})
    p["lines"].append(dict(p["lines"][0]))
    st = [lr.status for lr in validate_patch(doc, p, [b.roundtrip]).lines]
    assert "unknown_line" in st and "duplicate" in st


def test_a_reading_the_lyrics_write_in_katakana_and_one_in_brackets():
    """宿敵 → ライバル keeps katakana for the subtitles; 敬意(リスペクト): the brackets hold the reading."""
    doc = LyricsDoc(lines=[Line(id="L1", text="宿敵に敬意(リスペクト)")])
    prepare_doc(doc)
    b = build_prompt(doc)
    assert "hidden" in b.prompt and "ライバル" in b.prompt
    segs = [{"surface": "宿敵", "reading": "ライバル", "units": ["ラ", "イ", "バ", "ル"]}, {"surface": "に", "reading": "に"},
            {"surface": "敬意", "reading": "リスペクト"}, {"surface": "(リスペクト)", "hidden": True}]
    patch = {"format": FMT_READING_PATCH, "version": 1, "snapshot": b.snapshot_id,
             "lines": [{"id": "L1", "text": "宿敵に敬意(リスペクト)", "segments": segs}]}
    rep = validate_patch(doc, patch, [b.roundtrip])
    assert rep.ok, rep
    diff = rep.lines[0].diff
    assert [(d.katakana, d.hidden) for d in diff] == [(True, False), (False, False), (True, False), (False, True)]
    new, _ = apply_patch(doc, rep)
    s = new.lines[0].segments
    assert (s[0].reading, s[0].katakana, [u.reading for u in s[0].units]) == ("らいばる", True, ["ら", "い", "ば", "る"])
    assert not s[1].katakana and s[3].hidden and not s[3].units
    # the AI sees them so next time
    again = build_prompt(new).prompt
    assert '"reading": "ライバル"' in again and '"hidden": true' in again
    # only brackets right after a word with a reading may be hidden, and they have no reading
    for bad in ([{"surface": "宿敵に敬意", "reading": "らいばるにけいい"}, {"surface": "(リスペクト)", "hidden": True, "reading": "りすぺくと"}],
                [{"surface": "宿敵に", "reading": "らいばるに"}, {"surface": "敬意(リスペクト)", "hidden": True}]):
        patch["lines"][0]["segments"] = bad
        assert validate_patch(doc, patch, [b.roundtrip]).lines[0].status == "invalid"


def test_a_katakana_reading_typed_by_hand_stays_katakana():
    doc = LyricsDoc(lines=[Line(id="L1", text="本気でライバル")])
    prepare_doc(doc)
    ln = doc.lines[0]
    seg = next(s for s in ln.segments if s.surface == "本気")
    set_segment_reading(ln, seg.id, "マジ")
    assert (seg.reading, seg.katakana) == ("まじ", True)
    set_segment_reading(ln, seg.id, "ほんき")
    assert not seg.katakana
    kana = next(s for s in ln.segments if s.surface == "ライバル")  # a katakana word: nothing to keep
    set_segment_reading(ln, kana.id, "ライバル")
    assert not kana.katakana
