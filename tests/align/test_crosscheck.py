"""Hints on full runs: the LRC-mode cross check (a decode without the lyrics' times) and singing no line
is placed over (vocal stem)."""

import numpy as np

from kara_align.align import checks as chk
from kara_align.align.activity import detect_activity
from kara_align.align.runner import PLAIN_CHECK_LABEL, run_alignment
from kara_align.interfaces import Emission
from kara_align.models import AlignConfig, LineAnchor, LineTiming, LyricsDoc

from .helpers import VOCAB, inputs, make_doc, make_emission


def _song(sung, lrc_starts, decoys=(), duration_ms=16000, voices=None):
    """Units sung at ``sung`` [(unit, start ms)]; ``decoys`` [(unit, start ms)]: a weaker sound like the
    unit there (the same words sung again, an echo) – where a wrong LRC time can pull a line."""
    em = make_emission([(u, s, s + 400) for u, s in sung], duration_ms)
    lp = em.logp.copy()
    for u, s in decoys:
        f = s // 20
        lp[f:f + 10, :] = np.log(0.02 / (len(VOCAB) - 1))
        lp[f:f + 10, VOCAB.index(u)] = np.log(0.45)
        lp[f:f + 10, 0] = np.log(0.5)
    em = Emission(lp, em.frame_map, 0)
    doc = make_doc([[u] for u, _ in sung], starts=lrc_starts)
    for ln, v in zip(doc.lines, voices or []):
        ln.voice = v
    return doc, em


SUNG = [("a", 1000), ("b", 4000), ("c", 7000), ("d", 10000), ("e", 13000)]
WRONG = [1000, 4000, 9000, 10000, 13000]  # c written 2 s late, where it sounds again more softly
# the LRC-mode safeguards that would find c anyway (a joint decode with its neighbours, retries) off:
# its window starts after where it is sung, the LRC time wins
STRICT = AlignConfig.model_validate({"decode": {"joint_context_lines": 0}, "retry": {"enabled": False}})


def _codes(res, code):
    return [i for i in res.issues if i.code == code]


def test_a_line_the_lrc_times_wrongly_is_reported_with_the_other_placement():
    doc, em = _song(SUNG, WRONG, decoys=[("c", 9000)])
    res = run_alignment(inputs(doc, em, mode="lrc", config=STRICT))
    c = doc.lines[2].id
    assert abs(next(lt for lt in res.lines if lt.line_id == c).start_ms - 9000) <= 40  # the LRC way: the echo
    found = _codes(res, "mode_disagree")
    assert [i.line_id for i in found] == [c] and found[0].data["diff_ms"] <= -1000
    cand = [x for x in res.candidates if x.label == PLAIN_CHECK_LABEL]
    assert [x.line_id for x in cand] == [c] and abs(cand[0].units[0].start_ms - 7000) <= 40
    # a line someone anchored is theirs: not checked
    doc.lines[2].anchor = LineAnchor(abs_ms=9000, hard=True, tolerance_ms=50)
    assert not _codes(run_alignment(inputs(doc, em, mode="lrc", config=STRICT)), "mode_disagree")
    # plain mode, a rerun of some lines, the check turned off: no second decode
    assert not _codes(run_alignment(inputs(doc, em, mode="plain")), "mode_disagree")
    doc.lines[2].anchor = None
    assert not _codes(run_alignment(inputs(doc, em, mode="lrc", config=STRICT, line_ids=[c])), "mode_disagree")
    off = STRICT.model_copy(deep=True)
    off.checks.cross_check_ms = 0
    assert not _codes(run_alignment(inputs(doc, em, mode="lrc", config=off)), "mode_disagree")


def test_only_the_main_voice_is_checked():
    doc, em = _song(SUNG, WRONG, decoys=[("c", 9000)], voices=["main", "main", "backing", "main", "main"])
    assert not _codes(run_alignment(inputs(doc, em, mode="lrc", config=STRICT)), "mode_disagree")


def test_many_lines_disagreeing_is_one_note():
    # every line timed at a softer repeat 2 s after it (lyrics of another version): one note, no hint each
    sung = [("abcde"[k % 5], 1000 + 4000 * k) for k in range(10)]
    doc, em = _song(sung, [s + 2000 for _, s in sung], decoys=[(u, s + 2000) for u, s in sung], duration_ms=42000)
    res = run_alignment(inputs(doc, em, mode="lrc", config=STRICT))
    assert not _codes(res, "mode_disagree") and not [x for x in res.candidates if x.label == PLAIN_CHECK_LABEL]
    (note,) = _codes(res, "mode_disagree_many")
    assert note.severity == "info" and note.line_id is None


# ---------------------------------------------------------------- singing no line is over


def _env(singing, total_ms):
    env = np.full(total_ms // 10, -70.0)
    for a, b in singing:
        env[a // 10:b // 10] = -15.0
    return env


def test_uncovered_singing_on_full_runs_only():
    sung = [("a", 1000), ("b", 4000), ("c", 10000)]
    doc, em = _song(sung, None)
    env = _env([(s, s + 400) for _, s in sung] + [(5500, 8500)], 13000)  # 3 s between b and c, no line there

    def run(**kw):
        return run_alignment(inputs(doc, em, emissions={"original": em, "vocals": em},
                                    energy_for=lambda r: (env, 10.0), **kw))

    hints = _codes(run(), "uncovered_singing")
    assert [i.line_id for i in hints] == [doc.lines[2].id] and hints[0].severity == "warning"
    # a rerun of one line knows only that line: nothing about the rest of the song
    assert not _codes(run(line_ids=[doc.lines[2].id]), "uncovered_singing")


def _lt(lid, a=None, b=None):
    return LineTiming(line_id=lid, start_ms=a, end_ms=b)


def test_uncovered_before_and_after_the_last_line_apart_each_range_its_own():
    act = detect_activity(_env([(1000, 4000), (8000, 11000), (14000, 17000), (20000, 23000), (40000, 44000)],
                               50000), 10.0)
    found = chk.check_uncovered([_lt("L1", 1000, 4000), _lt("L2", 20000, 23000)], act)
    before = [i for i in found if i.severity == "warning"]
    after = [i for i in found if i.severity == "info"]
    # two runs before L2, each with its own range; the singing after the last line apart, only a note
    assert [i.line_id for i in before] == ["L2"] and len(before[0].data["spans_ms"]) == 2
    assert "这一句之前" in before[0].message and "、" in before[0].message
    assert [i.line_id for i in after] == ["L2"] and "最后一句之后" in after[0].message
    # next to a line without times: its own failure is reported, not this
    act = detect_activity(_env([(1000, 4000), (8000, 11000), (20000, 23000)], 30000), 10.0)
    assert not chk.check_uncovered([_lt("L1", 1000, 4000), _lt("Lx"), _lt("L2", 20000, 23000)], act)


def test_a_hint_goes_once_a_line_is_placed_over_its_singing():
    act = detect_activity(_env([(1000, 4000), (8000, 11000), (20000, 23000)], 30000), 10.0)
    (hint,) = chk.check_uncovered([_lt("L1", 1000, 4000), _lt("L2", 20000, 23000)], act)
    assert not chk.uncovered_now_covered(hint, [_lt("L1", 1000, 4000), _lt("L2", 20000, 23000)])
    assert chk.uncovered_now_covered(hint, [_lt("L1", 8000, 11000), _lt("L2", 20000, 23000)])


def test_times_in_the_hints_round_into_the_next_minute():
    assert chk._fmt(59960) == "1:00.0" and chk._fmt(119980) == "2:00.0" and chk._fmt(61234) == "1:01.2"


def test_plain_mode_lyrics_are_never_cross_checked():
    doc = LyricsDoc(language="en", lines=make_doc([["a"]]).lines)
    em = make_emission([("a", 1000, 1400)], 3000)
    assert not _codes(run_alignment(inputs(doc, em)), "mode_disagree")
