"""Who sings beside each line, the style's symbols there, and the legend of the singers' colours."""

import re

from kara_align import service as S
from kara_align.karaoke.fonts import Measurer
from kara_align.karaoke.info import legend_entries
from kara_align.karaoke.linetags import Tagger, label_rows
from kara_align.models import KaraokeSinger, KaraokeSingerCombo, KaraokeSingers, KaraokeStyle

from .test_karaoke import _home, _project  # noqa: F401  (the fixture is used by name)
from .test_singers import _two_singers


def _sg(n, **kw):
    return KaraokeSingers(members=[KaraokeSinger(name=f"N{i}", color="#ED35B3") for i in range(1, n + 1)], **kw)


def test_label_rows():
    sg = _sg(4)
    sg.members[0].label = "A"
    sg.members[2].name = ""
    assert label_rows(sg, ()) == []
    assert label_rows(sg, (1,)) == [[("A", 1)]]  # its own label, else its name, else its number
    assert label_rows(sg, (3,)) == [[("演唱者 3", 3)]]
    # two singers: one row each; three: the first two above
    assert label_rows(sg, (2, 1)) == [[("N2", 2)], [("A", 1)]]
    assert label_rows(sg, (1, 2, 4)) == [[("A", 1), ("/", 1), ("N2", 2)], [("N4", 4)]]
    # a combination of exactly these singers, with its own label: one row, in nobody's colour
    sg.combos = [KaraokeSingerCombo(key="5", singers=[4, 2, 1], label="全员")]
    assert label_rows(sg, (1, 2, 4)) == [[("全员", 0)]]
    assert label_rows(sg, (1, 2)) == [[("A", 1)], [("N2", 2)]]


def test_two_rows_fill_the_lyrics_em_box():
    st = KaraokeStyle()
    st.singers = _sg(2)
    m = Measurer("Hiragino Sans", True, 80.0)
    tg = Tagger(st, {1: ("#ED35B3", "#ED35B3"), 2: ("#2F80ED", "#2F80ED")}, "Hiragino Sans", True, 80.0, m.ink)
    i0, i1 = m.ink
    two, one = tg.label((1, 2)), tg.label((1,))
    assert len(two.rows) == 2 and two.size < one.size < 80
    main_y, scale = 900.0, 1.0
    em_top, em_bottom = main_y - 80 + 80 * i0, main_y - 80 + 80 * i1
    upper, lower = tg.row_baselines(two, main_y, 1.0)
    s = two.size * scale
    # the upper row's em top on the lyric's, the lower row's em bottom on the lyric's
    assert abs((upper - s + s * i0) - em_top) < 1e-6 and abs((lower - s + s * i1) - em_bottom) < 1e-6
    assert (upper - s + s * i1) < (lower - s + s * i0)  # with a gap between the rows
    (mid,) = tg.row_baselines(one, main_y, 1.0)
    s = one.size
    assert abs(((mid - s + s * i0) + (mid - s + s * i1)) / 2 - (em_top + em_bottom) / 2) < 1e-6


def _tags(text, body):
    return [e for e in text.splitlines() if e.startswith("Dialogue:") and ",KMain,," in e and e.endswith(body)]


def _x(event):
    return float(re.search(r"\\pos\(([\d.]+),", event).group(1))


def test_labels_and_symbols_beside_the_lines(tmp_path):
    h = _project(tmp_path)
    _two_singers(h)
    before, _ = S.karaoke_ass(h)
    assert not _tags(before, "}Ann")
    st = h.project.karaoke
    st.singers.labels = "start"
    st.decor.end = "♪"
    h.save()
    text, _ = S.karaoke_ass(h)
    # line 1 (Ann and Bo): two rows right-aligned before the lyric, in their colours; line 2 (Bo) one row
    ann, bo1, bo2 = _tags(text, "}Ann"), *[[e] for e in _tags(text, "}Bo, b")]
    assert len(ann) == 1 and "\\an3" in ann[0] and "\\1c&HB335ED&" in ann[0]
    assert _x(ann[0]) == _x(bo1[0]) and _y(ann[0]) < _y(bo1[0])
    assert float(re.search(r"\\fs([\d.]+)", ann[0]).group(1)) < float(re.search(r"\\fs([\d.]+)", bo2[0]).group(1))
    notes = _tags(text, "}♪")
    assert len(notes) == 2 and all("\\an1" in e for e in notes)
    # the lyric moves right to make room (the line, labels and all, stays centred)
    first = [e for e in text.splitlines() if ",KMain_1,Ann," in e and e.endswith("窓")]
    first_before = [e for e in before.splitlines() if ",KMain_1,Ann," in e and e.endswith("窓")]
    assert _x(first[0]) > _x(first_before[0]) and _x(first[0]) > _x(ann[0])
    # at the end instead: after the symbols
    st.singers.labels = "end"
    h.save()
    text, _ = S.karaoke_ass(h)
    (ann,) = _tags(text, "}Ann")
    assert "\\an1" in ann and _x(ann) > _x(_tags(text, "}♪")[0])


def _y(event):
    return float(re.search(r"\\pos\([\d.]+,([\d.]+)\)", event).group(1))


def test_legend(tmp_path):
    h = _project(tmp_path)
    _two_singers(h)
    st = h.project.karaoke
    st.singers.members[0].label = "A"
    assert [r[0] for r in legend_entries(h.project, st)] == ["Ann（A）", "Bo, b"]
    st.singers.combos = [KaraokeSingerCombo(key="3", singers=[2, 1], mix="gradient", label="一起")]
    rows = legend_entries(h.project, st)
    assert rows[-1] == ("一起", ["#ED35B3", "#2F80ED"], "gradient")
    text, _ = S.karaoke_ass(h)
    card = [e for e in text.splitlines() if ",KInfo," in e]
    assert any(e.endswith("}Ann（A）") for e in card) and any(e.endswith("}一起") for e in card)
    # beside each row a square of its colours: the combination's blended in stripes
    squares = [e for e in card if e.startswith("Dialogue: 9,") and "{\\p1}" in e]
    assert len(squares) > 3 and any("\\1c&HB335ED&" in e for e in squares)
    # only one singer heard, or switched off: none
    st.singers.legend = False
    h.save()
    assert not any(e.endswith("}一起") for e in S.karaoke_ass(h)[0].splitlines())
    st.singers.legend = True
    for ln in h.project.lyrics.lines:
        ln.singers, ln.singer_spans = [1], []
    assert legend_entries(h.project, st) == []


def test_long_names_stay_inside_the_frame(tmp_path):
    h = _project(tmp_path)
    _two_singers(h)
    st = h.project.karaoke
    st.text.size = 200  # the lyric alone is half the frame: with the labels it must wrap and shrink
    st.singers.members[0].label, st.singers.members[1].label = "天海春香" * 3, "秋月律子" * 3
    st.singers.labels = "start"
    st.decor.end = "★"
    h.save()
    text, warnings = S.karaoke_ass(h)
    family = re.search(r"^Style: KMain,([^,]+),", text, re.M).group(1)
    for e in _tags(text, "天海春香") + _tags(text, "秋月律子") + _tags(text, "★"):
        x, size = _x(e), float(re.search(r"\\fs([\d.]+)", e).group(1))
        body = e.rsplit("}", 1)[1]
        w = Measurer(family, True, size).width(body)
        left, right = (x - w, x) if "\\an3" in e else (x, x + w)
        assert left >= 0 and right <= 1920, e
    # the labels go with the first piece of the wrapped line, the symbol with the last
    first_label = min(e.split(",")[1] for e in _tags(text, "天海春香" * 3))
    last_star = max(e.split(",")[1] for e in _tags(text, "★"))
    assert first_label < last_star


def test_names_the_lyric_font_lacks_use_a_font_that_has_them(tmp_path, monkeypatch):
    from kara_align.karaoke import fonts

    h = _project(tmp_path)
    _two_singers(h)
    h.project.karaoke.singers.labels = "end"
    h.save()
    monkeypatch.setattr(fonts, "covering", lambda family, bold, text, prefer, **kw: "Other Sans")
    text, _ = S.karaoke_ass(h)
    assert all("\\fnOther Sans" in e for e in _tags(text, "}Ann"))
    assert not any("\\fnOther Sans" in e for e in text.splitlines() if e.endswith("窓"))


def test_rows_of_as_many_characters_are_as_long():
    """A name with characters the lyric font lacks (濑 织 in a Japanese font): every label in one font
    that has them all, also where the system fills them in (macOS) — else that row comes out shorter."""
    from kara_align.karaoke import fonts

    lyric = next((f for f in ("Hiragino Maru Gothic ProN", "Yu Gothic", "Meiryo")
                  if fonts.covering_family("春", True, [f]) and fonts.lacking(f, True, "濑织")), None)
    if lyric is None or fonts.covering_family("濑织春", True, [fonts.BUNDLED_JP, *fonts.HAN_FAMILIES]) is None:
        import pytest
        pytest.skip("no Japanese font without 濑 here")
    st = KaraokeStyle()
    st.singers = _sg(4)
    for m, name in zip(st.singers.members, ("天海春香", "高槻弥生", "秋月律子", "水濑伊织")):
        m.name = name
    m = Measurer(lyric, True, 80.0)
    tg = Tagger(st, {i: ("#ED35B3", "#ED35B3") for i in range(1, 5)}, lyric, True, 80.0, m.ink)
    assert tg.family != lyric
    block = tg.label((1, 2, 3, 4))
    widths = [Measurer(tg.family, True, block.size).width("".join(r[0] for r in row)) for row in block.rows]
    assert abs(widths[0] - widths[1]) < 0.5
