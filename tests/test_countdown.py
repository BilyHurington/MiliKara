"""Countdown dots before a line: which lines, when each dot goes, the per-line setting."""

import re

import pytest

from kara_align import service as S
from kara_align.karaoke import ass as A
from kara_align.models import KaraokeStyle, Line

from .test_karaoke import _home, _project  # noqa: F401  (the fixture is used by name)


def _laid(*spans):
    return [A.LaidLine(Line(id=f"L{i}", text="x"), [], a, b) for i, (a, b) in enumerate(spans)]


def test_first_line_and_lines_after_a_long_pause():
    st = KaraokeStyle()
    laid = _laid((5000, 8000), (9000, 12000), (20000, 23000), (26000, 28000))
    A.plan_countdowns(laid, st)
    assert [ll.countdown_ms for ll in laid] == [3000, 0, 3000, 0]  # 8 s pause before the third
    st.countdown.intro, st.countdown.min_gap_ms, st.countdown.dots = False, 3000, 4
    A.plan_countdowns(laid, st)
    assert [ll.countdown_ms for ll in laid] == [0, 0, 4000, 4000]
    # a line's own setting wins; a wrapped line counts once (its first piece)
    laid[1].line.countdown, laid[2].line.countdown = True, False
    laid.append(A.LaidLine(laid[3].line, [], 28500, 29000))
    A.plan_countdowns(laid, st)
    assert [ll.countdown_ms for ll in laid] == [0, 4000, 0, 4000, 0]


def test_one_dot_a_second_ending_as_the_line_starts():
    assert A.countdown_dots(1000, 10000, 3) == [10000, 9000, 8000]  # left to right: the leftmost goes last
    assert A.countdown_dots(8500, 10000, 3) == [10000, 9500, 9000]  # a shorter wait: evenly
    assert A.countdown_dots(9900, 10000, 3) == []  # hardly any time


def test_dots_in_the_subtitles(tmp_path):
    h = _project(tmp_path)  # 窓に舞う桜 sung from 1.0 s, 君 from 5.0 s (after a 2 s pause)
    text, _ = S.karaoke_ass(h)
    dots = [ln for ln in text.splitlines() if ",KDots," in ln]
    assert re.search(r"^Style: KDots,", text, re.M)
    # the first line only (its pause before 君 is shorter than 6 s); the lyrics are shown 150 ms ahead,
    # so the last dot goes at 0.85 s, as the sweep starts (the three share the 0.85 s before it)
    assert len(dots) == 3
    ends = sorted(ln.split(",")[2] for ln in dots)
    assert ends[-1] == "0:00:00.85"
    h.project.karaoke.countdown.min_gap_ms = 2000
    text, _ = S.karaoke_ass(h)
    assert sum(1 for ln in text.splitlines() if ",KDots," in ln) == 6
    h.project.karaoke.countdown.intro = h.project.karaoke.countdown.interlude = False
    text, _ = S.karaoke_ass(h)
    assert ",KDots," not in text


def test_a_line_with_a_countdown_appears_as_its_countdown_begins():
    st = KaraokeStyle()  # early show on: other lines appear as the line before them starts
    laid = _laid((10000, 12000), (30000, 32000), (33000, 34000))
    A.plan_countdowns(laid, st)
    A.schedule(laid, st)
    # the countdown lines exactly 3 s ahead (3 dots, the first goes a second later), the other with the
    # line before it (which starts 3 s before it)
    assert [ll.start - ll.show_from for ll in laid] == [3000, 3000, 3000]
    assert A.countdown_dots(laid[0].show_from, laid[0].start, 3) == [10000, 9000, 8000]
    st.timing.early_show, st.timing.lead_in_ms, st.countdown.dots = False, 1000, 5
    A.plan_countdowns(laid, st)
    A.schedule(laid, st)
    assert [ll.start - ll.show_from for ll in laid] == [5000, 5000, 1000]


def test_per_line_setting(tmp_path):
    h = _project(tmp_path)
    l1, l2 = h.project.lyrics.lines
    S.update_line(h, l2.id, countdown="on")
    S.update_line(h, l1.id, countdown="off")
    assert (l1.countdown, l2.countdown) == (False, True)
    text, _ = S.karaoke_ass(h)
    dots = [ln for ln in text.splitlines() if ",KDots," in ln]
    assert len(dots) == 3 and all(ln.split(",")[2] <= "0:00:04.85" for ln in dots)
    S.update_line(h, l1.id, countdown="auto")
    assert l1.countdown is None
    with pytest.raises(S.ServiceError):
        S.update_line(h, l1.id, countdown="maybe")


def test_simple_mode_switches():
    from kara_align import settings as app_settings
    from kara_align.pipeline import resolve_task_style

    simple = app_settings.SimpleSettings()
    opts = app_settings.TaskStyleOptions(countdown_intro=False, countdown_interlude=True)
    style, _, _ = resolve_task_style(simple, opts)
    assert (style.countdown.intro, style.countdown.interlude) == (False, True)
    style, _, _ = resolve_task_style(simple, app_settings.TaskStyleOptions())
    assert (style.countdown.intro, style.countdown.interlude) == (True, True)  # as the style says


def test_a_line_with_a_countdown_takes_the_top_row():
    st = KaraokeStyle()
    st.layout.lines = 2
    st.countdown.intro = False
    # the first line takes the top row; after a long pause the next one would take the lower row in
    # turn, and its dots would cover the line above: it takes the top row, the one after it the lower
    laid = _laid((1000, 3000), (12000, 14000), (14500, 16000), (16500, 18000))
    A.plan_countdowns(laid, st)
    A.schedule(laid, st)
    assert [ll.countdown_ms > 0 for ll in laid] == [False, True, False, False]
    assert [ll.slot for ll in laid] == [0, 0, 1, 0]
    # a countdown set on a line by hand, while the top row is still being sung: in turn as before
    laid = _laid((1000, 5000), (4000, 6000))
    laid[1].line.countdown = True
    A.plan_countdowns(laid, st)
    A.schedule(laid, st)
    assert [ll.slot for ll in laid] == [0, 1]
