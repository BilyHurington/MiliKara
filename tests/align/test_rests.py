"""Lines must not stretch across interludes: vocal activity, in-line pause
priors, LRC end markers, rest / gap checks and the retry residual tie."""

import numpy as np
import pytest

from kara_align.align import checks as chk
from kara_align.align.activity import detect_activity
from kara_align.align.calibration import effective_line_ends
from kara_align.align.ctc import FramePriors, ctc_align, ctc_align_reference
from kara_align.align.decoding import frame_priors
from kara_align.align.planning import plan_lrc
from kara_align.align.retry import Scored
from kara_align.align.runner import run_alignment
from kara_align.interfaces import Emission
from kara_align.models import AlignConfig, Calibration, CheckConfig, DecodeConfig, Line, UnitTiming
from kara_align.timebase import FrameMap

from .helpers import VOCAB, inputs, make_doc, make_emission


def rand_logp(T, V, seed):
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(T, V))
    return x - np.log(np.exp(x).sum(axis=1, keepdims=True))


# ---------------------------------------------------------------- CTC priors


@pytest.mark.parametrize("seed", range(8))
def test_priors_vectorised_matches_reference(seed):
    rng = np.random.default_rng(seed)
    T, V = 30, 5
    targets = [int(x) for x in rng.integers(1, V, size=4)]
    logp = rand_logp(T, V, seed)
    lines = ["a", "a", "b", "b"]
    pri = frame_priors(lines, T, 20.0, DecodeConfig(line_gap_cost=40.0), rng.uniform(0, 1, T))
    a = ctc_align(logp, targets, 0, priors=pri)
    b = ctc_align_reference(logp, targets, 0, priors=pri)
    assert list(a.states) == list(b.states)
    assert a.total_score == pytest.approx(b.total_score)
    assert a.total_score == pytest.approx(a.acoustic_score + a.prior_cost)
    assert a.prior_cost <= 0


def test_gap_states_are_only_blanks_inside_a_line():
    pri = frame_priors(["a", "a", "b"], 10, 20.0, DecodeConfig(), None)
    #                         b  a  b  a  b  b  b
    assert list(pri.gap_states) == [False, False, True, False, False, False, False]
    assert pri.token is None  # no vocal stem: no rest cost on tokens
    with pytest.raises(ValueError):
        ctc_align(rand_logp(10, 4, 0), [1, 2, 3], 0,
                  priors=FramePriors(gap=np.full(10, -1.0), gap_states=np.array([0, 1, 0, 0, 0, 0, 0], bool)))


def _stray_logp():
    """Line A = tokens 1,2; line B = token 3.  Token 2 has weak evidence right
    after token 1 and slightly better evidence far away, in an interlude."""
    T, V = 200, 4
    lp = np.full((T, V), -8.0)
    lp[:, 0] = -0.01
    lp[10:16, :] = -8.0
    lp[10:16, 1] = -0.01
    lp[16:19, 0] = -1.0
    lp[16:19, 2] = -3.8
    lp[150, 2] = -2.5
    lp[180:186, :] = -8.0
    lp[180:186, 3] = -0.01
    return lp


def test_stray_token_stays_in_its_line_with_gap_prior():
    lp = _stray_logp()
    free = ctc_align(lp, [1, 2, 3], 0)
    assert free.spans[1].start_frame == 150  # without priors it wanders into the interlude
    pri = frame_priors(["A", "A", "B"], 200, 20.0, DecodeConfig(), None)
    kept = ctc_align(lp, [1, 2, 3], 0, priors=pri)
    assert 16 <= kept.spans[1].start_frame < 19
    # the pause between lines stays free: line B keeps its place
    assert kept.spans[2].start_frame == 180


def test_rest_cost_alone_also_keeps_it_out_of_silence():
    lp = _stray_logp()
    rest = np.zeros(200)
    rest[40:175] = 1.0  # the vocal stem is silent in the interlude
    pri = frame_priors(["A", "A", "B"], 200, 20.0, DecodeConfig(line_gap_cost=0.0, rest_gap_cost=0.0), rest)
    kept = ctc_align(lp, [1, 2, 3], 0, priors=pri)
    assert 16 <= kept.spans[1].start_frame < 19


# ---------------------------------------------------------------- activity


def test_activity_is_relative_to_the_song_and_soft():
    env = np.full(3000, -22.0)  # 10 ms hop: singing
    env[1000:2000] = -45.0  # interlude with instrument bleed
    env[2000:2200] = -35.0  # soft passage: in between
    act = detect_activity(env, 10.0)
    assert act.reference_db == pytest.approx(-22.0, abs=0.5)
    assert act.rest_fraction(0, 9000) == pytest.approx(0.0, abs=0.02)
    assert act.rest_fraction(11000, 19000) == pytest.approx(1.0)
    assert 0.3 < act.rest_fraction(20500, 21500) < 0.6
    assert act.is_rest(12000, 12500) and not act.is_rest(20500, 21500)
    # the same stem 20 dB quieter gives the same answer
    act2 = detect_activity(env - 20, 10.0)
    assert act2.rest_fraction(11000, 19000) == pytest.approx(1.0)
    # emission frames (20 ms) average the envelope hops they cover
    fr = act.for_frames(FrameMap(16000, 320), 0, 1500)
    assert fr[:490].max() < 0.05 and fr[510:990].min() > 0.95
    assert detect_activity(np.full(100, -120.0), 10.0) is None


# ---------------------------------------------------------------- LRC end markers


def _doc_with_blank():
    doc = make_doc([["a"], ["b"], ["c"]], starts=[2000, 4000, 30000])
    blank = Line(text="", kind="blank", sing=False, imported_start_ms=6000)
    doc.lines.insert(2, blank)
    return doc


def test_timed_blank_line_marks_the_previous_line_end():
    doc = _doc_with_blank()
    a, b, _, c = doc.lines
    assert effective_line_ends(doc, Calibration()) == {b.id: 6000}
    # moves with the global shift, like the start
    assert effective_line_ends(doc, Calibration(user_shift_ms=-300)) == {b.id: 5700}
    # an explicit end wins
    a.imported_end_ms = 3500
    assert effective_line_ends(doc, Calibration())[a.id] == 3500


def test_end_marker_limits_window_and_forward_context():
    fm = FrameMap(16000, 320)
    doc = _doc_with_blank()
    a, b, _, c = doc.lines
    cfg = DecodeConfig(joint_context_lines=1, left_margin_ms=500, right_margin_ms=500, end_marker_margin_ms=3000)
    tasks = plan_lrc(doc, Calibration(), cfg, fm, 5000, 60000)
    tb = next(t for t in tasks if b.id in t.retained_line_ids)
    # gap to `a` unknown -> context before; the marked pause after -> none after
    assert tb.participating_line_ids == [a.id, b.id]
    # window stops 3 s after the marker instead of at the next anchor (30 s)
    assert tb.end_frame == int(fm.ms_to_frame(6000 + 3000))


# ---------------------------------------------------------------- checks


def _u(uid, line, s, e, reading="x"):
    return UnitTiming(unit_id=uid, line_id=line, segment_id="s", reading=reading, start_ms=s, end_ms=e)


def test_line_gap_and_rest_checks():
    units = [_u("1", "L1", 1000, 1300), _u("2", "L1", 1300, 1600), _u("3", "L1", 9000, 9100, "い"),
             _u("4", "L2", 20000, 20300)]
    iss = chk.check_line_gaps(units, CheckConfig(max_line_gap_ms=4000))
    assert [(i.code, i.unit_id) for i in iss] == [("line_gap", "3")]
    env = np.full(3000, -22.0)
    env[200:1900] = -60.0
    iss = chk.check_rest(units, detect_activity(env, 10.0))
    assert [(i.code, i.unit_id) for i in iss] == [("unit_in_rest", "3")]
    assert "line_gap" in chk.RETRYABLE and "unit_in_rest" in chk.RETRYABLE
    assert chk.check_rest(units, None) == []


# ---------------------------------------------------------------- end to end


def test_runner_keeps_line_out_of_interlude():
    # line 1 "ab" sung 1000-1800 but "b" is weak there; a spurious "b" in the
    # interlude at 6000; line 2 "c" at 9000.  The vocal stem is silent 2-8.8 s.
    em = make_emission([("a", 1000, 1400), ("c", 9000, 9400)], 10000)
    lp = em.logp.copy()
    b = VOCAB.index("b")
    lp[70:90, b] = np.log(0.3)
    lp[70:90, 0] = np.log(0.6)
    lp[300, b] = np.log(0.5)  # a vocal-like instrument note
    lp[300, 0] = np.log(0.45)
    em = Emission(lp, em.frame_map, 0)
    doc = make_doc([["a", "b"], ["c"]])
    env = np.full(1000, -22.0)  # 10 ms hop
    env[200:880] = -60.0

    def run(config):
        return run_alignment(inputs(doc, em, config=config, emissions={"original": em, "vocals": em},
                                    energy_for=lambda r: (env, 10.0)))

    off = AlignConfig()
    off.decode.line_gap_cost = off.decode.rest_gap_cost = off.decode.rest_token_cost = 0.0
    before = {u.reading: u for u in run(off).units}
    assert before["b"].start_ms >= 6000  # the failure this guards against
    res = run(AlignConfig())
    after = {u.reading: u for u in res.units}
    assert 1400 <= after["b"].start_ms < 1800
    assert after["c"].start_ms == pytest.approx(9000, abs=40)
    assert not [i for i in res.issues if i.code in ("line_gap", "unit_in_rest")]


# ---------------------------------------------------------------- retry


class _O:
    def __init__(self, res, role, acoustic=-1.0):
        self._res, self.role, self.feasible, self.mean_acoustic = res, role, True, acoustic
        self.task = type("T", (), {"start_frame": 0, "end_frame": 10, "participating_line_ids": ["L"]})()

    def residual(self, line_id):
        return self._res


def test_retry_ignores_residual_differences_within_anchor_noise():
    base = Scored(_O(240, "vocals"), [], 1.0)
    other = Scored(_O(200, "original"), [], 1.0)
    assert not other.better_than(base, "L")  # 40 ms closer to a soft anchor is not evidence
    far = Scored(_O(40, "original"), [], 1.0)
    assert far.better_than(base, "L")
    warned = Scored(_O(0, "original"), [chk.Issue(code="short_unit", severity="warning", message="")], 1.0)
    assert not warned.better_than(base, "L")


# ---------------------------------------------------------------- singing no line is over


def _activity(singing: list[tuple[int, int]], total_ms: int = 60000):
    """A vocal stem singing over these ms ranges, silent elsewhere."""
    env = np.full(total_ms // 10, -70.0)
    for a, b in singing:
        env[a // 10:b // 10] = -15.0
    return detect_activity(env, 10.0)


def _lt(lid, a, b):
    from kara_align.models import LineTiming
    return LineTiming(line_id=lid, start_ms=a, end_ms=b)


def test_singing_no_line_is_over_is_reported_on_the_line_after_it():
    act = _activity([(1000, 4000), (8000, 12000), (12300, 14000), (20000, 23000), (30000, 31000), (40000, 44000)])
    lines = [_lt("L1", 1000, 4000), _lt("L2", 20000, 23000), _lt("L3", 30000, 31000)]
    found = chk.check_uncovered(lines, act)
    # 8–14 s (a 0.3 s breath inside) before L2; 40–44 s after the last line, on it; L1 / L3 sung where they are
    assert [i.line_id for i in found] == ["L2", "L3"]
    for i, (a, b) in zip(found, ((8000, 14000), (40000, 44000))):  # (the activity is smoothed over 0.3 s)
        assert abs(i.data["from_ms"] - a) <= 300 and abs(i.data["to_ms"] - b) <= 300
    assert "这一句之前" in found[0].message and "这一句之后" in found[1].message
    # shorter than 2.5 s (a shout, a breath) or just past a line's end (its held note): nothing
    assert not chk.check_uncovered([_lt("L1", 1000, 4000)], _activity([(1000, 4300), (9000, 10500)]))
    # no vocal stem: nothing to go by
    assert chk.check_uncovered(lines, None) == []
