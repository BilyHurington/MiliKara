"""Karaoke rendering fixes of the fifth review round (R1–R8, S7, R-L*)."""

import io
import json
import re
import shutil
import subprocess
import sys

import numpy as np
import pytest

from kara_align import service as S
from kara_align.karaoke import ass as A
from kara_align.karaoke.fonts import Measurer, default_family
from kara_align.models import (AlignmentResult, Coverage, KaraokeStyle, Line, Project, Segment, UnitTiming,
                               VideoAsset)
from kara_align.reading.prepare import units_from_spec

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffprobe") is None or shutil.which("ffmpeg") is None,
                                  reason="needs ffmpeg with libass")


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("KARA_ALIGN_HOME", str(tmp_path / "home"))


def _seg(surface, reading=None):
    if reading is None:
        return Segment(surface=surface, units=[])
    return Segment(surface=surface, reading=reading, units=units_from_spec(reading, None, "ja"))


def _line(segs, translation=None):
    return Line(text="".join(s.surface for s in segs), segments=segs, translation=translation)


def _project(lines, *, t0=1000, step=300, gap=200, times=None, size=None):
    """A project whose units are sung one after another (or at ``times[line index]`` = (start, step))."""
    p = Project(name="t")
    p.lyrics.lines = lines
    if size:
        p.video = VideoAsset(sha256="x", container=".mp4", duration_ms=60000, width=size[0], height=size[1],
                             audio_sha256="y")
    units, t = [], t0
    for i, ln in enumerate(lines):
        if times and i in times:
            t, step_i = times[i]
        else:
            step_i = step
        for s in ln.segments:
            for u in s.units:
                units.append(UnitTiming(unit_id=u.id, line_id=ln.id, segment_id=s.id, reading=u.reading,
                                        start_ms=t, end_ms=t + step_i))
                t += step_i
        t += gap
    return p, AlignmentResult.model_construct(units=units, coverage=Coverage(), lines=[])


def _events(text, style=None):
    out = [l for l in text.splitlines() if l.startswith("Dialogue")]
    return [l for l in out if f",{style}," in l] if style else out


def _ms(t):
    h, m, s = t.split(":")
    return round((int(h) * 3600 + int(m) * 60 + float(s)) * 1000)


def _pos(l):
    x, y = re.search(r"\\pos\(([-\d.]+),([-\d.]+)\)", l).groups()
    return float(x), float(y)


class _Fixed:
    """1 character = ``per_char`` px."""

    def __init__(self, per_char):
        self.per_char = per_char

    def width(self, text):
        return len(text) * self.per_char


def _chunk(base, ruby=""):
    return A.Chunk([A.Part(base, 0, 1)], [A.Part(ruby, 0, 1)] if ruby else [])


def _png(ass_text, t_ms, size=(1920, 1080)):
    from PIL import Image

    from kara_align.karaoke.render import preview_png

    return np.asarray(Image.open(io.BytesIO(preview_png(ass_text, t_ms, size))).convert("RGB")).astype(int)


# ---------------------------------------------------------------- R1 / R8: the ruby following the lyric


def _base_style():
    st = KaraokeStyle()
    st.layout.lines = 1
    st.ruby.sweep = "base"
    st.timing.fade_in_ms = st.timing.fade_out_ms = 0
    st.timing.advance_ms = 0  # frames are taken at exact syllable times
    return st


def test_reading_wider_than_its_lyric_turns_sung_completely():
    # 窓 (まど) 1000–1600, に 1600–1900
    p, r = _project([_line([_seg("窓", "まど"), _seg("に", "に")])])
    st = _base_style()
    st.ruby.size_pct = 70  # まど clearly wider than 窓
    text, _ = A.build_ass(p, r, st)
    main, ruby = _events(text, "KMain"), _events(text, "KRuby")
    lyric = next(l for l in main if l.endswith("窓") and "\\iclip" not in l)
    reading = next(l for l in ruby if l.endswith("まど") and "\\iclip" not in l)
    cuts = lambda l: [(int(a), int(b), int(x)) for a, b, x in  # noqa: E731
                      re.findall(r"\\t\((\d+),(\d+),\\clip\(0,0,(-?\d+),", l)]
    lc, rc = cuts(lyric), cuts(reading)
    cx = _pos(lyric)[0]
    rw = Measurer(default_family(), True, 88 * 0.7).width("まど")
    bw = Measurer(default_family(), True, 88).width("窓")
    assert rw > bw + 20
    # while the lyric is swept, both are cut by the same line; when 窓 is done the reading is sung to its end
    assert rc[:len(lc) - 1] == lc[:-1]
    end = lc[-2][1]  # the lyric's sweep ends here
    assert rc[-1][0] == end and rc[-1][2] >= cx + rw / 2
    assert lc[-1][2] < cx + rw / 2  # the lyric's own cut never reaches into the next chunk that far
    # the unsung copy is cut by the matching \iclip (the rest), so fades never show one through the other
    unsung = next(l for l in ruby if l.endswith("まど") and "\\iclip" in l)
    assert unsung.split("\\iclip", 1)[1].replace("\\iclip", "\\clip") == reading.split("\\clip", 1)[1]
    assert "\\shad0" not in reading  # the sung copy draws its own shadow (the unsung one is cut away there)


@needs_ffmpeg
def test_reading_overhang_and_fades_render():
    p, r = _project([_line([_seg("窓", "まど"), _seg("に", "に")])])
    st = _base_style()
    st.ruby.size_pct = 70
    st.text.color_sung, st.text.color_unsung = "#0000FF", "#FFFFFF"
    st.text.shadow = 0
    text, _ = A.build_ass(p, r, st)
    reading = next(l for l in _events(text, "KRuby") if l.endswith("まど") and "\\iclip" not in l)
    cx, ry = _pos(reading)
    rw = Measurer(default_family(), True, 88 * 0.7).width("まど")
    img = _png(text, 1610)  # 窓 sung, に just starting
    band = img[int(ry - 88 * 0.7):int(ry), int(cx + rw / 4):int(cx + rw / 2)]  # the right quarter of まど
    blue = ((band[..., 2] > 180) & (band[..., 0] < 80)).sum()
    white = ((band[..., 0] > 200) & (band[..., 1] > 200) & (band[..., 2] > 200)).sum()
    assert blue > 50 and white < 5, (blue, white)
    # mid-fade the sung colour is not washed out by the unsung copy under it (R8): same as the own sweep
    st.timing.fade_out_ms = 1000
    base = _png(A.build_ass(p, r, st)[0], 2150)  # sung until 1900, gone at 2400
    st.ruby.sweep = "own"
    own = _png(A.build_ass(p, r, st)[0], 2150)
    region = (slice(800, 1080), slice(700, 1300))
    mb = base[region][base[region].sum(axis=2) > 60].mean(axis=0)
    mo = own[region][own[region].sum(axis=2) > 60].mean(axis=0)
    assert np.abs(mb - mo).max() < 12, (mb, mo)


# ---------------------------------------------------------------- R2 / L6: overhang


def test_readings_never_meet_across_a_narrow_chunk():
    main, ruby = _Fixed(100), _Fixed(45)
    # 承(うけたまわ) half-width space 承(うけたまわ) "!" 志(こころざし): 225 px readings over 100 px kanji
    chunks = [_chunk("承", "うけたまわ"), _chunk(" "), _chunk("承", "うけたまわ"), _chunk("!"), _chunk("志", "こころざし")]
    main_w = {" ": 25, "!": 30}
    main.width = lambda t: main_w.get(t, 100 * len(t))
    w = A.chunk_widths(chunks, main, ruby, 45, "widen")
    x, spans = 0.0, []
    for c, cw in zip(chunks, w):
        if c.ruby:
            half = ruby.width(c.ruby_text) / 2
            spans.append((x + cw / 2 - half, x + cw / 2 + half))
        x += cw
    assert all(a[1] <= b[0] + 1e-6 for a, b in zip(spans, spans[1:])), spans
    # a kana neighbour as wide as a ruby character still takes one character of overhang
    w = A.chunk_widths([_chunk("は"), _chunk("新", "あたら"), _chunk("しい")], _Fixed(100), ruby, 45, "widen")
    assert w[1] == 100


def test_overhang_at_the_line_ends_counts_for_the_margins():
    ruby = _Fixed(45)
    chunks = [_chunk("承", "うけたまわ"), _chunk("る")]
    widths = A.chunk_widths(chunks, _Fixed(100), ruby, 45, "widen")
    left, right = A.ruby_overhang(chunks, widths, ruby)
    assert left == pytest.approx(ruby.width("うけたまわ") / 2 - widths[0] / 2) and right == 0
    # a left-aligned line starting with a wide reading: the reading stays inside the margin
    p, r = _project([_line([_seg("承", "うけたまわ"), _seg("る", "る")]), _line([_seg("君", "きみ")])])
    st = KaraokeStyle()
    st.ruby.size_pct = 70
    st.layout.alternate_indent = 0
    text, _ = A.build_ass(p, r, st)
    first = next(l for l in _events(text, "KRuby") if "う" in l)
    rw = Measurer(default_family(), True, 88 * 0.7).width("うけたまわ")
    assert _pos(first)[0] - rw / 2 >= st.layout.margin_h - 0.5


# ---------------------------------------------------------------- R3: lines sung at the same time


def _laid(name, s, e):
    return A.LaidLine(line=Line(text=name), chunks=[], start=s, end=e, units=[(s, e)])


@pytest.mark.parametrize("rows", [1, 2, 3])
def test_overlapping_lines_are_all_shown_without_sharing_a_row(rows):
    st = KaraokeStyle()
    st.layout.lines = rows
    lines = [_laid("A", 0, 20000), _laid("B", 1000, 3000), _laid("C", 4000, 6000), _laid("D", 7000, 9000),
             _laid("E", 8000, 12000), _laid("F", 21000, 23000), _laid("G", 23500, 25000)]
    A.schedule(lines, st)
    for ll in lines:
        spans = A.visible_spans(ll, st)
        assert spans and spans[0][0] <= ll.start and spans[-1][1] >= ll.end, ll.line.text
    by_slot: dict[int, list] = {}
    for ll in lines:
        by_slot.setdefault(ll.slot, []).append((ll.show_from, ll.show_to))
    for spans in by_slot.values():
        spans.sort()
        assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:])), spans
    # ordinary alternation is kept for lines that do not overlap
    seq = [_laid(str(i), 10000 + 3000 * i, 12000 + 3000 * i) for i in range(4)]
    A.schedule(seq, st)
    assert [ll.slot for ll in seq] == [i % rows for i in range(4)]


def test_extra_row_is_placed_beyond_the_block_and_warned():
    a = _line([_seg("ずっと", "ずっと"), _seg("一緒", "いっしょ")])
    b = _line([_seg("君", "きみ")])
    p, r = _project([a, b], times={0: (1000, 3000), 1: (4000, 300)})
    st = KaraokeStyle()
    st.layout.lines = 1
    text, warnings = A.build_ass(p, r, st)
    assert any("同时演唱" in w for w in warnings)
    ya = {_pos(l)[1] for l in _events(text, "KMain") if l.endswith(("ず", "っ", "と", "一緒"))}
    yb = {_pos(l)[1] for l in _events(text, "KMain") if l.endswith("君")}
    assert len(ya) == 1 and len(yb) == 1 and yb.pop() < ya.pop()  # above the block (lyrics at the bottom)


# ---------------------------------------------------------------- R4 / L16: title card


def _card_project(translation, **kw):
    p, r = _project([_line([_seg("窓", "まど"), _seg("に", "に")], translation=translation), _line([_seg("君", "きみ")])],
                    t0=1500, **kw)
    p.lyrics.meta.title, p.lyrics.meta.artist = "春の歌", "アーティスト"
    return p, r


def test_title_card_stays_in_its_corner_and_narrows_to_keep_clear():
    st = KaraokeStyle()
    st.info.enabled, st.info.start_ms = True, 0
    st.translation.enabled = True  # along the top, from 0.7 s

    def card(text):
        rows = [l for l in _events(text, "KInfo") if "\\p1" not in l]
        bars = [l for l in _events(text, "KInfo") if "\\p1" in l]
        return rows, bars

    # a translation that reaches toward the card's corner: the card keeps its place and its whole time,
    # and its lines wrap so it ends before the translation begins
    mid = "很长很长的翻译文字" * 3
    p, r = _card_project(mid)
    p.lyrics.meta.title = "ハイファイ☆デイズ (M@STER VERSION) 赤城みりあ ソロ・リミックス"
    text, warnings = A.build_ass(p, r, st)
    rows, bars = card(text)
    trans = _events(text, "KTrans")[0]
    tw = Measurer(default_family(), st.translation.bold, 88 * st.translation.size_pct / 100).width(mid)
    trans_x0 = 960 - tw / 2
    assert all(_pos(b)[1] == 56 and _pos(b)[0] == 56 for b in bars)  # top-left, as set
    assert max(_ms(b.split(",")[2]) for b in bars) == 7000  # its whole time
    for l in rows:  # every row ends before the translation starts
        size = float(re.search(r"\\fs([\d.]+)", l).group(1))
        w = Measurer(default_family(), "\\b1" in l, size).width(l.split("}", 1)[1])
        assert 76 + w < trans_x0, (l, trans_x0)
    assert len({l.split("}", 1)[1] for l in rows}) > 3 and not warnings  # wrapped (title + artist + more rows)
    # a short translation in the middle: nothing to avoid, one row per line
    p, r = _card_project("短")
    text, _ = A.build_ass(p, r, st)
    rows, bars = card(text)
    assert max(_ms(b.split(",")[2]) for b in bars) == 7000 and len({l.split("}", 1)[1] for l in rows}) == 2
    # a translation across the whole top: no narrow card fits beside it; it moves down below it (same corner)
    p, r = _card_project("很长很长的翻译文字" * 6)
    text, warnings = A.build_ass(p, r, st)
    rows, bars = card(text)
    assert bars and all(_pos(b)[0] == 56 and _pos(b)[1] > 40 + 88 * 0.6 for b in bars) and not warnings
    # lyrics along the top where the card is: same corner, below the top row
    st = KaraokeStyle()
    st.info.enabled, st.info.start_ms = True, 0
    st.layout.position, st.layout.margin_v, st.layout.alternate_indent = "top", 40, 0
    p, r = _card_project(None)
    text, warnings = A.build_ass(p, r, st)
    rows, bars = card(text)
    first_row = min(_pos(l)[1] for l in _events(text, "KMain"))
    assert rows and all("\\an7" in l for l in rows) and all(_pos(b)[1] > first_row for b in bars) and not warnings

def test_accent_bar_is_as_tall_as_the_text_and_joint_credits():
    from kara_align.karaoke.info import song_fields

    p, r = _card_project(None)
    p.lyrics.lines.insert(0, Line(text="作词/作曲：山田", kind="meta", sing=False))
    p.lyrics.lines.insert(1, Line(text="词曲 : 李四", kind="meta", sing=False))
    f = song_fields(p)
    assert f["lyricist"] == "山田" and f["composer"] == "山田"
    st = KaraokeStyle()
    st.info.enabled = True
    text, _ = A.build_ass(p, r, st)
    bar = next(l for l in _events(text, "KInfo") if "\\p1" in l)
    height = float(re.search(r"l [\d.]+ ([\d.]+) l 0", bar).group(1))
    title, sub = 56, max(18, 56 * 0.56)
    assert height == pytest.approx(title * 1.18 + sub, abs=0.2)  # to the bottom of the last line, no more


# ---------------------------------------------------------------- R5 / L18: frame size of the burn


@needs_ffmpeg
def test_burn_odd_and_anamorphic_videos(tmp_path):
    from kara_align.karaoke.render import burn, even_size, frame_size

    odd, sar = tmp_path / "odd.mp4", tmp_path / "sar.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=321x241:r=10:d=1",
                    "-c:v", "mpeg4", str(odd)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=720x576:r=10:d=1",
                    "-vf", "setsar=16/15", "-pix_fmt", "yuv420p", str(sar)], check=True)
    assert frame_size(odd, (0, 0)) == (321, 241) and even_size((321, 241)) == (320, 240)
    assert frame_size(sar, (0, 0)) == (768, 576)  # shown with square pixels
    assert frame_size(tmp_path / "missing.mp4", (1, 2)) == (1, 2)
    p, r = _project([_line([_seg("窓", "まど")])], t0=100)
    for src in (odd, sar):
        size = even_size(frame_size(src, (0, 0)))
        text, _ = A.build_ass(p, r, KaraokeStyle(), size=size)
        out = burn(text, tmp_path / f"out-{src.stem}.mp4", size, 800, video=src)
        info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                          "stream=width,height,sample_aspect_ratio", "-of", "json", str(out)],
                                         capture_output=True, text=True).stdout)["streams"][0]
        assert (info["width"], info["height"]) == size and info.get("sample_aspect_ratio", "1:1") in ("1:1", "0:1")
        assert f"PlayResX: {size[0]}" in text and f"PlayResY: {size[1]}" in text


# ---------------------------------------------------------------- R6 / R7: effects


def test_ball_stays_below_the_line_above():
    lines = [_line([_seg("窓", "まど"), _seg("に", "に"), _seg("舞う", "まう")]),
             _line([_seg("真新", "まっさら"), _seg("な", "な"), _seg("教室", "きょうしつ")])]
    p, r = _project(lines, step=400, gap=100)
    st = KaraokeStyle()
    st.effects.kind, st.effects.behind = "ball", False
    st.layout.line_spacing, st.layout.arrangement = 0, "center"
    st.ruby.script, st.ruby.target = "romaji", "all"
    text, _ = A.build_ass(p, r, st)
    upper_bottom = max(_pos(l)[1] for l in _events(text, "KMain") if l.endswith(("窓", "に", "舞", "う")))
    second_start = min(u.start_ms for u in r.units if u.line_id == lines[1].id)
    radius = 88 * 0.1
    for l in _events(text, "KFx"):
        if _ms(l.split(",")[1]) >= second_start - 250:
            ys = [float(v) for v in re.search(r"\\move\([-\d.]+,([-\d.]+),[-\d.]+,([-\d.]+)", l).groups()]
            assert min(ys) - radius >= upper_bottom - 0.5, (ys, upper_bottom)  # below the line box above


def test_effect_copies_sit_on_the_glyphs():
    p, r = _project([_line([_seg("窓", "まど"), _seg("に", "に")])], step=600)
    st = KaraokeStyle()
    st.effects.kind = "pulse"
    text, _ = A.build_ass(p, r, st)
    lyric = next(l for l in _events(text, "KMain") if l.endswith("窓"))
    fx = next(l for l in _events(text, "KFx") if l.endswith("窓"))
    # \an5 at the middle of the line box (its height is the font size) = \an2 at its bottom
    assert "\\an5" in fx and _pos(fx) == pytest.approx((_pos(lyric)[0], _pos(lyric)[1] - 88 / 2), abs=0.11)


@needs_ffmpeg
def test_effect_copy_matches_the_lyric_ink():
    p, r = _project([_line([_seg("窓", "まど")])], step=600)
    st = KaraokeStyle()
    st.effects.kind, st.ruby.enabled, st.timing.fade_in_ms = "ring", False, 0
    st.text.outline = st.text.shadow = 0
    text, _ = A.build_ass(p, r, st)
    head, events = text.split("[Events]\n")
    fmt, *evs = events.strip().split("\n")
    lyric = [e for e in evs if ",KMain," in e]
    fx = next(e for e in evs if ",KFx," in e)
    a, b = fx.split(",KFx,,0,0,0,,", 1)
    tags, body = b[1:].split("}", 1)
    still = a + ",KFx,,0,0,0,,{" + re.sub(r"\\(t\(.*\)|1a[^\\]*|3a[^\\]*|bord[^\\]*|blur[^\\]*)", "", tags) + "}" + body

    def ink(evl):
        img = _png(head + "[Events]\n" + fmt + "\n" + "\n".join(evl) + "\n", 1100)
        ys, xs = np.where(img.max(axis=2) > 100)
        return xs.min(), ys.min(), xs.max(), ys.max()

    assert np.abs(np.array(ink(lyric)) - np.array(ink([still]))).max() <= 1


def test_ruby_effects_follow_the_lyric_sweep(monkeypatch):
    import kara_align.karaoke.effects as E

    seen = []
    real = E.syllable_events
    monkeypatch.setattr(E, "syllable_events", lambda style, syl, k: seen.extend(syl) or real(style, syl, k))
    # 桜 is one lyric piece (1000–1900) under さ く ら (1000–1300–1600–1900)
    p, r = _project([_line([_seg("桜", "さくら")])])
    st = KaraokeStyle()
    st.effects.kind, st.effects.ruby = "pulse", True
    st.ruby.size_pct = 30  # the reading is narrower than the lyric
    A.build_ass(p, r, st)
    own = sorted((s.start, s.x) for s in seen if s.ruby)
    assert [t for t, _ in own] == [1000, 1300, 1600]
    seen.clear()
    st.ruby.sweep = "base"
    A.build_ass(p, r, st)
    base = sorted((s.start, s.x) for s in seen if s.ruby)
    main = next(s for s in seen if not s.ruby)
    left = main.x - main.w / 2
    for (t, x), (_, x_own) in zip(base, own):
        assert x == pytest.approx(x_own)
    # each reading syllable fires when the lyric's sweep reaches its left edge (inside 桜's time)
    widths = [Measurer(default_family(), True, 88 * 0.3).width(c) for c in "さくら"]
    edges = [base[0][1] - widths[0] / 2]
    for w in widths[:-1]:
        edges.append(edges[-1] + w)
    for (t, _), e in zip(base, edges):
        assert t == pytest.approx(1000 + 900 * (e - left) / main.w, abs=15)


# ---------------------------------------------------------------- S7: validation


def test_styles_are_validated_and_stored_ones_clamped(tmp_path):
    bad = {"text": {"size": 100000, "color_sung": "red", "color_unsung": "#fff", "outline": -3,
                    "font": "Arial,0,{\\p1}"},
           "glow": {"color_unsung": "#12}{\\p1", "blur": float("nan"), "strength": 0},
           "layout": {"margin_h": 5000, "line_spacing": -500, "lines": 9},
           "timing": {"hold_ms": -5000, "lead_in_ms": "x", "highlight": "wobble"},
           "info": {"fields": ["title", "bogus"], "accent": "nope"},
           "theme": {"template": "glow", "color": "orange"},
           "ruby": "not an object"}
    st = KaraokeStyle.model_validate(bad)
    assert (st.text.size, st.text.color_sung, st.text.color_unsung, st.text.outline) == (200, "#2F80ED", "#ffffff", 0)
    assert "," not in st.text.font and "{" not in st.text.font and "\\" not in st.text.font
    assert (st.glow.color_unsung, st.glow.blur, st.glow.strength) == ("#FF8AC2", 7.0, 10)
    assert (st.layout.margin_h, st.layout.line_spacing, st.layout.lines) == (600, 0, 3)
    assert (st.timing.hold_ms, st.timing.lead_in_ms, st.timing.highlight) == (0, 2000, "sweep")
    assert st.info.fields == ["title"] and st.info.accent == "" and st.theme is None
    assert st.ruby == KaraokeStyle().ruby
    # a style sent to be saved is refused instead
    with pytest.raises(Exception):
        KaraokeStyle.model_validate({"text": {"color_sung": "red"}}, context={"strict": True})
    with pytest.raises(Exception):
        KaraokeStyle.model_validate({"timing": {"hold_ms": -1}}, context={"strict": True})
    # short colours are written out, also when strict (and by the colour templates)
    assert KaraokeStyle.model_validate({"text": {"color_sung": "#f80"}}, context={"strict": True}).text.color_sung \
        == "#ff8800"
    from kara_align.karaoke.themes import theme_style
    assert theme_style("glow", "#f80", KaraokeStyle(), "#ff0").theme.model_dump() == \
        {"template": "glow", "color": "#FF8800", "secondary": "#FFFF00"}
    # a stored project with such values opens (clamped); saving one through the service is refused
    h = S.create_dir(tmp_path / "proj", "k", "plain")
    data = json.loads((h.dir / "project.json").read_text(encoding="utf-8"))
    data["karaoke"] = bad
    (h.dir / "project.json").write_text(json.dumps(data), encoding="utf-8")
    h2 = S.open_dir(h.dir)
    assert h2.project.karaoke.text.size == 200
    with pytest.raises(S.ServiceError):
        S.set_karaoke_style(h2, {**h2.project.karaoke.model_dump(mode="json"), "text": {"color_sung": "#12}{"}})
    # every colour in the ASS is a colour
    p, r = _project([_line([_seg("窓", "まど")])])
    text, _ = A.build_ass(p, r, st)
    assert "\\p1}" not in text.split("[Events]")[0]
    for c in re.findall(r"\\[13]c([^\\}]*)", text):
        assert re.fullmatch(r"&H[0-9A-F]{6}&", c), c


def test_build_ass_guards_unvalidated_styles():
    p, r = _project([_line([_seg("窓", "まど")], translation="窗"), _line([_seg("君", "きみ")])])
    st = KaraokeStyle()
    st.text.size, st.ruby.size_pct, st.translation.size_pct = 0, -5, 0
    st.layout.margin_h, st.layout.margin_v = 5000, 99999
    st.translation.enabled = True
    st.text.color_sung = "}{\\p1"
    text, _ = A.build_ass(p, r, st)
    assert _events(text, "KMain")
    for l in _events(text, "KMain"):
        x, y = _pos(l)
        assert 0 <= x <= 1920 and 0 <= y <= 1080
    assert A.ass_color("}{\\p1") == A.ass_color("#FFFFFF") and A.bgr_tag("#12345") == "&HFFFFFF&"


def test_saved_style_names_and_broken_files():
    from kara_align.karaoke import styles as ST

    mine = ST.save_style("我的", KaraokeStyle().model_dump(mode="json"))
    with pytest.raises(ST.StyleError):  # renamed to a built-in name
        ST.save_style("默认", KaraokeStyle().model_dump(mode="json"), mine["id"])
    with pytest.raises(ST.StyleError):
        ST.save_style("新的", {**KaraokeStyle().model_dump(mode="json"), "text": {"size": 5000}})
    # a preset stored with values out of range still loads (clamped)
    raw = json.loads(ST._path().read_text(encoding="utf-8"))
    raw[0]["style"]["text"] = {"size": 5000, "color_sung": "blue"}
    ST._path().write_text(json.dumps(raw), encoding="utf-8")
    assert ST.get_style(mine["id"]).text.size == 200
    # two broken files in a row: the first one is not overwritten by the second
    ST._path().write_text("{broken 1", encoding="utf-8")
    ST.list_styles()
    ST._path().write_text("{broken 2", encoding="utf-8")
    ST.list_styles()
    kept = sorted(x.read_text(encoding="utf-8") for x in ST._path().parent.glob("styles.broken*.json"))
    assert kept == ["{broken 1", "{broken 2"]


# ---------------------------------------------------------------- R-L2 / L10: measuring and text


def test_measured_text_is_the_written_text():
    m = Measurer(default_family(), True, 88)
    assert m.width("a\\b{c}") == pytest.approx(m.width("a＼b｛c｝"))
    assert A.escape_text("a\r\nb\u2028c\u2029d\x0be") == "a  b c d e"


@needs_ffmpeg
@pytest.mark.skipif(shutil.which("fc-match") is None and sys.platform != "darwin",
                    reason="the fallback font libass picks is known through fontconfig (or CoreText on macOS)")
def test_characters_the_font_lacks_are_measured_with_the_fallback():
    fam = default_family()
    m = Measurer(fam, False, 52.8)
    from kara_align.karaoke.fonts import _charmap

    have = _charmap(*m._face)
    text = "".join(c for c in "们这说话们这说话" if ord(c) not in have)
    if not text:
        pytest.skip(f"{fam} has these characters")
    ass = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 400\nWrapStyle: 2\n\n[V4+ Styles]\n"
           "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
           "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
           "MarginR, MarginV, Encoding\n"
           f"Style: T,{fam},52.8,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n\n"
           "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")

    def marker_x(t):
        img = _png(ass + f"Dialogue: 0,0:00:00.00,0:00:05.00,T,,0,0,0,,{{\\an7\\pos(50,50)}}{t}{{\\1c&H0000FF&}}I\n",
                   1000, (1920, 400))
        return np.where(((img[..., 0] > 150) & (img[..., 1] < 80)).any(axis=0))[0].min()

    drawn = marker_x(text) - marker_x("")
    assert abs(drawn - m.width(text)) <= 3, (drawn, m.width(text))


def test_ambiguous_okurigana_is_not_split():
    def split(s, reading):
        return [x for x, _ in A._split_affixes(_seg(s, reading))]

    assert split("物の怪", "もののけ") == ["物の怪"]  # も|の|のけ or もの|の|け
    assert split("笑い合え", "わらいあえ") == ["笑", "い", "合", "え"]
    assert split("思い出", "おもいで") == ["思", "い", "出"]
    assert split("田舎の子", "いなかのこ") == ["田舎", "の", "子"]


def test_no_font_warning_for_parts_that_are_off():
    p, r = _project([_line([_seg("窓", "まど")], translation="窗")])
    st = KaraokeStyle()
    st.translation.font = st.ruby.font = "No Such Font 123"
    st.translation.enabled, st.ruby.enabled = False, False
    assert not any("字体" in w for w in A.build_ass(p, r, st)[1])
    st.translation.enabled = True
    assert any("No Such Font 123" in w for w in A.build_ass(p, r, st)[1])


def test_back_to_back_lines_never_fade_while_sung():
    p, r = _project([_line([_seg("窓", "まど"), _seg("に", "に")]), _line([_seg("君", "きみ")])], gap=100)
    st = KaraokeStyle()
    st.layout.lines = 1
    st.timing.advance_ms = 0
    st.timing.fade_in_ms = st.timing.fade_out_ms = 800
    text, _ = A.build_ass(p, r, st)
    sung = {}
    for u in r.units:
        a, b = sung.get(u.line_id, (u.start_ms, u.end_ms))
        sung[u.line_id] = (min(a, u.start_ms), max(b, u.end_ms))
    for l in _events(text, "KMain"):
        t0, t1 = _ms(l.split(",")[1]), _ms(l.split(",")[2])
        fi, fo = map(int, re.search(r"\\fad\((\d+),(\d+)\)", l).groups()) if "\\fad" in l else (0, 0)
        first = min(s for s, _ in sung.values() if t0 <= s < t1)
        last = max(e for s, e in sung.values() if t0 <= s < t1)
        assert t0 + fi <= first + 10 and t1 - fo >= last - 10, l


def test_lrc_meta_tags_stay_one_tag():
    from kara_align.project.exports import _meta_tags

    p = Project(name="t")
    p.lyrics.meta.title, p.lyrics.meta.artist, p.lyrics.meta.album = "A]B\nC", "[x]", "  "
    assert _meta_tags(p.lyrics) == ["[ti:AB C]", "[ar:x]"]


def test_font_names_are_literal_in_fontconfig_patterns():
    from kara_align.karaoke.fonts import fc_escape, resolve

    assert fc_escape("Foo-Bar:Baz,Qux") == "Foo\\-Bar\\:Baz\\,Qux"
    resolve.cache_clear()
    path, index = resolve("M+ 1p-Bold:italic", True)  # no crash; a real file
    assert path and index >= 0


def test_title_card_comes_again_at_the_end_of_the_song():
    from kara_align.models import AudioAsset

    st = KaraokeStyle()
    st.info.enabled, st.info.start_ms = True, 0
    st.info.duration_ms, st.info.outro_duration_ms = 3000, 5000
    p, r = _card_project(None)
    p.audio = [AudioAsset.model_construct(role="original", duration_ms=30000, id="a", sha256="s")]

    def cards(style):
        text, warnings = A.build_ass(p, r, style)
        spans = []  # the accent bar's showings (growing and shrinking events joined)
        for a, b in sorted({(_ms(l.split(",")[1]), _ms(l.split(",")[2])) for l in _events(text, "KInfo") if "\\p1" in l}):
            if spans and spans[-1][1] == a:
                spans[-1] = (spans[-1][0], b)
            else:
                spans.append((a, b))
        return spans, warnings

    spans, _ = cards(st)
    assert spans == [(0, 3000), (25000, 30000)]  # the opening card, and the same card until the song ends
    # leaving mirrors coming in: the lines slide back out toward the edge (the last line first), then
    # the bar shrinks up
    text, _ = A.build_ass(p, r, st)
    outs = [l for l in _events(text, "KInfo") if ",0:00:0" in l and "\\move" in l and "\\fad(0," in l]
    assert len(outs) == 2
    x = [re.search(r"\\move\(([\d.]+),[\d.]+,([\d.]+)", l).groups() for l in outs]
    assert all(float(b) < float(a) for a, b in x)  # top-left card: back toward the left edge
    title_out, artist_out = sorted(outs, key=lambda l: "\\fs56.0" not in l)
    assert _ms(artist_out.split(",")[2]) < _ms(title_out.split(",")[2]) <= 3000 - 200
    assert any("\\p1" in l and "\\t(0,380,1.6,\\fscy0)" in l and l.split(",")[2] == "0:00:03.00" for l in _events(text, "KInfo"))
    st.info.outro = False
    assert cards(st)[0] == [(0, 3000)]
    # lyrics along the top at the end, right where the card is (no narrow card fits beside them): it
    # comes in after they have gone, in its corner
    st = KaraokeStyle()
    st.info.enabled, st.info.start_ms, st.info.duration_ms, st.info.outro_duration_ms = True, 0, 2000, 6000
    st.layout.position, st.layout.margin_v, st.layout.alternate_indent = "top", 40, 0
    st.timing.advance_ms = 0
    p.audio = [AudioAsset.model_construct(role="original", duration_ms=9000, id="a", sha256="s")]
    text, _ = A.build_ass(p, r, st)
    # (only what is in its corner counts: a lyric on the right side does not hold it back)
    corner_gone = max(_ms(l.split(",")[2]) for l in _events(text, "KMain") if _pos(l)[0] < 960)
    spans, _ = cards(st)
    assert spans[-1] == (corner_gone + 200, 9000) and corner_gone + 200 > 9000 - 6000
    # an ending card that would run into the opening one is left out
    p.audio = [AudioAsset.model_construct(role="original", duration_ms=5000, id="a", sha256="s")]
    assert all(e < 5000 for _, e in cards(st)[0])
    # the card is off: no ending card either
    st.info.enabled = False
    assert cards(st)[0] == []


# --- long lines wrap; what cannot wrap reaches toward the edges ------------------------------------

def _long_line(translation=None):
    # 18 kana words, a full-width space in the middle: wider than the room between the margins
    words = [_seg(w, w) for w in ("きみと", "あるいた", "そらの", "したで")] + [_seg("　")] + \
        [_seg(w, w) for w in ("ゆめを", "みていた", "あのひの", "ことを")]
    return _line(words, translation=translation)


def _main_rows(text):
    """Visible text of each lyric event group (KMain events that start together form one row)."""
    rows = {}
    for l in _events(text, "KMain"):
        if "\\iclip" in l:
            continue
        rows.setdefault((l.split(",")[1], _pos(l)[1]), []).append(re.sub(r"\{[^}]*\}", "", l.split(",", 9)[9]))
    return ["".join(v) for _, v in sorted(rows.items())]


def test_a_long_line_wraps_at_the_space_and_keeps_one_translation():
    st = KaraokeStyle()
    st.ruby.enabled = False
    st.translation.enabled = True
    st.text.size = 110
    p, r = _project([_long_line("走过天空之下 梦见那一天的事")])
    text, warnings = A.build_ass(p, r, st)
    rows = _main_rows(text)
    assert rows == ["きみとあるいたそらのしたで", "ゆめをみていたあのひのことを"]  # broken at the space, dropped
    assert not any("过长" in w for w in warnings)
    trans = _events(text, "KTrans")
    assert len(trans) == 1 and trans[0].endswith("走过天空之下 梦见那一天的事")
    # shown until the second piece is sung (plus the hold)
    last_end = max(u.end_ms for u in r.units)
    assert _ms(trans[0].split(",")[2]) >= last_end
    st.layout.wrap = "off"
    text, warnings = A.build_ass(p, r, st)
    assert len(_main_rows(text)) == 1


def test_the_ai_suggested_break_is_used_when_asked():
    st = KaraokeStyle()
    st.ruby.enabled = False
    st.text.size = 110
    ln = _long_line()
    ln.segments[2].wrap_before = True  # before そらの
    p, r = _project([ln])
    assert _main_rows(A.build_ass(p, r, st)[0])[0] == "きみとあるいたそらのしたで"  # auto: the space
    st.layout.wrap = "ai"
    assert _main_rows(A.build_ass(p, r, st)[0])[0] == "きみとあるいた"


def test_a_line_that_cannot_wrap_reaches_toward_the_edge_before_it_shrinks():
    st = KaraokeStyle()
    st.ruby.enabled = False
    st.layout.lines = 1
    st.layout.margin_h = 240
    st.text.size = 125
    p, r = _project([_line([_seg("あいうえおかきくけこさしすせそたち", "あいうえおかきくけこさしすせそたち")])])
    text, warnings = A.build_ass(p, r, st)
    w = Measurer(default_family(), st.text.bold, 125).width("あいうえおかきくけこさしすせそたち")
    assert 1920 - 2 * 240 < w < 1920 - 2 * 50  # wider than the margins allow, not than the edges
    assert not any("过长" in x for x in warnings)  # not shrunk: it uses the room out to 50 px
    xs = [_pos(l)[0] for l in _events(text, "KMain")]
    assert min(xs) > 50 and max(xs) < 1920 - 50


def test_long_titles_wrap_instead_of_being_squeezed():
    from kara_align.karaoke.info import wrap_text

    st = KaraokeStyle()
    st.info.enabled, st.info.start_ms = True, 0
    p, r = _card_project(None)
    p.lyrics.meta.title = "ハイファイ☆デイズ (M@STER VERSION) 赤城みりあ ソロ・リミックス"
    text, _ = A.build_ass(p, r, st)
    card = [l for l in _events(text, "KInfo") if "\\p1" not in l]
    assert card and not any("\\fscx" in l for l in card)  # no squeezed rows
    texts = list(dict.fromkeys(l.split("}", 1)[1] for l in card))
    # not inside the brackets (where the row breaks depends on the font: Hiragino or the bundled Noto Sans CJK)
    assert texts[0].startswith("ハイファイ☆デイズ") and any("(M@STER VERSION)" in t for t in texts)
    # rows break outside brackets where they can
    w = lambda s: len(s) * 40  # noqa: E731
    assert wrap_text("ショコラ・ティアラ ～For Miria rearrange MIX～", w, 1100) == ["ショコラ・ティアラ", "～For Miria rearrange MIX～"]
