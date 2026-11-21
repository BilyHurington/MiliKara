"""Singers (多人演唱分色): who sings which part, kept through edits; drawn in each singer's colours."""

import io
import re

import numpy as np
import pytest
from fastapi.testclient import TestClient

from kara_align import service as S
from kara_align.karaoke import ass as A
from kara_align.lyrics import singers as SG
from kara_align.lyrics.pairing import merge_lines, split_line
from kara_align.models import KaraokeSinger, KaraokeSingerCombo, KaraokeStyle, Line, LyricsDoc, SingerSpan

from .test_karaoke import _home, _project, needs_ffmpeg  # noqa: F401  (the fixture is used by name)


def _spans(line):
    return [(s.start, s.end, s.singers) for s in line.singer_spans]


# ---------------------------------------------------------------- data


def test_loading_is_lenient_and_numbers_are_kept_in_range():
    ln = Line.model_validate({"text": "あいう", "singers": [2, 2, 0, 12, "3", True, 1],
                              "singer_spans": [{"start": 0, "end": 2, "singers": [3, 3, 99]}, {"start": 2, "end": 1},
                                               "x"]})
    assert ln.singers == [2, 12, 1]  # any number of singers
    assert _spans(ln) == [(0, 2, [3, 99])]
    st = KaraokeStyle.model_validate({"singers": {"members": [{"name": "A", "color": "#f00"}] * 12, "mix": "bad"}})
    assert len(st.singers.members) == 12 and st.singers.members[0].color == "#ff0000" and st.singers.mix == "split"
    # saved before singers had their own keys: singer n was key n (1–9)
    assert [m.key for m in st.singers.members] == [str(i) for i in range(1, 10)] + ["", "", ""]
    with pytest.raises(Exception):  # a style sent to be saved is refused instead
        KaraokeStyle.model_validate({"singers": {"members": [{"color": "#f00", "key": "1"}] * 2}}, context={"strict": True})


def test_normalize_joins_clips_and_drops_what_the_line_says_anyway():
    ln = Line(text="abcdef", singers=[1], singer_spans=[
        SingerSpan(start=4, end=9, singers=[2]), SingerSpan(start=0, end=2, singers=[1]),
        SingerSpan(start=2, end=3, singers=[1, 2]), SingerSpan(start=3, end=4, singers=[1, 2])])
    SG.normalize(ln)
    assert _spans(ln) == [(2, 4, [1, 2]), (4, 6, [2])]
    assert SG.effective(ln) == [(1,), (1,), (1, 2), (1, 2), (2,), (2,)]
    assert SG.range_singers(SG.effective(ln), 1, 4) == (1, 2)
    assert SG.range_singers(SG.effective(ln), 0, 2) == (1,)


def test_text_edit_keeps_each_characters_singers():
    ln = Line(text="窓に舞う桜", singers=[1], singer_spans=[SingerSpan(start=4, end=5, singers=[2])])
    old = ln.text
    ln.text = "窓の外に舞う桜"
    SG.remap(ln, old)
    assert _spans(ln) == [(6, 7, [2])]


def test_merge_and_split_carry_the_singers():
    doc = LyricsDoc(lines=[Line(text="あい", singers=[1]), Line(text="うえ", singers=[2]),
                           Line(text="おか", singers=[1, 2], singer_spans=[SingerSpan(start=1, end=2, singers=[3])])])
    merged = merge_lines(doc, [doc.lines[0].id, doc.lines[1].id])
    m = merged.lines[0]
    assert m.text == "あいうえ" and m.singers == [1] and _spans(m) == [(2, 4, [2])]
    parts = split_line(doc, doc.lines[2].id, 1)
    left, right = parts.lines[2], parts.lines[3]
    assert (left.text, left.singers, _spans(left)) == ("お", [1, 2], [])
    assert (right.text, right.singers, _spans(right)) == ("か", [1, 2], [(0, 1, [3])])


def test_removing_a_singer_moves_the_later_ones_up():
    doc = LyricsDoc(lines=[Line(text="あい", singers=[1, 3], singer_spans=[SingerSpan(start=0, end=1, singers=[2])]),
                           Line(text="う", singers=[2])])
    assert SG.shift_numbers(doc, 2) == 2
    # singer 2 of the part leaves (the part is then sung by 1 … no, by nobody else: back to the line's own)
    assert doc.lines[0].singers == [1, 2] and _spans(doc.lines[0]) == [(0, 1, [])]
    assert doc.lines[1].singers == []


def test_markers_need_a_name_used_twice():
    doc = LyricsDoc(lines=[Line(text="A：あいう"), Line(text="B：かきく"), Line(text="A&B：さしす"),
                           Line(text="（Hey!）たちつ"), Line(text="全员：なにぬ"), Line(text="はひふ")])
    found = SG.detect_markers(doc)
    assert [m.names for m in found] == [["A"], ["B"], ["A", "B"], ["全员"]]  # not "(Hey!)": once only
    assert SG.marker_names(found) == ["A", "B"]
    assert not SG.detect_markers(LyricsDoc(lines=[Line(text="（Hey!）たちつ"), Line(text="はひふ")]))


# ---------------------------------------------------------------- service


def test_assign_markers_and_remove(tmp_path):
    h = _project(tmp_path)
    l1, l2 = h.project.lyrics.lines
    # the song's lyrics named the singers: 「A：窓に舞う桜」「B：君」, and a line both sing
    S.update_line(h, l1.id, text="Ａ：窓に舞う桜")
    S.update_line(h, l2.id, text="Ａ＆Ｂ：君")
    lines = S.singer_markers(h)["lines"]
    assert [x["names"] for x in lines] == [["Ａ"], ["Ａ", "Ｂ"]]
    msgs = S.apply_singer_markers(h, strip=True)
    assert "Ａ、Ｂ" in msgs[0]
    l1, l2 = h.project.lyrics.lines
    assert (l1.text, l1.singers, l2.text, l2.singers) == ("窓に舞う桜", [1], "君", [1, 2])
    assert [m.name for m in h.project.karaoke.singers.members] == ["Ａ", "Ｂ"]
    assert h.project.karaoke.singers.members[0].color != h.project.karaoke.singers.members[1].color
    assert "".join(s.surface for s in l1.segments) == l1.text

    S.set_line_singers(h, [{"line_id": l1.id, "singers": [2], "spans": [{"start": 0, "end": 1, "singers": [1, 2]}],
                            "text": l1.text}])
    assert _spans(l1) == [(0, 1, [1, 2])]
    with pytest.raises(S.ServiceError):
        S.set_line_singers(h, [{"line_id": l1.id, "singers": [1], "spans": [], "text": "别的字"}])
    with pytest.raises(S.ServiceError):
        S.set_line_singers(h, [{"line_id": l1.id, "singers": [], "spans": [{"start": 0, "end": 99}]}])
    assert S.remove_singer(h, 1) == 2
    assert (l1.singers, _spans(l1), l2.singers) == ([1], [], [1])
    assert [m.name for m in h.project.karaoke.singers.members] == ["Ｂ"]


def test_http_api(tmp_path):
    from kara_align.web.server import create_app

    _project(tmp_path)
    c = TestClient(create_app(tmp_path))
    pid = "proj"  # (a workspace names its projects by folder)
    r = c.put(f"/api/projects/{pid}/karaoke/singers",
              json={"members": [{"name": "A", "color": "#ED35B3"}, {"name": "B", "color": "#2F80ED"}],
                    "mix": "gradient", "direction": "horizontal"})
    assert r.status_code == 200 and r.json()["singers"]["mix"] == "gradient"
    assert c.put(f"/api/projects/{pid}/karaoke/singers", json={"members": [{"color": "red"}]}).status_code == 400
    line = c.get(f"/api/projects/{pid}").json()["project"]["lyrics"]["lines"][0]
    r = c.put(f"/api/projects/{pid}/singers", json={"lines": [{"line_id": line["id"], "singers": [1, 2], "spans": []}]})
    assert r.status_code == 200 and r.json()["project"]["lyrics"]["lines"][0]["singers"] == [1, 2]
    assert r.json()["view"]["singer_markers"] == 0
    cols = c.post("/api/karaoke/singer-colors", json={"members": [{"color": "#2F80ED", "color_sung": "#000000"}]}).json()
    assert cols[0]["sung"] == "#000000" and cols[0]["unsung"].startswith("#")
    r = c.delete(f"/api/projects/{pid}/karaoke/singers/2")
    assert r.status_code == 200 and r.json()["project"]["lyrics"]["lines"][0]["singers"] == [1]
    assert c.delete(f"/api/projects/{pid}/karaoke/singers/5").status_code == 400
    assert c.get(f"/api/projects/{pid}/singers/markers").json()["lines"] == []


# ---------------------------------------------------------------- subtitles


def _two_singers(h, mix="split", direction="vertical", sweep="own"):
    l1, l2 = h.project.lyrics.lines
    l1.singers = [1, 2]
    l1.singer_spans = [SingerSpan(start=0, end=1, singers=[1]), SingerSpan(start=4, end=5, singers=[2])]
    l2.singers = [2]
    st = h.project.karaoke.model_copy(deep=True)
    st.singers.members = [KaraokeSinger(name="Ann", color="#ED35B3"), KaraokeSinger(name="Bo, b", color="#2F80ED")]
    st.singers.mix, st.singers.direction, st.ruby.sweep = mix, direction, sweep
    st.translation.enabled = True
    l1.translation = "窗外"
    h.project.karaoke = st
    h.save()


def _events(text, body):
    return [ln for ln in text.splitlines() if ln.startswith("Dialogue:") and ln.endswith(body)]


def test_no_singers_changes_nothing(tmp_path):
    h = _project(tmp_path)
    before, _ = S.karaoke_ass(h)
    h.project.lyrics.lines[0].singers = [3]  # a number the style has no singer for: its own colours
    after, _ = S.karaoke_ass(h)
    assert before == after and "KMain_" not in after


def test_singers_get_their_own_styles_and_bands(tmp_path):
    h = _project(tmp_path)
    _two_singers(h)
    text, _ = S.karaoke_ass(h)
    assert re.search(r"^Style: KMain_1,", text, re.M) and not re.search(r"^Style: KTrans_", text, re.M)
    # 窓: singer 1 alone (its style, its name as the event's actor; commas left out of the name)
    assert any(",KMain_1,Ann," in e for e in _events(text, "窓"))
    assert any(",KMain_2,Bo b," in e for e in _events(text, "}桜"))
    # に (both): two copies, the upper band singer 1, the lower singer 2, meeting at one y
    ni = [e for e in _events(text, "}に") if ",KMain_" in e]
    assert [e.split(",")[3] for e in ni] == ["KMain_1", "KMain_2"]
    (a0, a1), (b0, b1) = (tuple(map(int, re.search(r"\\clip\(0,(\d+),1920,(\d+)\)", e).groups())) for e in ni)
    assert a0 == 0 and a1 == b0 and b1 == 1080
    # the translation keeps its own colours (readable), named after the line's first singer
    assert any(",KTrans,Ann," in e for e in _events(text, "窗外"))


def test_following_sweep_bands_partition_each_chunk(tmp_path):
    h = _project(tmp_path)
    _two_singers(h, sweep="base")
    text, _ = S.karaoke_ass(h)
    ni = [e for e in text.splitlines() if ",KMain_" in e and e.endswith("}に")]
    assert len(ni) == 4  # unsung + sung per band
    rects = [re.findall(r"\\clip\((\d+),(\d+),(\d+),(\d+)\)", e) for e in ni]
    # every step of the animation: unsung and sung of a band meet at the same x
    for unsung, sung in (rects[0:2], rects[2:4]):
        assert len(unsung) == len(sung)
        for u, s_ in zip(unsung, sung):
            assert s_[0] == "0" and u[2] == "1920" and u[0] == s_[2] and u[1] == s_[1] and u[3] == s_[3]


def test_band_clip_travels_with_a_scrolling_line():
    rect = (150, 400, 250, 600)
    # a line scrolling 100 px up over 1 s: the band's edges travel with it (a fixed cut would let the
    # band's edge drift through the glyphs)
    assert A.band_clip([], rect, True, 400.0, dy=-100.0,
                       dur=1000) == "\\clip(150,400,250,600)\\t(0,1000,\\clip(150,300,250,500))"
    # inside a sweep: every step of the animation carries the shift of its own moment, and the band's edge
    # keeps travelling with the line once the sweep has left it
    tags = A.band_clip([(0, 1000, 300.0)], rect, True, 400.0, dy=-100.0, dur=1000)
    assert tags == ("\\clip(150,400,150,600)"
                    "\\t(500,833,\\clip(150,317,250,517))\\t(833,1000,\\clip(150,300,250,500))")


def test_band_cuts_travel_with_the_scrolling_line():
    from .test_round5_render import _line, _project as _render_project, _seg

    p, r = _render_project([_line([_seg("窓", "まど"), _seg("に", "に")])], times={0: (8000, 400)})
    p.lyrics.lines[0].singers = [1, 2]
    st = KaraokeStyle()
    st.layout.position, st.layout.center_scroll = "center", "flow"
    st.singers.members = [KaraokeSinger(name="Ann", color="#ED35B3"), KaraokeSinger(name="Bo", color="#2F80ED")]
    st.singers.mix, st.singers.direction = "split", "vertical"
    text, warnings = A.build_ass(p, r, st)
    assert not warnings
    moving = [e for e in text.splitlines()
              if e.startswith("Dialogue:") and ",KMain_" in e and "\\move(" in e and "}に" in e]
    assert len(moving) == 2  # に, sung by both: one copy per band
    ends = []
    for e in moving:
        y_from, y_to, dur = re.search(r"\\move\([\d.]+,([\d.]+),[\d.]+,([\d.]+),0,(\d+)\)", e).groups()
        dy = round(float(y_to) - float(y_from))
        assert dy < 0 and f"\\t(0,{dur}," in e  # the cut animates over the same stretch as the move
        (a0, a1), (b0, b1) = [(int(m[1]), int(m[3])) for m in re.findall(r"\\clip\((\d+),(-?\d+),(\d+),(-?\d+)\)", e)]
        assert b0 - a0 == dy and b1 - a1 == dy
        ends.append((a0, a1, b0, b1))
    # the two bands meet at one edge: where they start and where they end
    assert ends[0][1] == ends[1][0] and ends[0][3] == ends[1][2]


def test_band_clip_follows_the_sweep_only_inside_the_band():
    moves = [(0, 1, 100.0), (0, 1000, 300.0), (1000, 1001, 400.0)]
    tags = A.band_clip(moves, (150, 0, 250, 1080), True, 400.0)
    # nothing before the band is reached, then it grows from 150 to 250 between 250 ms and 750 ms
    assert tags.startswith("\\clip(150,0,150,1080)")
    assert "\\t(250,750,\\clip(150,0,250,1080))" in tags
    assert A.band_clip([], (150, 0, 250, 1080), False, 400.0) == "\\clip(250,0,250,1080)"


def test_gradient_and_horizontal_bands():
    cols = {1: {r: "#FF0000" for r in A._ROLES}, 2: {r: "#0000FF" for r in A._ROLES}}
    grad = A.plan_bands((1, 2), cols, "gradient", "vertical", 800, 880, None, 1920, 1080)
    assert len(grad) >= 4 and all(b.blend for b in grad)
    assert grad[0].rect[1] == 0 and grad[-1].rect[3] == 1080
    reds = [int(b.colors["sung"][1:3], 16) for b in grad]
    assert reds == sorted(reds, reverse=True) and reds[0] > reds[-1]
    # side by side: once across the whole run (100–260), not per character
    side = A.plan_bands((1, 2), cols, "split", "horizontal", 0, 0, (100, 260), 1920, 1080)
    assert [(b.singer, b.rect[0], b.rect[2]) for b in side] == [(1, 0, 180), (2, 180, 1920)]
    assert [b.singer for b in A.plan_bands((1, 2), cols, "split", "horizontal", 0, 0, (100, 260), 1920, 1080,
                                           within=(90, 150))] == [1]
    assert A.plan_bands((7,), cols, "split", "vertical", 0, 0, None, 1920, 1080) == [A.BASE_BAND]


@needs_ffmpeg
def test_parts_sung_together_render_in_both_colours(tmp_path):
    from PIL import Image

    h = _project(tmp_path)
    _two_singers(h)
    arr = np.asarray(Image.open(io.BytesIO(S.karaoke_preview(h, 2250))).convert("RGB")).astype(int)
    pink = (arr[..., 0] > 180) & (arr[..., 2] > 120) & (arr[..., 1] < 110)  # singer 1 sung (#ED35B3)
    blue = (arr[..., 2] > 180) & (arr[..., 0] < 110)  # singer 2 sung (#2F80ED)
    # に舞う (both, sung by now): pink above, blue below
    x0, x1 = 440, 760
    rows_p = np.where(pink[:, x0:x1].any(axis=1))[0]
    rows_b = np.where(blue[:, x0:x1].any(axis=1))[0]
    assert rows_p.size and rows_b.size and np.median(rows_p) < np.median(rows_b)


def test_split_parts_glow_blended(tmp_path):
    h = _project(tmp_path)
    _two_singers(h)
    h.project.karaoke.glow.enabled = True
    text, _ = S.karaoke_ass(h)
    ni = _events(text, "}に")
    fills = [e for e in ni if ",KMain_" in e]
    glows = [e for e in ni if ",KGlow," in e]
    assert len(fills) == 2  # the text: one band each
    assert len(glows) > 4  # its glow: thin strips blending the two (no seam beside the glyphs)
    assert len({re.search(r"\\3c(&H\w+&)", e).group(1) for e in glows}) > 4
    # side by side: 「に舞う」 (both) split once from left to right: に is in singer 1's half, う in singer 2's
    _two_singers(h, direction="horizontal")
    h.project.karaoke.glow.enabled = True
    text, _ = S.karaoke_ass(h)
    assert {e.split(",")[3] for e in _events(text, "}に") if ",KMain_" in e} == {"KMain_1"}
    assert {e.split(",")[3] for e in _events(text, "}う") if ",KMain_" in e} == {"KMain_2"}


def test_readings_take_the_top_singer_when_split_top_to_bottom(tmp_path):
    h = _project(tmp_path)
    _two_singers(h)
    text, _ = S.karaoke_ass(h)
    # 舞 (both) has the reading ま: one copy, singer 1's
    ruby = [e for e in text.splitlines() if ",KRuby_" in e and e.endswith("ま")]
    assert ruby and {e.split(",")[3] for e in ruby} == {"KRuby_1"} and not any("\\clip" in e for e in ruby)



def _ruby(text, body):
    return [e for e in text.splitlines() if ",KRuby_" in e and e.endswith(body)]


def test_readings_split_or_first_as_chosen(tmp_path):
    h = _project(tmp_path)
    _two_singers(h)
    h.project.karaoke.singers.ruby = "split"  # top to bottom, the reading split too
    text, _ = S.karaoke_ass(h)
    ma = _ruby(text, "ま")
    assert [e.split(",")[3] for e in ma] == ["KRuby_1", "KRuby_2"] and all("\\clip(0," in e for e in ma)
    # side by side: by default the reading follows its place; "first": the first singer's colours
    _two_singers(h, direction="horizontal")
    text, _ = S.karaoke_ass(h)
    ma = _ruby(text, "ま")
    rects = [tuple(map(int, re.search(r"\\clip\((\d+),(\d+),(\d+),(\d+)\)", e).groups())) for e in ma]
    assert rects and all(r[1] == 0 and r[3] == 1080 for r in rects)  # cut by where it is, the whole height
    h.project.karaoke.singers.ruby = "first"
    text, _ = S.karaoke_ass(h)
    assert {e.split(",")[3] for e in _ruby(text, "ま")} == {"KRuby_1"} and not any("\\clip" in e for e in _ruby(text, "ま"))


def test_a_combination_has_its_own_look(tmp_path):
    from kara_align.models import KaraokeSingers

    h = _project(tmp_path)
    _two_singers(h)  # the default: split top to bottom
    sg = h.project.karaoke.singers
    sg.combos = [KaraokeSingerCombo(key="3", singers=[1, 2], mix="gradient", direction="horizontal")]
    assert sg.look((1, 2)) == ("gradient", "horizontal") and sg.look((2, 1)) == ("gradient", "horizontal")
    assert sg.look((1, 3)) == ("split", "vertical")
    text, _ = S.karaoke_ass(h)
    ni = [e for e in _events(text, "}に") if ",KMain_" in e]
    # 「に舞う」 blended from left to right: strips side by side, the whole height each
    rects = [tuple(map(int, re.search(r"\\clip\((\d+),(\d+),(\d+),(\d+)\)", e).groups())) for e in ni]
    assert rects and all(r[1] == 0 and r[3] == 1080 for r in rects) and len(rects) >= 2
    # a combination that only sets the direction keeps the default mix
    sg.combos = [KaraokeSingerCombo(key="3", singers=[2, 1], direction="horizontal")]
    assert sg.look((1, 2)) == ("split", "horizontal")
    # the same singers twice in the same order: refused when saved, dropped when loaded
    two = [{"key": "3", "singers": [1, 2]}, {"key": "4", "singers": [1, 2], "mix": "gradient"}]
    members = [{"color": "#ED35B3"}, {"color": "#2F80ED"}]
    assert len(KaraokeSingers.model_validate({"members": members, "combos": two}).combos) == 1
    with pytest.raises(Exception):
        KaraokeSingers.model_validate({"members": members, "combos": two}, context={"strict": True})
    assert KaraokeSingers.model_validate({"members": members, "ruby": "bad"}).ruby == "auto"


def test_saved_combinations(tmp_path):
    from kara_align.models import KaraokeSingers

    members = [{"color": "#ED35B3"}, {"color": "#2F80ED"}, {"color": "#F5C400"}]  # keys 1, 2, 3 (older projects)
    sg = KaraokeSingers.model_validate({"members": members, "combos": [
        {"key": 4, "singers": [1, 2]}, {"key": 2, "singers": [1, 3]}, {"key": 4, "singers": [2, 3]},
        {"key": 5, "singers": [1, 9]}, {"key": "Q", "singers": [3, 1]}, {"key": "l", "singers": [2, 1]}]})
    # loading: a key already taken or not usable (l loops) is dropped, a combination of fewer than two goes
    assert [(c.key, c.singers) for c in sg.combos] == [("4", [1, 2]), ("", [1, 3]), ("", [2, 3]), ("q", [3, 1]), ("", [2, 1])]
    assert sg.free_key() == "5"
    for bad in ({"key": 3, "singers": [1, 2]}, {"key": "p", "singers": [1, 2]}, {"key": "7", "singers": [1]}):
        with pytest.raises(Exception):
            KaraokeSingers.model_validate({"members": members, "combos": [bad]}, context={"strict": True})
    h = _project(tmp_path)
    S.set_singers(h, {"members": members, "combos": [{"key": "a", "singers": [1, 3]}, {"key": "b", "singers": [1, 2]}]})
    S.remove_singer(h, 2)  # 1+3 becomes 1+2; 1+2 has one singer left and goes; keys stay with their singers
    sg = h.project.karaoke.singers
    assert [(c.key, c.singers) for c in sg.combos] == [("a", [1, 2])]
    assert [m.key for m in sg.members] == ["1", "3"]


def test_own_keys_and_no_limit(tmp_path):
    from kara_align.karaoke.themes import SINGER_SWATCHES, new_singer_color
    from kara_align.models import SINGER_KEYS, KaraokeSingers

    h = _project(tmp_path)
    members = [{"name": f"S{i}", "color": new_singer_color(set(), i), "key": SINGER_KEYS[i] if i < 33 else ""} for i in range(40)]
    members[0]["key"], members[1]["key"], members[32]["key"] = "z", "1", "2"  # any usable key, in any order
    S.set_singers(h, {"members": members})
    sg = h.project.karaoke.singers
    assert len(sg.members) == 40 and sg.members[0].key == "z" and sg.members[39].key == ""
    assert new_singer_color({"#ED35B3"}, 1) == "#2F80ED"
    colors = {new_singer_color(set(SINGER_SWATCHES), i) for i in range(9, 30)}
    assert len(colors) == 21  # past the swatches: all different
    h.project.lyrics.lines[0].singers = [40]
    text, _ = S.karaoke_ass(h)
    assert "Style: KMain_40," in text
    assert KaraokeSingers(members=[]).free_key() == "1"


def test_a_saved_set_of_singers(tmp_path, monkeypatch):
    from kara_align.karaoke import singer_presets as P

    monkeypatch.setenv("KARA_ALIGN_HOME", str(tmp_path / "home"))
    group = {"members": [{"name": "Ann", "color": "#ED35B3", "key": "a"}, {"name": "Bo", "color": "#2F80ED", "key": "b"},
                         {"name": "Cy", "color": "#3CC46A", "key": "c"}],
             "combos": [{"key": "d", "singers": [1, 2]}], "mix": "gradient"}
    saved = P.save_preset("三人组", group)
    assert P.save_preset("三人组", group)["id"] == saved["id"]  # the same name again: replaced
    assert [p["name"] for p in P.list_presets()] == ["三人组"]
    with pytest.raises(P.PresetError):
        P.save_preset("空", {"members": []})
    # a song where Bo and an unnamed singer 2 already sing, and someone the preset does not know
    h = _project(tmp_path)
    l1, l2 = h.project.lyrics.lines
    S.set_singers(h, {"members": [{"name": "Bo", "color": "#111111"}, {"name": "", "color": "#222222"},
                                  {"name": "Dee", "color": "#333333"}, {"name": "Eve", "color": "#444444"}]})
    l1.singers, l1.singer_spans = [1], [SingerSpan(start=0, end=1, singers=[2, 3])]
    l2.singers = [3]
    out = S.apply_singer_preset(h, P.get_preset(saved["id"])["singers"])
    sg = h.project.karaoke.singers
    # Bo by name; the unnamed singer and Dee sing here and are not in the preset: kept; Eve sings nothing
    assert [m.name for m in sg.members] == ["Ann", "Bo", "Cy", "", "Dee"]
    assert [m.key for m in sg.members] == ["a", "b", "c", "1", "2"]  # the first free keys and sg.members[4].color == "#333333"
    assert sg.mix == "gradient" and [(c.key, c.singers) for c in sg.combos] == [("d", [1, 2])]
    assert l1.singers == [2] and _spans(l1) == [(0, 1, [4, 5])] and l2.singers == [5]
    assert out == {"lines": 2, "kept": ["演唱者 2", "Dee"]}
    P.delete_preset(saved["id"])
    assert P.list_presets() == []


def test_blanks_are_nobodys():
    # a space inside a part sung together joins it; between two different parts it is the line's own
    ln = Line(text="あい うえ　お", singers=[1], singer_spans=[SingerSpan(start=0, end=6, singers=[1, 2])])
    SG.normalize(ln)
    assert _spans(ln) == [(0, 5, [1, 2])]  # the full-width space before お (singer 1 alone) is not part of it
    ln = Line(text="あい うえ", singers=[], singer_spans=[SingerSpan(start=0, end=2, singers=[1]),
                                                        SingerSpan(start=2, end=5, singers=[2])])
    SG.normalize(ln)
    assert _spans(ln) == [(0, 2, [1]), (3, 5, [2])]  # the space belongs to nobody
    ln = Line(text="あい うえ", singers=[1], singer_spans=[SingerSpan(start=2, end=3, singers=[2])])
    SG.normalize(ln)
    assert _spans(ln) == []  # a space alone is never a part
    chars = SG.effective(Line(text="a b", singers=[], singer_spans=[SingerSpan(start=0, end=3, singers=[2])]))
    assert SG.range_singers([(), (2,), (2,)], 0, 3, " bb") == (2,)
    assert chars == [(2,), (2,), (2,)]


def test_singer_presets_over_the_api(tmp_path, monkeypatch):
    from kara_align.web.server import create_app

    monkeypatch.setenv("KARA_ALIGN_HOME", str(tmp_path / "home"))
    c = TestClient(create_app(tmp_path / "projects"))
    pid = c.post("/api/projects", json={"name": "s", "mode": "plain"}).json()["project"]["id"]
    group = {"members": [{"name": "Ann", "color": "#ED35B3", "key": "q"}], "combos": []}
    assert c.post("/api/karaoke/singer-presets", json={"name": "", "singers": group}).status_code == 400
    bad = {"members": [{"name": "Ann", "key": "p"}]}
    assert c.post("/api/karaoke/singer-presets", json={"name": "x", "singers": bad}).status_code == 400
    sp = c.post("/api/karaoke/singer-presets", json={"name": "组", "singers": group}).json()
    assert [p["name"] for p in c.get("/api/karaoke/singer-presets").json()] == ["组"]
    v = c.post(f"/api/projects/{pid}/karaoke/singers/preset", json={"id": sp["id"]}).json()
    assert v["project"]["karaoke"]["singers"]["members"][0]["key"] == "q" and v["lines"] == 0 and v["kept"] == []
    assert c.post(f"/api/projects/{pid}/karaoke/singers/preset", json={"id": "nope"}).status_code == 404
    assert c.delete(f"/api/karaoke/singer-presets/{sp['id']}").json() == {"ok": True}
    assert c.get("/api/karaoke/singer-presets").json() == []


def test_a_translations_glow_takes_the_singers_colours_left_to_right(tmp_path):
    h = _project(tmp_path)
    _two_singers(h)  # 窓 Ann, に舞う both, 桜 Bo; split top to bottom
    h.project.karaoke.glow.enabled = True
    text, _ = S.karaoke_ass(h)
    glows = [e for e in _events(text, "窗外") if ",KGlow," in e]
    rects = [tuple(map(int, re.search(r"\\clip\((\d+),(\d+),(\d+),(\d+)\)", e).groups())) for e in glows]
    # strips side by side, each the whole height (never top to bottom), blending Ann's glow into Bo's
    assert len(glows) >= 4 and all(r[1] == 0 and r[3] == 1080 for r in rects)
    assert [r[0] for r in rects] == sorted(r[0] for r in rects)
    # under each line too
    h.project.karaoke.translation.position = "line"
    under = [e for e in _events(S.karaoke_ass(h)[0], "窗外") if ",KGlow," in e]
    assert len(under) >= 4 and all("\\clip(" in e for e in under)
    h.project.karaoke.translation.position = "opposite"
    colours = [re.search(r"\\3c(&H\w+&)", e).group(1) for e in glows]
    assert len(set(colours)) >= 3
    # the text itself: the translation's own colour
    assert [e for e in _events(text, "窗外") if ",KTrans," in e]
    # one singer: that singer's glow, no strips; switched off: the translation's own glow
    l1 = h.project.lyrics.lines[0]
    l1.singers, l1.singer_spans = [2], []
    text, _ = S.karaoke_ass(h)
    one = [e for e in _events(text, "窗外") if ",KGlow," in e]
    assert len(one) == 1 and "\\clip" not in one[0]
    h.project.karaoke.translation.singer_glow = False
    text, _ = S.karaoke_ass(h)
    own = [e for e in _events(text, "窗外") if ",KGlow," in e]
    assert len(own) == 1 and own[0] != one[0] and ",KTrans,," in [e for e in _events(text, "窗外") if "KTrans" in e][0]
