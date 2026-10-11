"""Round 5 review fixes: alignment and text processing (items A1–A12, A-L1–A-L20)."""

import json

import numpy as np
import pytest
import soundfile as sf

from kara_align import service as S
from kara_align.align import calibration as C
from kara_align.align import checks as chk
from kara_align.align.backends.fake import ScriptedBackend
from kara_align.align.ctc import ctc_align, ctc_align_reference
from kara_align.align.decoding import prepare
from kara_align.align.emission_cache import emission_cache_key
from kara_align.align.planning import plan_lrc
from kara_align.align.runner import run_alignment
from kara_align.lyrics.lrc import parse_lrc
from kara_align.lyrics.pairing import pair_track
from kara_align.lyrics.parse import is_credit_line, normalize_text, parse_lyrics_text
from kara_align.models import (AlignConfig, BackendInfo, Calibration, CheckConfig, DecodeConfig, LineTiming,
                               RetryConfig, Segment, Unit, UnitTiming)
from kara_align.project import edits
from kara_align.project.exports import export
from kara_align.reading.ai import FMT_READING_PATCH, validate_patch
from kara_align.reading.japanese import rule_segments, split_morae, to_hiragana
from kara_align.reading.prepare import _assign_surfaces, _kana_splittable, prepare_doc
from kara_align.reading.profiles import JaHepburnProfile
from tests.align.helpers import IdentityProfile, inputs, make_doc, make_emission, tokenize

SCRIPT = [("ki", 1000, 1200), ("mi", 1200, 1400), ("to", 1400, 1700)]


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("KARA_ALIGN_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(ScriptedBackend, "default_script", SCRIPT)


def _wav(path, seconds=6.0, sr=22050):
    rng = np.random.default_rng(0)
    sf.write(path, (rng.standard_normal(int(seconds * sr)) * 0.05).astype(np.float32), sr)
    return path


def _project(tmp_path, text, mode="plain", seconds=6.0, name="proj"):
    h = S.create_dir(tmp_path / name, "t", mode)
    S.update_settings(h, config={"backend": "scripted"})
    pv = S.parse_lyrics(h, text, origin="paste")
    assert pv["error"] is None, pv
    S.apply_lyrics(h, pv["preview_id"])
    S.add_audio(h, _wav(tmp_path / f"{name}.wav", seconds), "original")
    return h


def _texts(text):
    doc = parse_lyrics_text(text, mode="plain").doc
    prepare_doc(doc)
    prep = prepare(doc, JaHepburnProfile(), ScriptedBackend().tokenize)
    return prep, [[(prep.units[u].reading, prep.units[u].text) for u in prep.line_units[lid]] for lid in prep.order]


# --- A1 tildes are long vowels -----------------------------------------------------------------

@pytest.mark.parametrize("text", ["ラララ～", "ラララ~", "ラララ〜"])
def test_tilde_after_kana_is_long_vowel(text):
    prep, lines = _texts(text)
    assert lines[0][-1] == ("ー", "")  # held: timed from the ら before it, no tokens of its own
    assert not [i for i in prep.issues if i.code == "untokenizable_unit"]


def test_tilde_after_kanji_and_helpers():
    _, lines = _texts("空～\n空~きれい")
    assert lines[0][-1] == ("ー", "") and ("ー", "") in lines[1]
    assert to_hiragana("ららら～") == "らららー" and to_hiragana("~") == "ー" and to_hiragana("a~") == "a~"
    assert [m.text for m in split_morae("ラララ～")] == ["ら", "ら", "ら", "ー"]
    # English text keeps its tilde as punctuation
    assert [s.surface for s in rule_segments("Love~") if s.units] == ["Love"]


def test_ai_patch_reading_tilde_as_long_vowel_is_valid():
    doc = parse_lyrics_text("ラララ～", mode="plain").doc
    prepare_doc(doc)
    ln = doc.lines[0]
    patch = {"format": FMT_READING_PATCH, "version": 1, "lines": [{"id": ln.id, "text": ln.text, "segments": [
        {"surface": "ラララ～", "reading": "らららー", "units": ["ら", "ら", "ら", "～"]}]}]}
    rep = validate_patch(doc, patch)
    assert [lr.status for lr in rep.lines] == ["ok"], rep.lines[0].reasons


# --- A11 っ / ー take neighbours within their line only ------------------------------------------

def test_sokuon_and_long_mark_do_not_cross_lines():
    _, lines = _texts("あっ\nかわいい\nーあ")
    assert lines[0][-1] == ("っ", "")  # line-final sokuon: empty (reported as unaligned)
    assert lines[2][0] == ("ー", "")  # line-initial long mark: no vowel of the previous line
    _, lines = _texts("あっか")
    assert lines[0][1] == ("っ", "k")


# --- A-L5 / A-L6 / A-L7 / A-L20 text normalisation --------------------------------------------

def test_particle_segment_keeps_mora_surfaces():
    seg = Segment(surface="には", reading="にわ", lang="ja", units=[Unit(reading="に"), Unit(reading="わ")])
    _assign_surfaces(seg)
    assert [u.surface for u in seg.units] == ["に", "は"] and _kana_splittable(seg)
    seg = Segment(surface="ラララ～", reading="らららー", lang="ja",
                  units=[Unit(reading=r) for r in ("ら", "ら", "ら", "ー")])
    _assign_surfaces(seg)
    assert [u.surface for u in seg.units] == ["ラ", "ラ", "ラ", "～"]


def test_normalize_text_kana_and_invisibles():
    assert normalize_text("がくせい") == "がくせい"
    assert normalize_text("ｶﾞﾝﾊﾞﾚ") == "ガンバレ"
    assert normalize_text("a‎b­c d") == "abc\nd"
    doc = parse_lyrics_text("ｶﾞﾝﾊﾞﾚ\nがくせい", mode="plain").doc
    prepare_doc(doc)
    assert [u.reading for u in doc.lines[0].units()] == ["が", "ん", "ば", "れ"]
    assert [u.reading for u in doc.lines[1].units()] == ["が", "く", "せ", "い"]


# --- A-L8 / A-L18 LRC tags ----------------------------------------------------------------------

def test_offset_not_finite_or_huge_is_ignored():
    for v in ("inf", "-inf", "nan", "1e12"):
        p = parse_lrc(f"[offset:{v}]\n[00:01.00]あ")
        assert p.offset_ms == 0 and p.warnings
    r = parse_lyrics_text("[offset:inf]\n[00:01.00]あ", mode="lrc")
    assert r.doc.embedded_shift_ms == 0


def test_lrc_time_tags_edge_cases():
    p = parse_lrc("[00:75.00]あ\n[00:01.0005]い\n[00:02.1234]う")
    assert [(e.time_ms, e.text) for e in p.entries] == [(1001, "い"), (2123, "う"), (75000, "あ")]
    assert any("60" in w for w in p.warnings)
    r = parse_lyrics_text("[00:01.1234]あ\n[00:03.00]い", mode="lrc")
    assert r.doc.lines[0].imported_start_ms == 1123


def test_enhanced_lrc_end_and_word_tag_only_lines():
    r = parse_lyrics_text("[00:01.00]<00:01.00>き<00:01.30>み<00:01.60>\n<00:03.00>あ<00:03.40>い", mode="lrc")
    a, b = r.doc.lines
    assert (a.text, a.imported_start_ms, a.imported_end_ms) == ("きみ", 1000, 1600)
    assert (b.text, b.imported_start_ms, b.imported_end_ms) == ("あい", 3000, None)


# --- A-L2 / A-L20 credits and title lines -------------------------------------------------------

def test_credit_detection_is_not_greedy():
    assert not is_credit_line("君の曲：聴かせて")
    assert not is_credit_line("Mix it up: yeah")
    assert not is_credit_line("Drum: ドンドン") and is_credit_line("Drum: 山田", at_top=True)
    assert is_credit_line("曲：山田", at_top=True) and not is_credit_line("曲：山田")
    assert is_credit_line("Mixing Engineer: X") and is_credit_line("作词 : 山田") and is_credit_line("Lyricist: X")
    doc = parse_lyrics_text("词：山田\n曲：鈴木\n君と歩いた\n曲：聴かせて", mode="plain").doc
    assert [ln.kind for ln in doc.lines] == ["meta", "meta", "lyric", "lyric"]


def test_netease_title_line_at_zero_is_meta():
    doc = parse_lyrics_text("[00:00.00]夜に駆ける - YOASOBI\n[00:00.50]作词 : Ayase\n[00:05.00]沈むように",
                            mode="lrc").doc
    assert [ln.kind for ln in doc.lines] == ["meta", "meta", "lyric"]


# --- A5 / A-L20 bilingual LRC -------------------------------------------------------------------

BILINGUAL = """[00:01.00]君を見ていた
[00:01.00]我一直注视着你
[00:05.00]I love you
[00:05.00]我爱你
[00:09.00]夜空に咲く花
[00:09.00]在夜空中绽放的花
[00:13.00]永遠
[00:13.00]永远
[00:17.00]ラララ
[00:17.00]//
"""


def test_bilingual_pairs_lines_without_kana():
    doc = parse_lyrics_text(BILINGUAL, mode="lrc").doc
    got = [(ln.text, ln.translation, ln.sing) for ln in doc.lines]
    assert got == [("君を見ていた", "我一直注视着你", True), ("I love you", "我爱你", True),
                   ("夜空に咲く花", "在夜空中绽放的花", True), ("永遠", "永远", True), ("ラララ", None, True)]


def test_two_japanese_lines_at_one_time_stay_lyrics():
    lrc = BILINGUAL + "[00:21.00]あなたと\n[00:21.00]わたしと\n"
    doc = parse_lyrics_text(lrc, mode="lrc").doc
    assert [ln.text for ln in doc.lines if ln.imported_start_ms == 21000] == ["あなたと", "わたしと"]


def test_placeholder_translations_are_not_paired():
    doc = parse_lyrics_text("[00:01.00]きみと\n[00:03.00]あるいた", mode="lrc").doc
    prev = pair_track(doc, "[00:01.00]//\n[00:03.00]和你走过")
    assert [(p.line_id, p.text) for p in prev.pairs] == [(doc.lines[1].id, "和你走过")]
    assert not prev.unmatched_texts


# --- A3 / A4 planning ---------------------------------------------------------------------------

def test_untimed_lines_before_first_timed_line_are_not_squeezed():
    doc = make_doc([["ha", "na"], ["ki", "mi"], ["so", "ra"]], starts=[None, 10000, 14000])
    em = make_emission([("ha", 2000, 2500), ("na", 2500, 3000), ("ki", 10000, 10400), ("mi", 10400, 10800),
                        ("so", 14000, 14400), ("ra", 14400, 14800)], 16000)
    res = run_alignment(inputs(doc, em, mode="lrc"))
    first = res.lines[0]
    assert abs(first.start_ms - 2000) <= 40 and abs(first.end_ms - 3000) <= 40
    assert not [i for i in res.issues if i.code == "window_edge"]


def test_lines_after_audio_are_no_context():
    doc = make_doc([["ki", "mi"], ["so", "ra"], ["ha", "na", "bi", "ra", "ma", "tsu"]], starts=[1000, 3000, 12000])
    em = make_emission([("ki", 1000, 1400), ("mi", 1400, 1800), ("so", 3000, 3400), ("ra", 3400, 5600)], 6000)
    skip = {doc.lines[2].id}
    tasks = plan_lrc(doc, Calibration(), DecodeConfig(), em.frame_map, em.num_frames, 6000, exclude=skip)
    assert all(doc.lines[2].id not in t.participating_line_ids for t in tasks)
    res = run_alignment(inputs(doc, em, mode="lrc", skip_line_ids=list(skip)))
    ra = [u for u in res.units if u.reading == "ra"][0]
    assert abs(ra.end_ms - 5600) <= 40
    assert all(doc.lines[2].id not in lt.context_line_ids for lt in res.lines)


# --- A-L3 window edge at the end of the audio ---------------------------------------------------

def test_no_window_edge_when_window_ends_with_audio():
    doc = make_doc([["ki", "mi"], ["so", "ra"]], starts=[1000, 3000])
    em = make_emission([("ki", 1000, 1400), ("mi", 1400, 1800), ("so", 3000, 3400), ("ra", 3400, 3960)], 4000)
    res = run_alignment(inputs(doc, em, mode="lrc"))
    assert not [i for i in res.issues if i.code == "window_edge"]
    lt = LineTiming(line_id="x", start_ms=3000, end_ms=3990, window_ms=(1500, 4000))
    assert chk.check_lines([lt], CheckConfig())  # without the audio end it is still reported
    assert not chk.check_lines([lt], CheckConfig(), audio_end_ms=4000)


# --- A10 units without tokens --------------------------------------------------------------------

def test_untokenizable_units_do_not_trigger_retries():
    lines = [[a, b, "っ"] for a, b in (("ka", "ta"), ("ne", "ko"), ("su", "shi"), ("yo", "ru"))] + [["ki", "mi"]]
    script, starts = [], []
    for i, (a, b, _) in enumerate(lines[:-1]):
        t = 1000 + i * 2000
        script += [(a, t, t + 200), (b, t + 200, t + 400)]
        starts.append(t)
    script += [("ki", 12000, 12200), ("mi", 12200, 19000)]  # the last line has a genuinely long unit
    starts.append(12000)
    doc = make_doc(lines, starts=starts)
    em = make_emission(script, 20000)
    res = run_alignment(inputs(doc, em, mode="lrc", config=AlignConfig(retry=RetryConfig(max_total_candidates=4))))
    codes = {i.code for i in res.issues}
    assert "line_incomplete" not in codes and "low_coverage" not in codes
    assert "untokenizable_unit" in codes
    tried = [x["line_id"] for x in res.stats["retry"] if "tried" in x]
    assert tried == [doc.lines[-1].id]
    assert res.stats["unit_coverage"] == 1.0


# --- A-L16 segments without a reading -----------------------------------------------------------

def test_a_segment_without_reading_is_reported():
    doc = parse_lyrics_text("3人で", mode="plain").doc
    prepare_doc(doc)
    seg = doc.lines[0].segments[0]
    assert (seg.surface, seg.reading) == ("3", "さん")  # digits have the usual reading now
    seg.units, seg.reading = [], None  # (as when it could not be read)
    prep = prepare(doc, JaHepburnProfile(), ScriptedBackend().tokenize)
    iss = [i for i in prep.issues if i.code == "segment_no_reading"]
    assert iss and iss[0].data["surface"] == "3"


# --- A-L19 token gap does not hide a long unit ---------------------------------------------------

def test_token_gap_and_long_unit_both_reported():
    u = UnitTiming(unit_id="u", line_id="L", segment_id="s", reading="a", start_ms=0, end_ms=7000, flags=["token_gap"])
    assert {i.code for i in chk.check_units([u], CheckConfig())} == {"token_gap", "long_unit"}


# --- A8 checks after manual locks -----------------------------------------------------------------

def test_checks_see_manual_times_and_no_retry_for_locked_units():
    doc = make_doc([["ka", "i", "ta"]])
    em = make_emission([("ka", 1000, 1300), ("i", 1300, 1320), ("ta", 1320, 1700)], 3000)
    r1 = run_alignment(inputs(doc, em))
    assert any(i.code == "short_unit" for i in r1.issues)
    edits.set_manual(r1, r1.units[1].unit_id, 1300, 1320 + 100, locked=True)
    edits.set_manual(r1, r1.units[2].unit_id, 1420, 1700, locked=True)
    r2 = run_alignment(inputs(doc, em, previous=r1))
    assert not [i for i in r2.issues if i.code == "short_unit"]
    assert r2.stats["retry"] == []
    # a manual time that overlaps a neighbour is reported
    edits.set_manual(r2, r2.units[0].unit_id, 1000, 1600, locked=True)
    r3 = run_alignment(inputs(doc, em, previous=r2))
    assert any(i.code == "unit_overlap" for i in r3.issues)


# --- A7 / A9 manual locks across audio / reading changes ----------------------------------------

def test_manual_locks_not_carried_to_another_original(tmp_path):
    h = _project(tmp_path, "きみと")
    r1 = S.run_align(h)
    edits.set_manual(r1, r1.units[0].unit_id, 900, 1150, locked=True)
    h.save()
    S.add_audio(h, _wav(tmp_path / "other.wav", 9.0), "original")
    ScriptedBackend.default_script = [("ki", 4000, 4200), ("mi", 4200, 4400), ("to", 4400, 4700)]
    r2 = S.run_align(h)
    u = r2.units[0]
    assert u.manual is None and abs(u.start_ms - 4000) <= 40
    assert u.manual_history and u.manual_history[-1].start_ms == 900
    assert any(i.code == "manual_audio_changed" for i in r2.issues)


def test_manual_locks_follow_reading_change_or_are_reported(tmp_path):
    ScriptedBackend.default_script = [("kimi", 1000, 1400), ("to", 1400, 1700)]
    h = _project(tmp_path, "君と")
    r1 = S.run_align(h)
    ln = h.project.lyrics.lines[0]
    seg = ln.segments[0]
    edits.set_manual(r1, seg.units[0].id, 900, 1150, locked=True)
    h.save()
    S.set_segment_reading(h, ln.id, seg.id, "くん")  # same number of morae: mapped by position
    ScriptedBackend.default_script = [("kun", 1000, 1400), ("to", 1400, 1700)]
    r2 = S.run_align(h)
    moved = [u for u in r2.units if u.manual]
    assert [(u.reading, u.start_ms) for u in moved] == [("く", 900)]
    assert any(i.code == "manual_reading_changed" for i in r2.issues)
    S.set_segment_reading(h, ln.id, seg.id, "あなた")  # three morae: cannot be mapped
    ScriptedBackend.default_script = [("anata", 1000, 1400), ("to", 1400, 1700)]
    r3 = S.run_align(h)
    assert not [u for u in r3.units if u.manual]
    lost = [i for i in r3.issues if i.code == "manual_dropped"]
    assert lost and lost[0].data["units"][0]["start_ms"] == 900


# --- A2 re-including a line ----------------------------------------------------------------------

def test_reincluded_line_is_prepared_in_the_project(tmp_path):
    h = _project(tmp_path, "作词：山田\nきみと\nあるいた")
    meta = h.project.lyrics.lines[0]
    assert meta.kind == "meta" and not meta.units()
    S.update_line(h, meta.id, kind="lyric", sing=True)
    assert h.project.lyrics.line(meta.id).units()
    r = S.run_align(h)
    known = {u.id for ln in h.project.lyrics.lines for u in ln.units()}
    assert all(u.unit_id in known for u in r.units) and not r.stale


def test_run_align_prepares_missing_readings_in_the_project(tmp_path):
    h = _project(tmp_path, "きみと\nあるいた")
    h.project.lyrics.lines[1].segments = []
    r = S.run_align(h)
    assert h.project.lyrics.lines[1].units()
    known = {u.id for ln in h.project.lyrics.lines for u in ln.units()}
    assert all(u.unit_id in known for u in r.units) and not r.stale


# --- A6 importing other lyrics -------------------------------------------------------------------

def test_new_lyrics_reset_calibration(tmp_path):
    h = _project(tmp_path, "[00:01.00]きみと\n[00:03.00]あるいた\n[00:05.00]みち\n", "lrc", seconds=8.0)
    first, _, third = h.project.lyrics.lines
    S.calibration_op(h, "mark", line_id=first.id, marked_ms=2200)
    S.calibration_op(h, "check", line_id=third.id, marked_ms=6200)
    pv = S.parse_lyrics(h, "[00:02.20]そらに\n[00:04.00]うたう\n[00:06.20]こえ\n", origin="paste")
    msgs = S.apply_lyrics(h, pv["preview_id"])
    c = h.project.calibration
    assert (c.user_shift_ms, c.confirmed, c.reference_line_id, c.checks) == (0, False, None, [])
    assert any("偏移" in m for m in msgs)
    S.calibration_op(h, "undo")  # the old calibration can be restored
    assert h.project.calibration.user_shift_ms == 1200


def test_same_lyrics_reimported_keep_calibration(tmp_path):
    lrc = "[00:01.00]きみと\n[00:03.00]あるいた\n[00:05.00]みち\n"
    h = _project(tmp_path, lrc, "lrc", seconds=8.0)
    S.calibration_op(h, "mark", line_id=h.project.lyrics.lines[0].id, marked_ms=2200)
    pv = S.parse_lyrics(h, lrc, origin="paste")
    S.apply_lyrics(h, pv["preview_id"])
    c = h.project.calibration
    assert c.user_shift_ms == 1200 and c.confirmed and c.reference_line_id == "L0001"
    # one typo fixed: the shift stays, but has to be confirmed again
    pv = S.parse_lyrics(h, lrc.replace("みち", "みちを") + "[00:06.00]そら\n[00:07.00]うみ\n", origin="paste")
    S.apply_lyrics(h, pv["preview_id"])
    c = h.project.calibration
    assert c.user_shift_ms == 1200 and not c.confirmed and c.reference_line_id == "L0001"


# --- A12 auto calibration with stale stems ----------------------------------------------------

def test_auto_calibration_uses_original_when_stems_are_stale(tmp_path):
    from kara_align.auto_calibrate import suggest_calibration

    ScriptedBackend.default_script = [("kimito", 1000, 1600), ("aruita", 3000, 3600), ("michi", 4500, 4800),
                                      ("sora", 5000, 5400)]
    h = _project(tmp_path, "[00:01.00]きみと\n[00:03.00]あるいた\n[00:04.50]みち\n[00:05.00]そら\n", "lrc")
    S.add_audio(h, _wav(tmp_path / "voc.wav"), "vocals")
    S.add_audio(h, _wav(tmp_path / "inst.wav"), "instrumental")
    S.add_audio(h, _wav(tmp_path / "song2.wav", 7.0), "original")
    assert not S.stems_current(h.project)
    out = suggest_calibration(h)
    assert out["audio_role"] == "original" and out["vocal_onset_ms"] is None and out["shift_ms"] == 0


# --- A-L1 check residuals ------------------------------------------------------------------------

def test_check_residuals_follow_every_shift_change(tmp_path):
    doc = parse_lyrics_text("[00:10.00]あ\n[01:00.00]い", mode="lrc").doc
    a, b = doc.lines
    cal = C.mark_first_onset(Calibration(), doc, a.id, 11000)
    cal, iss = C.add_check(cal, doc, b.id, 61000)
    assert not iss and cal.checks[0].residual_ms == 0
    z = C.confirm_zero(cal, doc)
    assert z.checks[0].residual_ms == 1000 and C.check_issues(z)
    u = C.undo(C.set_user_shift(cal, 3000, doc), doc)
    assert u.user_shift_ms == 1000 and u.checks[0].residual_ms == 0 and not C.check_issues(u)
    # check_issues with the document recomputes (e.g. after a line anchor was set)
    assert C.check_issues(C.set_user_shift(cal, 3000), doc=doc)
    # adding a check is undoable
    assert C.undo(cal, doc).checks == []


# --- A-L17 end marks behind untimed lines ---------------------------------------------------------

def test_end_mark_after_untimed_line_belongs_to_it():
    doc = parse_lyrics_text("[00:01.00]きみと\nあるいた\n[00:05.00]\n[00:20.00]みち", mode="lrc").doc
    ends = C.effective_line_ends(doc, Calibration(user_shift_ms=100))
    assert ends == {doc.lines[1].id: 5100}


# --- A-L4 adopting lines updates the issues -------------------------------------------------------

def test_adopt_lines_rechecks_issues(tmp_path):
    ScriptedBackend.default_script = [("ki", 1000, 1100), ("mi", 1100, 1400), ("to", 1400, 1700)]
    h = _project(tmp_path, "きみと")
    S.update_settings(h, config={"checks": {"min_unit_ms": 150}, "retry": {"enabled": False}})
    r1 = S.run_align(h)
    assert any(i.code == "short_unit" for i in r1.issues)
    ScriptedBackend.default_script = [("ki", 1000, 1300), ("mi", 1300, 1500), ("to", 1500, 1700)]
    lid = h.project.lyrics.lines[0].id
    # a rerun on other scores (the scripted backend's emissions are cached by script)
    part = S.run_align(h, line_ids=[lid])
    assert not any(i.code == "short_unit" for i in part.issues)
    target = S.adopt_lines(h, r1.id, [lid], from_result_id=part.id)
    assert not any(i.code == "short_unit" for i in target.issues)
    assert not any("short_unit" in u.flags for u in target.units)


# --- A-L12 ai_validate keeps the prompt's hashes -------------------------------------------------

def test_ai_validate_keeps_line_hashes(tmp_path):
    h = _project(tmp_path, "君と歩いた\n夜空")
    out = S.ai_prompt(h)
    ln = h.project.lyrics.lines[0]
    reply = {"format": "kara-align/reading-patch", "version": 1, "snapshot": out["snapshot_id"], "lines": [
        {"id": ln.id, "text": ln.text, "segments": [{"surface": "君", "reading": "きみ"},
                                                  {"surface": "と", "reading": "と"},
                                                  {"surface": "歩いた", "reading": "あるいた"}]}]}
    S.ai_validate(h, json.dumps(reply, ensure_ascii=False))
    rt = h.project.ai_roundtrips[-1]
    assert "line_hashes" in rt.report and "validation" in rt.report
    seg = h.project.lyrics.lines[0].segments[-1]
    seg.reading = "あゆいた"
    seg.units[1].reading = "ゆ"
    r2 = S.ai_validate(h, json.dumps(reply, ensure_ascii=False))
    assert [x["status"] for x in r2["report"]["lines"]] == ["stale_reading"]


# --- A-L13 exports -------------------------------------------------------------------------------

def test_exports_warn_about_stale_results_and_keep_lrc_lines(tmp_path):
    lrc = "[00:01.00]きみと\n[00:02.50]\n[00:03.00]Mix it up: yeah\n[00:04.00]あるいた\n"
    h = _project(tmp_path, lrc, "lrc")
    S.update_line(h, h.project.lyrics.lines[3].id, sing=False)
    S.run_align(h)
    S.update_line(h, h.project.lyrics.lines[0].id, text="きみが")
    for fmt in ("csv", "lrc-line", "lrc-unit", "alignment"):
        assert any("过期" in w for w in S.export(h, fmt).warnings), fmt
    cal = export(h.project, "lrc-calibrated").content
    assert "[00:02.50]\n" in cal and "[00:04.00]あるいた" in cal


def test_export_reports_lines_after_audio(tmp_path):
    h = _project(tmp_path, "[00:01.00]きみと\n[00:30.00]あるいた\n", "lrc")
    S.run_align(h)
    out = S.export(h, "lrc-line")
    assert any("音频结束之后" in w for w in out.warnings)


# --- A-L15 staleness covers more inputs -----------------------------------------------------------

def test_staleness_covers_voice_flags_and_anchor_tolerance(tmp_path):
    h = _project(tmp_path, "[00:01.00]きみと\n[00:03.00]あるいた\n", "lrc")
    r = S.run_align(h)
    assert S.staleness(h.project, r) is None
    h.project.lyrics.lines[1].voice = "b"
    assert S.staleness(h.project, r)
    h.project.lyrics.lines[1].voice = "main"
    S.set_line_anchor(h, h.project.lyrics.lines[0].id, 1000, hard=True, tolerance_ms=80)
    r = S.run_align(h)
    h.project.lyrics.lines[0].anchor.tolerance_ms = 300
    assert S.staleness(h.project, r)
    h.project.lyrics.lines[0].anchor.tolerance_ms = 80
    h.project.lyrics.lines[0].segments[0].units[0].flags = ["long"]
    assert S.staleness(h.project, r)


# --- A-L14 other tracks' origin -------------------------------------------------------------------

def test_retry_on_other_track_uses_its_own_origin():
    doc = make_doc([["ki", "mi"], ["so", "ra"], ["ha", "na"]], starts=[1000, 4000, 7000])
    script = [("ki", 1000, 1400), ("mi", 1400, 1800), ("so", 4000, 4400), ("ra", 4400, 4800),
              ("ha", 7000, 7400), ("na", 7400, 7800)]
    orig = make_emission([x for x in script if x[0] not in ("so", "ra")], 10000)
    # the vocal stem starts 3 s into the song: its frame 0 is at 3000 ms
    voc = make_emission([(t, s - 3000, e - 3000) for t, s, e in script if s >= 3000], 7000, origin_samples=48000)
    cfg = AlignConfig()
    cfg.decode.soft_lambda = 0.0001
    cfg.decode.joint_context_lines = 0  # window of line 2 starts at 2.5 s, before the stem
    cfg.checks.min_unit_ms = 100  # the garbage on the original gives short units: retried
    res = run_alignment(inputs(doc, orig, mode="lrc", config=cfg, emissions={"original": orig, "vocals": voc}))
    lid = doc.lines[1].id
    tried = [x for x in res.stats["retry"] if x.get("line_id") == lid]
    assert tried and tried[0]["chosen"] == "audio:vocals", tried
    got = {u.reading: (u.start_ms, u.end_ms) for u in res.units if u.line_id == lid}
    assert abs(got["so"][0] - 4000) <= 40 and abs(got["ra"][1] - 4800) <= 40, got


# --- A-L11 Viterbi memory / local plain rerun -----------------------------------------------------

def test_ctc_priors_still_match_reference():
    from kara_align.align.ctc import FramePriors

    rng = np.random.default_rng(1)
    logp = np.log(rng.dirichlet(np.ones(6), size=40))
    targets = [1, 2, 2, 3]
    pri = FramePriors(token=-rng.random(40), gap=-rng.random(40),
                      gap_states=np.array([False, False, True, False, True, False, False, False, False]))
    a, b = ctc_align(logp, targets, 0, priors=pri), ctc_align_reference(logp, targets, 0, priors=pri)
    assert [(s.start_frame, s.end_frame) for s in a.spans] == [(s.start_frame, s.end_frame) for s in b.spans]
    assert a.total_score == pytest.approx(b.total_score)


def test_plain_local_rerun_decodes_only_the_neighbourhood():
    doc = make_doc([["ki", "mi"], ["so", "ra"], ["ha", "na"]])
    script = [("ki", 1000, 1400), ("mi", 1400, 1800), ("so", 20000, 20400), ("ra", 20400, 20800),
              ("ha", 40000, 40400), ("na", 40400, 40800)]
    em = make_emission(script, 60000)
    full = run_alignment(inputs(doc, em))
    lid = doc.lines[1].id
    part = run_alignment(inputs(doc, em, previous=full, line_ids=[lid]))
    lt = part.lines[0]
    assert abs(lt.start_ms - 20000) <= 40
    assert lt.context_line_ids == [doc.lines[0].id, doc.lines[2].id]


# --- A-L19 small ones ---------------------------------------------------------------------------

def test_lock_needs_times_and_clear_restores_status():
    doc = make_doc([["ki", "mi"]])
    doc.lines[0].segments[1].units[0].reading = "ル"  # no tokens
    doc.lines[0].segments[1].surface = "ル"  # (a Latin surface would be aligned on its letters, as English)
    res = run_alignment(inputs(doc, make_emission([("ki", 1000, 1400)], 3000)))
    bad = next(u for u in res.units if u.reading == "ル")
    with pytest.raises(edits.EditError):
        edits.set_lock(res, bad.unit_id, True)
    edits.set_manual(res, bad.unit_id, 1400, 1600)
    assert bad.status == "ok"
    edits.clear_manual(res, bad.unit_id)
    assert bad.status == "unaligned" and bad.reason and bad.start_ms is None


def test_reading_candidates_are_not_decoded_twice():
    from kara_align.align.retry import reading_overrides

    doc = make_doc([["ki", "mi"]])
    seg = doc.lines[0].segments[0]
    seg.candidates = ["ki", "ka", "ka"]  # the current reading and a duplicate
    seg.lang = "ja"
    prep = prepare(doc, IdentityProfile(), tokenize)
    got = reading_overrides(prep, doc.lines[0].id, IdentityProfile(), tokenize, max_n=4)
    assert [label for label, _, _ in got] == ["reading:ki=ka"]


def test_emission_key_changes_with_local_model_files(tmp_path):
    d = tmp_path / "model"
    d.mkdir()
    (d / "weights.bin").write_bytes(b"a")
    info = BackendInfo(name="wav2vec2-ctc", model_id=str(d), profile="ja-hepburn", sample_rate=16000)
    k1 = emission_cache_key("0" * 64, "original", 0, info, 20.0, 3.0, "x")
    (d / "weights.bin").write_bytes(b"abc")
    assert emission_cache_key("0" * 64, "original", 0, info, 20.0, 3.0, "x") != k1


# --- long vowels are held, not spelled twice ----------------------------------------------------

def test_long_vowel_is_timed_from_the_unit_it_lengthens():
    doc = make_doc([["ら", "ー", "め", "ん"], ["そ", "ら", "ー"]], starts=[1000, 3000])
    em = make_emission([("ra", 1000, 1100), ("me", 1600, 1700), ("n", 1700, 1800),
                        ("so", 3000, 3100), ("ra", 3100, 3200)], 5000)
    inp = inputs(doc, em)
    inp.profile = JaHepburnProfile()
    res = run_alignment(inp)
    assert not [i for i in res.issues if i.code == "untokenizable_unit"]
    assert len(res.units) == 7
    ra, hold, me = res.units[0], res.units[1], res.units[2]
    assert hold.status == "ok" and "held" in hold.flags
    # ら and its ー share the time up to め, one equal piece each
    assert abs(ra.start_ms - 1000) <= 40 and ra.end_ms == hold.start_ms and hold.end_ms == me.start_ms
    assert abs((ra.end_ms - ra.start_ms) - (hold.end_ms - hold.start_ms)) <= 1
    # line-final ー: held a little past the model's end of ら (the tail step may extend it)
    ra2, hold2 = res.units[5], res.units[6]
    assert hold2.status == "ok" and hold2.start_ms == ra2.end_ms and hold2.end_ms > hold2.start_ms


def test_apply_holds_caps_the_hold_before_a_rest_and_needs_a_unit_before_it():
    from kara_align.align.decoding import HOLD_MAX_MS, apply_holds
    from kara_align.models import UnitTiming

    def ut(r, s=None, e=None):
        return UnitTiming(unit_id=r + str(s), line_id="L", segment_id="s", reading=r, start_ms=s, end_ms=e,
                          status="ok" if s is not None else "unaligned")

    uts = [ut("そ", 0, 100), ut("ー"), ut("ー"), ut("ら", 5000, 5100)]
    apply_holds(uts)
    assert [(u.start_ms, u.end_ms) for u in uts[:3]] == [(0, 300), (300, 600), (600, 100 + HOLD_MAX_MS)]
    first = [ut("ー"), ut("あ", 0, 100)]
    apply_holds(first)
    assert first[0].start_ms is None and first[0].status == "unaligned"


# --- spelled-out letters: one unit each, with the letter's name --------------------------------

def test_spelled_letters_are_one_unit_each():
    # (in Japanese lyrics; an English song keeps English readings)
    prep, lines = _texts("きみと\nR O M A N T I C now")
    lines = lines[1:]
    letters = [(r, t) for r, t in lines[0] if r != "now"]
    assert letters == [("あーる", "aru"), ("おー", "o"), ("えむ", "emu"), ("えー", "e"), ("えぬ", "enu"),
                       ("てぃー", "ti"), ("あい", "ai"), ("しー", "shi")]
    assert ("now", "now") in lines[0]
    _, lines = _texts("きみ W X")
    assert lines[0][-2:] == [("だぶりゅー", "daburyu"), ("えっくす", "ekkusu")]
    # a word, or a lowercase letter (the article a), is not spelled out
    segs = rule_segments("LOVE a")
    assert [(s.surface, s.lang) for s in segs if s.units] == [("LOVE", "en"), ("a", "en")]


def test_ai_reply_splitting_a_letter_into_morae_is_merged():
    doc = parse_lyrics_text("きみと\nR O", mode="plain").doc
    prepare_doc(doc)
    ln = doc.lines[1]
    patch = {"format": FMT_READING_PATCH, "version": 1, "lines": [{"id": ln.id, "text": ln.text, "segments": [
        {"surface": "R", "reading": "あーる", "units": ["あ", "ー", "る"]}, {"surface": " ", "reading": ""},
        {"surface": "O", "reading": "おー", "units": ["お", "ー"]}]}]}
    rep = validate_patch(doc, patch)
    assert [lr.status for lr in rep.lines] == ["ok"], rep.lines[0].reasons
    assert [d.new_units for d in rep.lines[0].diff if d.new_units] == [["あーる"], ["おー"]]


def test_ai_reply_can_suggest_where_a_long_line_wraps():
    from kara_align.reading.ai import apply_patch

    doc = parse_lyrics_text("きみと\nそらを みてた", mode="plain").doc
    prepare_doc(doc)
    ln = doc.lines[1]
    patch = {"format": FMT_READING_PATCH, "version": 1, "lines": [{"id": ln.id, "text": ln.text, "segments": [
        {"surface": "そらを", "reading": "そらを"}, {"surface": " ", "reading": ""},
        {"surface": "みてた", "reading": "みてた", "wrap": True}]}]}
    rep = validate_patch(doc, patch)
    assert [lr.status for lr in rep.lines] == ["ok"], rep.lines[0].reasons
    new, _ = apply_patch(doc, rep)
    assert [s.wrap_before for s in new.lines[1].segments] == [False, False, True]
    # display only: the same readings, and the hint is in no revision (no alignment goes out of date)
    new.lines[1].segments[2].wrap_before = False
    assert new.reading_revision() == new.model_copy(deep=True).reading_revision()
    assert [s.reading for s in new.lines[1].segments] == ["そらを", None, "みてた"]
