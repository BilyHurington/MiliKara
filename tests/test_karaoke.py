"""Karaoke subtitles: chunking, ruby, timing, schedule, ASS, rendering."""

import io
import re
import shutil

import numpy as np
import pytest
import soundfile as sf

from kara_align import service as S
from kara_align.align.backends.fake import ScriptedBackend
from kara_align.karaoke import ass as A
from kara_align.karaoke.fonts import Measurer, default_family
from kara_align.models import KaraokeEffects, KaraokeStyle, Line, Segment, Unit
from kara_align.reading.prepare import units_from_spec

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffprobe") is None, reason="needs ffmpeg with libass")

# 窓に舞う桜: まど に まう さくら
SCRIPT = [("ma", 1000, 1200), ("do", 1200, 1400), ("ni", 1400, 1700), ("ma", 1800, 2000), ("u", 2000, 2200),
          ("sa", 2300, 2500), ("ku", 2500, 2700), ("ra", 2700, 3000),
          ("ki", 5000, 5200), ("mi", 5200, 5400)]


def _seg(surface, reading, lang="ja"):
    units = units_from_spec(reading, None, lang) if reading else []
    return Segment(surface=surface, reading=reading, units=units, reading_source="ai" if reading else "none")


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("KARA_ALIGN_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(ScriptedBackend, "default_script", SCRIPT)


def _project(tmp_path):
    h = S.create_dir(tmp_path / "proj", "k", "plain")
    S.update_settings(h, config={"backend": "scripted"})
    pv = S.parse_lyrics(h, "窓に舞う桜\n君\n", origin="paste")
    S.apply_lyrics(h, pv["preview_id"])
    l1, l2 = h.project.lyrics.lines
    l1.segments = [_seg("窓", "まど"), _seg("に", "に"), _seg("舞う", "まう"), _seg("桜", "さくら")]
    l2.segments = [_seg("君", "きみ")]
    h.save()
    x = (np.random.default_rng(0).standard_normal(22050 * 7) * 0.05).astype(np.float32)
    sf.write(tmp_path / "s.wav", x, 22050)
    S.add_audio(h, tmp_path / "s.wav", "original")
    S.run_align(h)
    return h


# ---------------------------------------------------------------- pure helpers


def test_ass_color_and_time():
    assert A.ass_color("#2F80ED") == "&H00ED802F"
    assert A.ass_color("#000000", 50) == "&H80000000"
    assert A.ass_time(61234) == "0:01:01.23"


def test_okurigana_split_keeps_whole_units():
    seg = _seg("舞う", "まう")
    pieces = A._split_affixes(seg)
    assert [(s, [u.reading for u in us]) for s, us in pieces] == [("舞", ["ま"]), ("う", ["う"])]
    # prefix kana and a reading that cannot be split on a unit boundary
    assert [s for s, _ in A._split_affixes(_seg("お茶", "おちゃ"))] == ["お", "茶"]
    assert [s for s, _ in A._split_affixes(_seg("真新", "まっさら"))] == ["真新"]


def _line():
    return Line(text="窓に舞う桜", segments=[_seg("窓", "まど"), _seg("に", "に"), _seg("舞う", "まう"), _seg("桜", "さくら")])


def _times(line, t0=1000, step=200):
    out, t = {}, t0
    for u in line.units():
        out[u.id] = (t, t + step)
        t += step
    return out


def test_chunks_ruby_targets_and_scripts():
    line = _line()
    st = KaraokeStyle()
    ch = A.build_chunks(line, _times(line), st, {})
    assert [c.base_text for c in ch] == ["窓", "に", "舞", "う", "桜"]
    assert [c.ruby_text for c in ch] == ["まど", "", "ま", "", "さくら"]
    st.ruby.script = "katakana"
    assert [c.ruby_text for c in A.build_chunks(line, _times(line), st, {})] == ["マド", "", "マ", "", "サクラ"]
    st.ruby.target = "all"  # kana now get katakana ruby too
    assert [c.ruby_text for c in A.build_chunks(line, _times(line), st, {})] == ["マド", "ニ", "マ", "ウ", "サクラ"]
    st.ruby.script, st.ruby.target = "hiragana", "all"  # hiragana over hiragana is suppressed
    assert [c.ruby_text for c in A.build_chunks(line, _times(line), st, {})] == ["まど", "", "ま", "", "さくら"]
    st.ruby.enabled = False
    assert all(not c.ruby for c in A.build_chunks(line, _times(line), st, {}))


def test_digits_get_ruby_like_kanji():
    from kara_align.reading.japanese import rule_segments

    line = Line(text="24時間", segments=rule_segments("24時間"))
    ch = A.build_chunks(line, _times(line), KaraokeStyle(), {})  # 仅汉字: digits too
    assert [(c.base_text, c.ruby_text) for c in ch] == [("24", "にじゅうよん"), ("時間", "じかん")]
    assert ch[0].base[0].start == 1000 and ch[0].base[0].end == 2000  # swept over its five units


def test_karaoke_tags_follow_unit_times_exactly():
    parts = [A.Part("ま", 1000, 1210), A.Part("ど", 1300, 1400)]
    tags = A._karaoke(parts, 500, "kf")
    assert tags == "{\\k50}{\\kf21}ま{\\k9}{\\kf10}ど"
    cs = sum(int(x) for x in re.findall(r"\\kf?(\d+)", tags))
    assert cs * 10 == 1400 - 500


def test_untimed_parts_never_invent_duration():
    line = Line(text="あ、い", segments=[_seg("あ", "あ"), _seg("、", None), _seg("い", "い")])
    a, i = line.segments[0].units[0].id, line.segments[2].units[0].id
    ch = A.build_chunks(line, {a: (1000, 1200), i: (1500, 1700)}, KaraokeStyle(), {})
    comma = ch[1].base[0]
    assert comma.start == comma.end == 1200  # zero-length, at the neighbour boundary


def _laid(starts):
    return [A.LaidLine(line=Line(text=str(s)), chunks=[], start=s, end=s + 2000) for s in starts]


def test_schedule_two_slots_early_show_without_overlap():
    st = KaraokeStyle()
    lines = _laid([10000, 13000, 16000, 19000])
    A.schedule(lines, st)
    assert [ll.slot for ll in lines] == [0, 1, 0, 1]
    assert lines[1].show_from == 13000 - 4000  # slot free: shown early (capped at 4 s)
    for a, b in ((lines[0], lines[2]), (lines[1], lines[3])):
        assert a.show_to <= b.show_from  # same slot never overlaps
    for ll in lines:
        assert ll.start - ll.show_from >= 200


def test_schedule_without_early_show_uses_lead_in():
    st = KaraokeStyle()
    st.timing.early_show = False
    lines = _laid([10000, 30000])
    A.schedule(lines, st)
    assert lines[1].show_from == 30000 - st.timing.lead_in_ms


# ---------------------------------------------------------------- whole ASS


def test_build_ass_from_alignment(tmp_path):
    h = _project(tmp_path)
    text, warnings = S.karaoke_ass(h)
    assert "PlayResX: 1920" in text and "[Events]" in text
    dialogues = [l for l in text.splitlines() if l.startswith("Dialogue")]
    ruby = [l for l in dialogues if ",KRuby," in l]
    assert any(l.endswith("{\\kf20}ま{\\kf20}ど") for l in ruby)
    assert any("{\\kf20}き{\\kf20}み" in l for l in ruby)
    main = [l for l in dialogues if ",KMain," in l]
    assert any(l.endswith("窓") for l in main) and any(l.endswith("う") for l in main)
    # 2 lines alternate: first left half, second right half
    xs = [float(re.search(r"\\pos\(([\d.]+),", l).group(1)) for l in main]
    assert min(xs) < 960 < max(xs)
    # export route gives the same file
    out = S.export(h, "karaoke-ass")
    assert out.filename == "karaoke.ass" and out.content == text


def test_style_is_saved_and_validated(tmp_path):
    h = _project(tmp_path)
    st = h.project.karaoke.model_dump()
    st["layout"]["lines"] = 1
    st["text"]["color_sung"] = "#FF0000"
    S.set_karaoke_style(h, st)
    assert S.open_dir(h.dir).project.karaoke.layout.lines == 1
    st["layout"]["lines"] = 9
    with pytest.raises(S.ServiceError):
        S.set_karaoke_style(h, st)


def test_saved_styles_library():
    from kara_align.karaoke import styles as ST

    lib = ST.list_styles()
    assert [x["name"] for x in lib] == ["默认", "暖阳"] and lib[0]["builtin"] and lib[1]["builtin"]
    assert lib[0]["style"]["text"]["color_sung"] == "#ED35B3" and lib[0]["style"]["timing"]["fade_in_ms"] == 200
    warm = lib[1]["style"]  # yellow / orange, with translations and the title card
    assert (warm["text"]["color_sung"], warm["glow"]["enabled"], warm["translation"]["enabled"], warm["info"]["enabled"]) == \
        ("#FF8A1E", True, True, True)
    assert warm["preset"] == "暖阳" and warm["timing"]["lead_in_ms"] == 4000
    mine = KaraokeStyle()
    mine.glow.enabled = True
    a = ST.save_style("荧光", mine.model_dump(mode="json"))
    assert a["style"]["preset"] == "荧光" and a["style"]["glow"]["enabled"]
    again = ST.save_style("荧光", KaraokeStyle().model_dump(mode="json"))  # same name: replaced, not duplicated
    assert again["id"] == a["id"] and [x["name"] for x in ST.list_styles()] == ["默认", "暖阳", "荧光"]
    assert not ST.get_style(a["id"]).glow.enabled
    with pytest.raises(ST.StyleError):
        ST.save_style("默认", mine.model_dump(mode="json"))
    with pytest.raises(ST.StyleError):
        ST.save_style("暖阳", mine.model_dump(mode="json"))
    with pytest.raises(ST.StyleError):
        ST.delete_style("default")
    with pytest.raises(ST.StyleError):
        ST.delete_style("warm")
    ST.delete_style(a["id"])
    assert [x["name"] for x in ST.list_styles()] == ["默认", "暖阳"]


def test_v1_styles_migrate():
    old = {"version": 1, "preset": "sakura", "layout": {"lines": 1, "show_translation": True,
                                                       "translation_position": "block", "translation_size_pct": 50},
           "text": {"color_unsung": "#EEEEEE", "outline_color": "#112233"}}
    s = KaraokeStyle.model_validate(old)
    assert s.version == 2 and s.layout.lines == 1 and s.preset == ""
    assert (s.translation.enabled, s.translation.position, s.translation.size_pct) == (True, "block", 50)
    assert (s.translation.color, s.translation.outline_color) == ("#EEEEEE", "#112233")
    assert s.timing.fade_in_ms == 200 and not s.glow.enabled and s.effects.kind == "none"
    assert KaraokeEffects.model_validate({"particles": "sakura", "density": 40}).kind == "petals"
    assert KaraokeEffects.model_validate({"particles": "snow"}).kind == "none"
    assert KaraokeEffects.model_validate({"kind": "hearts"}).behind  # older styles: behind the text


# ---------------------------------------------------------------- rendering


@needs_ffmpeg
def test_measurement_matches_libass_rendering(tmp_path):
    """Our layout widths must agree with what libass draws."""
    from PIL import Image

    from kara_align.karaoke.render import preview_png

    fam = default_family()
    size = 88
    text = "窓に舞う桜きみと歩いた"
    ass = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nWrapStyle: 2\n\n[V4+ Styles]\n"
           "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
           "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
           "MarginR, MarginV, Encoding\n"
           f"Style: T,{fam},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n\n"
           "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
           f"Dialogue: 0,0:00:00.00,0:00:05.00,T,,0,0,0,,{{\\pos(100,300)}}{text}\n")
    img = np.asarray(Image.open(io.BytesIO(preview_png(ass, 1000, (1920, 1080)))).convert("L"))
    cols = np.where(img.max(axis=0) > 60)[0]
    ink = cols.max() - cols.min()
    predicted = Measurer(fam, True, size).width(text)
    assert abs(ink - predicted) / predicted < 0.05, (ink, predicted)


@needs_ffmpeg
def test_preview_and_burn(tmp_path):
    import subprocess

    from PIL import Image

    h = _project(tmp_path)
    png = S.karaoke_preview(h, 1500)
    img = Image.open(io.BytesIO(png))
    assert img.size == (1920, 1080)
    arr = np.asarray(img.convert("RGB")).astype(int)
    assert arr[:540].max() < 30  # top half black (subtitles at the bottom)
    sung = (arr[..., 2] > 150) & (arr[..., 0] < 120)  # blue = already sung
    assert sung.sum() > 50
    before = S.karaoke_preview(h, 900)  # nothing sung yet
    arr0 = np.asarray(Image.open(io.BytesIO(before)).convert("RGB")).astype(int)
    assert ((arr0[..., 2] > 150) & (arr0[..., 0] < 120)).sum() < sung.sum()

    out = S.karaoke_burn(h, background="black", audio="original")
    path = h.dir / "exports" / out["filename"]
    info = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height",
                           "-show_entries", "format=duration", "-of", "json", str(path)], capture_output=True, text=True)
    assert '"video"' in info.stdout and '"audio"' in info.stdout and '"width": 1920' in info.stdout


class _FixedMeasurer:
    """1 lyric char = 100 px, 1 ruby char = 45 px."""

    def __init__(self, per_char):
        self.per_char = per_char

    def width(self, text):
        return len(text) * self.per_char


def test_ruby_overhangs_kana_but_never_another_reading():
    main, ruby = _FixedMeasurer(100), _FixedMeasurer(45)
    mk = lambda base, rb="": A.Chunk([A.Part(base, 0, 1)], [A.Part(rb, 0, 1)] if rb else [])
    # 新(あたら) between kana: 135 px of ruby over 100 px may overhang the kana
    w = A.chunk_widths([mk("は"), mk("新", "あたら"), mk("しい")], main, ruby, 45, "widen")
    assert w[1] == 100
    # two kanji with readings side by side: widen so readings do not collide
    w = A.chunk_widths([mk("新", "あたら"), mk("制", "せいふく")], main, ruby, 45, "widen")
    assert w[0] > 100 and w[1] > 100
    # at the start of a line the ruby may overhang into the margin
    assert A.chunk_widths([mk("新", "あたら"), mk("しい")], main, ruby, 45, "widen")[0] == 100
    # overflow never widens
    assert A.chunk_widths([mk("制", "せいふく")], main, ruby, 45, "overflow") == [100]


def _main_x(text):
    xs = [float(re.search(r"\\pos\(([\d.]+),", l).group(1)) for l in text.splitlines()
          if l.startswith("Dialogue") and ",KMain," in l]
    return min(xs), max(xs)


def test_alternate_indent_moves_short_lines_toward_centre(tmp_path):
    h = _project(tmp_path)
    st = h.project.karaoke.model_copy(deep=True)
    st.layout.alternate_indent = 0
    lo0, hi0 = _main_x(A.build_ass(h.project, h.project.result(), st)[0])
    st.layout.alternate_indent = 240
    lo1, hi1 = _main_x(A.build_ass(h.project, h.project.result(), st)[0])
    assert lo1 - lo0 == pytest.approx(240, abs=0.2)  # upper (left) line moved right
    assert hi0 - hi1 == pytest.approx(240, abs=0.2)  # lower (right) line moved left
    st.layout.arrangement = "center"  # indent only applies to alternating lines
    lo2, _ = _main_x(A.build_ass(h.project, h.project.result(), st)[0])
    st.layout.alternate_indent = 0
    assert _main_x(A.build_ass(h.project, h.project.result(), st)[0])[0] == lo2


def test_long_line_slides_back_instead_of_shrinking(tmp_path):
    h = _project(tmp_path)
    st = h.project.karaoke.model_copy(deep=True)
    st.layout.alternate_indent = 5000  # far more than any line has room for
    text, warnings = A.build_ass(h.project, h.project.result(), st)
    assert not any("缩小" in w for w in warnings)  # indent never causes shrinking
    lo, hi = _main_x(text)
    assert 140 < lo and hi < 1920 - 140  # still inside the margins


def test_line_is_hidden_during_a_long_pause_inside_it():
    st = KaraokeStyle()
    line = _line()
    times = _times(line)
    last = line.units()[-1].id
    times[last] = (30000, 30400)  # the last syllable comes after a 27 s pause
    ll = A.LaidLine(line, A.build_chunks(line, times, st, {}), 1000, 30400, units=sorted(times.values()))
    A.schedule([ll], st)
    spans = A.visible_spans(ll, st)
    assert spans == [(ll.show_from, 2400 + st.timing.hold_ms), (30000 - st.timing.lead_in_ms, 30400 + st.timing.hold_ms)]
    # an ordinary breath inside a line changes nothing
    times[last] = (4000, 4300)
    ll2 = A.LaidLine(line, A.build_chunks(line, times, st, {}), 1000, 4300, units=sorted(times.values()))
    A.schedule([ll2], st)
    assert A.visible_spans(ll2, st) == [(ll2.show_from, ll2.show_to)]


def test_ass_hides_line_across_interlude(tmp_path):
    h = _project(tmp_path)
    h.project.karaoke.timing.advance_ms = 0  # exact times below
    r = h.project.result()
    last = [u for u in r.units if u.line_id == h.project.lyrics.lines[0].id][-1]
    last.start_ms, last.end_ms = 40000, 40300
    text, warnings = S.karaoke_ass(h)
    assert any("停顿" in w for w in warnings)
    ruby = [l for l in text.splitlines() if l.startswith("Dialogue") and ",KRuby," in l and "さ" in l]
    assert [l.split(",")[1:3] for l in ruby] == [["0:00:00.00", "0:00:03.20"], ["0:00:39.00", "0:00:40.80"]]
    # the second event starts with the earlier syllables already sung
    assert ruby[1].endswith("{\\kf0}さ{\\kf0}く{\\k100}{\\kf30}ら")


def test_burn_reduced_vocals_uses_its_own_level(tmp_path, monkeypatch):
    import kara_align.karaoke.render as R

    h = _project(tmp_path)
    x = (np.random.default_rng(1).standard_normal(22050 * 7) * 0.05).astype(np.float32)
    for role in ("vocals", "instrumental"):
        sf.write(tmp_path / f"{role}.wav", x, 22050)
        S.add_audio(h, tmp_path / f"{role}.wav", role)
    h.project.mix.vocal_keep_pct = 80.0  # the Export page's setting
    seen = {}

    def fake_burn(text, out, size, duration_ms, *, audio=None, **kw):
        seen["audio"] = audio
        seen["peak"] = float(np.abs(sf.read(str(audio))[0]).max())
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x")

    monkeypatch.setattr(R, "burn", fake_burn)
    S.set_karaoke_style(h, {**h.project.karaoke.model_dump(mode="json"), "output": {"vocal_keep_pct": 30}})
    out = S.karaoke_burn(h, background="black", audio="mix")
    assert re.fullmatch(r"k-karaoke-vocal30-\d{8}-\d{6}\.mp4", out["filename"])  # the karaoke page's own level
    again = S.karaoke_burn(h, background="black", audio="mix")["filename"]
    assert again != out["filename"] and (h.dir / "exports" / out["filename"]).exists()  # never overwritten
    assert "-karaoke-vocal0-" in S.karaoke_burn(h, background="black", audio="mix", vocal_keep_pct=0)["filename"]
    assert h.project.mix.vocal_keep_pct == 80.0  # Export page settings untouched
    with pytest.raises(S.ServiceError):
        S.karaoke_burn(h, background="black", audio="mix", vocal_keep_pct=150)


def _dialogue_times(text, style_name="KMain"):
    return [l.split(",")[1:3] for l in text.splitlines() if l.startswith("Dialogue") and f",{style_name}," in l]


def _ms(t):
    h, m, s = t.split(":")
    return round((int(h) * 3600 + int(m) * 60 + float(s)) * 1000)


def test_advance_shows_everything_earlier_and_lrc_exports_follow(tmp_path):
    h = _project(tmp_path)
    assert h.project.karaoke.timing.advance_ms == 150  # on by default
    h.project.karaoke.timing.advance_ms = 0
    plain, _ = S.karaoke_ass(h)
    lrc0 = S.export(h, "lrc-unit").content
    st = h.project.karaoke.model_copy(deep=True)
    st.timing.advance_ms = 150
    S.set_karaoke_style(h, st.model_dump(mode="json"))
    early, _ = S.karaoke_ass(h)
    a, b = _dialogue_times(plain), _dialogue_times(early)
    assert len(a) == len(b)
    assert all(_ms(x[0]) - _ms(y[0]) in (150, 0) and _ms(x[1]) - _ms(y[1]) == 150 for x, y in zip(a, b))
    assert any(_ms(x[0]) - _ms(y[0]) == 150 for x, y in zip(a, b))
    # \k durations are relative to each event, so the highlight moves with it; an event cut at 0:00
    # (it would start before the video) has its lead-in \k shortened by the same 150 ms
    p0 = [l.split(",,", 1)[1] for l in plain.splitlines() if ",KRuby," in l][0]
    e0 = [l.split(",,", 1)[1] for l in early.splitlines() if ",KRuby," in l][0]
    k_p, k_e = (int(re.search(r"\{\\k(\d+)\}", x).group(1)) for x in (p0, e0))
    assert k_p - k_e == 15 and re.sub(r"\{\\k\d+\}", "", p0, count=1) == re.sub(r"\{\\k\d+\}", "", e0, count=1)
    out = S.export(h, "lrc-unit")
    assert out.content != lrc0 and "[00:00.85]" in out.content  # first unit sung at 1.000 s
    assert any("提前 150 ms" in w for w in out.warnings)
    assert "\"start_ms\": 1000" in S.export(h, "alignment").content  # data keeps the real time


def test_translation_in_a_font_lacking_its_characters(tmp_path, monkeypatch):
    """Without a known fallback for Chinese characters (Windows, Linux), a translation is not drawn in a
    Japanese font that lacks many of them (libass would fill those in at another font's scale)."""
    from kara_align.karaoke import ass, fonts

    monkeypatch.setattr(ass, "system_han_fallback", lambda: False)
    h = _project(tmp_path)
    l1, l2 = h.project.lyrics.lines
    l1.translation, l2.translation = "我们这样说话", "谢谢你陪在我身边"
    st = h.project.karaoke.model_copy(deep=True)
    st.translation.enabled = True
    lyric = fonts.default_family()
    if not fonts.lacking(lyric, st.translation.bold, "我们这样说话谢谢你陪在我身边"):
        pytest.skip(f"{lyric} has these characters")
    better = fonts.covering_family("我们这样说话谢谢你陪在我身边", st.translation.bold, fonts.HAN_FAMILIES)
    if better is None:
        pytest.skip("no Chinese font here")

    def trans_font(text: str) -> str:
        return [ln for ln in text.splitlines() if ln.startswith("Style: KTrans,")][0].split(",")[1]

    S.set_karaoke_style(h, st.model_dump(mode="json"))  # translation font "" = the lyric font
    text, warnings = S.karaoke_ass(h)
    assert trans_font(text) == better and any(f"翻译改用 {better}" in w for w in warnings)
    st.translation.font = "No Such Font Anywhere"  # (the built-in 暖阳 names a macOS font)
    S.set_karaoke_style(h, st.model_dump(mode="json"))
    assert trans_font(S.karaoke_ass(h)[0]) == better
    st.translation.font = lyric  # chosen on purpose: kept, with a hint
    S.set_karaoke_style(h, st.model_dump(mode="json"))
    text, warnings = S.karaoke_ass(h)
    assert trans_font(text) == lyric and any("换一个中文字体" in w for w in warnings)
    monkeypatch.setattr(ass, "system_han_fallback", lambda: True)  # macOS: as before
    st.translation.font = ""
    S.set_karaoke_style(h, st.model_dump(mode="json"))
    assert trans_font(S.karaoke_ass(h)[0]) == lyric


def test_translation_positions(tmp_path):
    h = _project(tmp_path)
    l1, l2 = h.project.lyrics.lines
    l1.translation, l2.translation = "樱花在窗边飞舞", "你"
    st = h.project.karaoke.model_copy(deep=True)
    st.translation.enabled = True
    for pos in ("opposite", "block", "line"):
        st.translation.position = pos
        S.set_karaoke_style(h, st.model_dump(mode="json"))
        text, _ = S.karaoke_ass(h)
        tr = [l for l in text.splitlines() if ",KTrans," in l]
        assert len(tr) == 2, pos
        y = [float(re.search(r"\\pos\([\d.]+,([\d.]+)\)", l).group(1)) for l in tr]
        if pos == "opposite":  # top of the frame, one at a time, following the singing
            assert all("\\an8" in l for l in tr) and all(v == st.layout.margin_v for v in y)
            (s1, e1), (s2, e2) = [(_ms(a), _ms(b)) for a, b in _dialogue_times(text, "KTrans")]
            assert e1 <= s2 and s1 <= 1000 <= e1 and s2 <= 5000 <= e2
        elif pos == "block":  # just above the lyric block
            assert all("\\an2" in l for l in tr) and all(540 < v < 1080 - st.layout.margin_v - 200 for v in y)
        else:  # under each lyric line, inside the block
            assert all(v > 700 for v in y) and len(set(y)) == 2  # each under its own line
    st.translation.enabled = False
    S.set_karaoke_style(h, st.model_dump(mode="json"))
    assert ",KTrans," not in S.karaoke_ass(h)[0].split("[Events]")[1]


def test_fades_glow_layers_and_syllable_effects(tmp_path):
    h = _project(tmp_path)
    st = h.project.karaoke.model_copy(deep=True)
    text, _ = S.karaoke_ass(h)
    main = [l for l in text.splitlines() if ",KMain," in l]
    assert all("\\fad(200,200)" in l for l in main)  # lines ease in and out by default
    assert not [l for l in text.splitlines() if ",KGlow," in l or ",KFx," in l]
    st.timing.fade_in_ms = st.timing.fade_out_ms = 0
    st.glow.enabled = True
    st.effects.kind = "sparkle"
    S.set_karaoke_style(h, st.model_dump(mode="json"))
    text, _ = S.karaoke_ass(h)
    lines = text.splitlines()
    assert not any("\\fad(" in l for l in lines if ",KFx," not in l)
    glows = [l for l in lines if ",KGlow," in l]
    main = [l for l in lines if ",KMain," in l]
    ruby = [l for l in lines if ",KRuby," in l]
    assert len(glows) == 2 * (len(main) + len(ruby))  # unsung + sung glow for every text event
    assert all(l.startswith(("Dialogue: 3,", "Dialogue: 4,")) for l in glows)
    sung = [l for l in glows if l.startswith("Dialogue: 4,")]
    assert all("\\ko" in l and "\\3c&HB3F2FF&" in l for l in sung)  # glow turns #FFF2B3 as it is sung
    assert all(l.startswith("Dialogue: 5,") for l in main) and all(l.startswith("Dialogue: 6,") for l in ruby)
    fx = [l for l in lines if ",KFx," in l]
    # stars around the sung syllables, in the sung glow colour, drawn behind every subtitle by default
    assert fx and all(l.startswith("Dialogue: 0,") and "\\p1" in l and "\\1c&HB3F2FF&" in l for l in fx)
    again, _ = S.karaoke_ass(h)
    assert [l for l in again.splitlines() if ",KFx," in l] == fx  # deterministic: preview == burn
    st.effects.behind = False  # or in front of the lyrics and ruby
    S.set_karaoke_style(h, st.model_dump(mode="json"))
    front = [l for l in S.karaoke_ass(h)[0].splitlines() if ",KFx," in l]
    assert front == [l.replace("Dialogue: 0,", "Dialogue: 7,", 1) for l in fx]

    from kara_align.karaoke.effects import LABELS, Syllable, syllable_events

    syl = [Syllable("好", 1000, 1400, 900, 950, 80, 88, "Arial", 88, False, 3000),
           Syllable("き", 1400, 1800, 980, 950, 80, 88, "Arial", 88, False, 3000)]
    for kind in LABELS:
        st.effects.kind = kind
        ev = syllable_events(st, syl, 1.0)
        assert (not ev) == (kind == "none"), kind
        assert all(700 <= t0 < t1 <= 3300 for _, t0, t1, _, _ in ev), kind
    st.effects.kind = "sparkle"
    few = syllable_events(st.model_copy(update={"effects": st.effects.model_copy(update={"amount": 30})}), syl, 1.0)
    many = syllable_events(st.model_copy(update={"effects": st.effects.model_copy(update={"amount": 200})}), syl, 1.0)
    assert 0 < len(few) < len(many)
    st.effects.color = "#00FF00"
    assert all("\\1c&H00FF00&" in tags for *_, tags, _ in syllable_events(st, syl, 1.0))


def test_styles_effects_and_translation_http_api(tmp_path):
    from fastapi.testclient import TestClient

    from kara_align.web.server import create_app

    client = TestClient(create_app(tmp_path / "projects"))
    lib = client.get("/api/karaoke/styles").json()
    assert lib[0]["id"] == "default" and lib[0]["builtin"]
    st = lib[0]["style"]
    st["glow"]["enabled"] = True
    saved = client.post("/api/karaoke/styles", json={"name": "我的荧光", "style": st}).json()
    assert saved["name"] == "我的荧光" and saved["style"]["glow"]["enabled"]
    assert [x["name"] for x in client.get("/api/karaoke/styles").json()] == ["默认", "暖阳", "我的荧光"]
    assert client.post("/api/karaoke/styles", json={"name": "", "style": st}).status_code == 400
    assert client.delete("/api/karaoke/styles/default").status_code == 400
    assert client.delete(f"/api/karaoke/styles/{saved['id']}").json() == {"ok": True}
    assert client.get("/api/effects").status_code == 404  # full-screen effects are gone
    pid = client.post("/api/projects", json={"name": "t", "mode": "plain"}).json()["project"]["id"]
    r = client.post(f"/api/projects/{pid}/lyrics/fetch-translation")
    assert r.status_code == 400 and "网易云" in r.json()["detail"]


def test_song_info_title_card(tmp_path):
    from fastapi.testclient import TestClient

    from kara_align.karaoke.info import auto_lines, song_fields
    from kara_align.models import Line
    from kara_align.web.server import create_app

    h = _project(tmp_path)
    meta = h.project.lyrics.meta
    meta.title, meta.artist, meta.album = "わたぐも", "赤城みりあ", "STARLIGHT MASTER 13"
    h.project.lyrics.lines.insert(0, Line(text="作词 : 渡辺拓也", kind="meta", sing=False))
    h.project.lyrics.lines.insert(1, Line(text="編曲：本多友紀", kind="meta", sing=False))
    h.save()
    f = song_fields(h.project)
    assert f == {"title": "わたぐも", "artist": "赤城みりあ", "album": "STARLIGHT MASTER 13",
                 "lyricist": "渡辺拓也", "arranger": "本多友紀"}
    st = h.project.karaoke.model_copy(deep=True)
    assert not [l for l in S.karaoke_ass(h)[0].splitlines() if ",KInfo," in l]  # off by default
    st.info.enabled = True
    st.info.fields = ["title", "artist", "lyricist", "composer"]  # no composer in the data: skipped
    assert auto_lines(h.project, st) == ["わたぐも", "赤城みりあ", "作词：渡辺拓也"]
    S.set_karaoke_style(h, st.model_dump(mode="json"))
    card = [l for l in S.karaoke_ass(h)[0].splitlines() if ",KInfo," in l]
    texts = list(dict.fromkeys(l.split("}", 1)[1] for l in card if "\\p1" not in l))  # (in and out events)
    assert texts == ["わたぐも", "赤城みりあ", "作词：渡辺拓也"]
    # at the start (coming in at 0:00, going out before 0:10), above everything
    assert all(l.startswith("Dialogue: 9,0:00:0") for l in card)
    assert all(l.startswith("Dialogue: 9,0:00:00.") for l in card if "\\fad(0," not in l and "1.6," not in l)
    assert all("\\an7" in l for l in card) and any("\\p1" in l for l in card)  # top-left, with the accent bar
    # top-right, glow copies under the text, and the project's own text
    st.info.position = "top-right"
    st.glow.enabled = True
    S.set_karaoke_style(h, st.model_dump(mode="json"))
    client = TestClient(create_app(tmp_path))
    pid = h.dir.name  # the directory name is the project's id in a workspace
    r = client.put(f"/api/projects/{pid}/karaoke/info", json={"text": "わたぐも\n\n赤城みりあ (CV: 黒沢ともよ)\n"}).json()
    assert r["text"].startswith("わたぐも") and r["fields"]["arranger"] == "本多友紀"
    h2 = S.open_dir(h.dir)
    card = [l for l in S.karaoke_ass(h2)[0].splitlines() if ",KInfo," in l]
    assert list(dict.fromkeys(l.split("}", 1)[1] for l in card if l.startswith("Dialogue: 9,") and "\\p1" not in l)) == \
        ["わたぐも", "赤城みりあ (CV: 黒沢ともよ)"]
    # glow copies: coming in and going out for each of the two lines
    assert all("\\an9" in l for l in card if "\\p1" not in l) and len([l for l in card if l.startswith("Dialogue: 8,")]) == 4
    assert client.put(f"/api/projects/{pid}/karaoke/info", json={"text": None}).json()["text"] is None
    # translations along the top edge (a short one, in the middle): the card is not in its way and stays
    # its whole time
    st.translation.enabled = True
    st.info.duration_ms = 60000
    h3 = S.open_dir(h.dir)
    for ln in h3.project.lyrics.sung_lines():
        ln.translation = "译文"
    S.set_karaoke_style(h3, st.model_dump(mode="json"))
    text = S.karaoke_ass(h3)[0].splitlines()
    assert min(l.split(",")[1] for l in text if ",KTrans," in l) < "0:00:02.50"
    assert max(l.split(",")[2] for l in text if ",KInfo," in l) == "0:01:00.50"  # 0.5 s + 60 s


def test_ruby_sweep_follows_the_lyric(tmp_path):
    h = _project(tmp_path)
    st = h.project.karaoke.model_copy(deep=True)
    st.timing.fade_in_ms = st.timing.fade_out_ms = 0
    assert st.ruby.sweep == "own"
    own = S.karaoke_ass(h)[0].splitlines()
    assert all("\\k" in l for l in own if ",KRuby," in l)  # each reading syllable on its own timing
    st.ruby.sweep = "base"
    st.glow.enabled = True
    S.set_karaoke_style(h, st.model_dump(mode="json"))
    lines = S.karaoke_ass(h)[0].splitlines()
    main = [l for l in lines if ",KMain," in l]
    ruby = [l for l in lines if ",KRuby," in l]
    n_ruby_chunks = len([l for l in own if ",KRuby," in l])
    n_chunks = len([l for l in own if ",KMain," in l])
    # the lyric is cut by the same computed line (libass puts its own \\kf edge by glyph ink)
    assert len(main) == 2 * n_chunks and not any("\\k" in l for l in main)
    assert len(ruby) == 2 * n_ruby_chunks and not any("\\k" in l for l in ruby)
    unsung, sung = ruby[0::2], ruby[1::2]
    # the reading in the unsung colour right of the sweep (\iclip), in the sung colour left of it
    assert all("\\1c&HFFFFFF&" in l and "\\clip" not in l and "\\iclip(0,0," in l for l in unsung)
    assert all("\\clip(0,0," in l and "\\t(" in l for l in sung)  # the sung colour, cut at the sweep
    assert [l.replace("\\iclip", "\\clip").split("}", 1)[0].split("\\clip", 1)[1] for l in unsung] == \
        [l.split("}", 1)[0].split("\\clip", 1)[1] for l in sung]
    # lyric and reading of a chunk are cut by one line while it sweeps over the lyric (the reading's
    # cut then goes on over the part of the reading wider than its lyric)
    cuts = lambda l: re.findall(r"\\t\(\d+,\d+,\\clip\([^)]*\)\)", l)  # noqa: E731
    lyric = next(l for l in main[1::2] if l.endswith("窓"))
    reading = next(l for l in sung if l.endswith("まど"))
    assert cuts(reading)[:len(cuts(lyric)) - 1] == cuts(lyric)[:-1]
    # nothing sung before the line starts; the cut sweeps while 窓 (まど) is swept below
    m = next(l for l in own if ",KMain," in l and "窓" in l)
    pre = re.search(r"\\k(\d+)\}\{\\kf(\d+)\}窓", m)
    delay, dur = (int(pre.group(1)) * 10, int(pre.group(2)) * 10) if pre else \
        (0, int(re.search(r"\\kf(\d+)\}窓", m).group(1)) * 10)
    r = next(l for l in sung if "まど" in l)
    assert "\\clip(0,0,0," in r
    t = [(int(a), int(b), int(x)) for a, b, x in re.findall(r"\\t\((\d+),(\d+),\\clip\(0,0,(\d+),", r)]
    assert (delay, delay + 1) == t[0][:2] and (delay, delay + dur) == t[1][:2] and t[1][2] > t[0][2]
    xs = [x for _, _, x in t]
    assert xs == sorted(xs)  # left to right
    # the sung glow of the ruby follows the same cut (no \ko)
    glows = [l for l in lines if ",KGlow," in l and ("まど" in l)]
    assert len(glows) == 2 and "\\clip" in glows[1] and "\\ko" not in glows[1]
    assert not any("\\ko" in l for l in lines if ",KGlow," in l)
    # instant highlight: the cut jumps when each piece starts
    st.timing.highlight = "instant"
    S.set_karaoke_style(h, st.model_dump(mode="json"))
    r = next(l for l in S.karaoke_ass(h)[0].splitlines() if ",KRuby," in l and "\\clip" in l and "まど" in l)
    a, b = map(int, re.search(r"\\t\((\d+),(\d+),\\clip", r).groups())
    assert b - a == 1


def test_sizes_scale_with_the_video_width(tmp_path):
    """Style pixels are for a 1920-wide frame: any video shows text at the same share of its width."""
    h = _project(tmp_path)
    r = h.project.result()

    def main_size(w, hgt):
        text, _ = A.build_ass(h.project, r, size=(w, hgt))
        style = next(l for l in text.splitlines() if l.startswith("Style: KMain,"))
        assert f"PlayResX: {w}" in text and f"PlayResY: {hgt}" in text
        return float(style.split(",")[2])

    base = main_size(1920, 1080)
    assert base == h.project.karaoke.text.size
    assert main_size(3840, 2160) == 2 * base and main_size(1280, 720) == pytest.approx(base * 2 / 3, abs=0.1)
    assert main_size(1080, 1920) == pytest.approx(base * 1080 / 1920, abs=0.1)  # vertical video: not 1.78× larger
    assert main_size(1440, 1080) == pytest.approx(base * 0.75, abs=0.1)  # 4:3


def test_rotated_phone_video_reports_its_upright_size(monkeypatch):
    import json
    import subprocess

    from kara_align.audio import video as V

    def fake(streams):
        out = json.dumps({"format": {"duration": "3.0"}, "streams": streams})
        return lambda *a, **k: subprocess.CompletedProcess(a, 0, out, "")

    monkeypatch.setattr(V, "ffprobe_path", lambda: "ffprobe")
    base = {"codec_type": "video", "width": 1920, "height": 1080, "avg_frame_rate": "30/1"}
    for extra, want in (({}, (1920, 1080)), ({"side_data_list": [{"rotation": -90}]}, (1080, 1920)),
                        ({"tags": {"rotate": "270"}}, (1080, 1920)), ({"side_data_list": [{"rotation": 180}]}, (1920, 1080))):
        monkeypatch.setattr(V.subprocess, "run", fake([{**base, **extra}]))
        info = V.probe_media("x.mp4")
        assert (info["width"], info["height"]) == want, extra


def test_user_text_with_backslashes_and_missing_fonts(tmp_path):
    h = _project(tmp_path)
    ln = h.project.lyrics.sung_lines()[0]
    ln.translation = "AC\\DC {x} \\N"
    h.save()
    st = h.project.karaoke.model_copy(deep=True)
    st.translation.enabled = True
    st.translation.position = "line"
    st.text.font = "No Such Font 123"
    S.set_karaoke_style(h, st.model_dump(mode="json"))
    text, warnings = S.karaoke_ass(h)
    trans = [l for l in text.splitlines() if ",KTrans," in l]
    # libass has no escape for "\": it becomes a full-width one, never "\\" (shown doubled, and a trailing
    # one would eat the next {\k} tag); "\N" is no line break
    assert trans and all("AC＼DC ｛x｝ ＼N" in l for l in trans)
    assert "No Such Font 123" not in text and any("没有字体 No Such Font 123" in w for w in warnings)
    from kara_align.karaoke.fonts import default_family

    assert f"Style: KMain,{default_family()}," in text


def test_translation_under_each_line_stays_inside_the_margins(tmp_path):
    h = _project(tmp_path)
    for ln in h.project.lyrics.sung_lines():
        # (twice over: too wide for the margins in any font, Hiragino or the bundled Noto Sans CJK)
        ln.translation = "a very long English translation that is certainly much wider than the lyric line above it " * 2
    h.save()
    st = h.project.karaoke.model_copy(deep=True)
    st.translation.enabled, st.translation.position = True, "line"
    S.set_karaoke_style(h, st.model_dump(mode="json"))
    text, _ = A.build_ass(h.project, h.project.result(), size=(1080, 1920))
    trans = [l for l in text.splitlines() if ",KTrans," in l]
    assert trans and all("\\fscx" in l for l in trans)
    for l in trans:
        x = float(re.search(r"\\pos\(([\d.]+),", l).group(1))
        assert 0 < x < 1080


def test_effects_end_with_their_line(tmp_path):
    from kara_align.karaoke.effects import Syllable, syllable_events

    st = KaraokeStyle()
    syl = [Syllable("き", 1000, 1300, 500, 900, 60, 88, "Arial", 88, False, 1500)]
    for kind in ("sparkle", "petals", "hearts", "pulse"):
        st.effects.kind = kind
        assert all(t1 <= 1500 for _, _, t1, _, _ in syllable_events(st, syl, 1.0)), kind


def test_saved_styles_file_problems_lose_nothing(tmp_path):
    import json

    from kara_align.karaoke import styles as ST

    ST._path().parent.mkdir(parents=True, exist_ok=True)
    ST._path().write_text("{not json", encoding="utf-8")
    assert [x["name"] for x in ST.list_styles()] == ["默认", "暖阳"]
    assert ST._path().with_suffix(".broken.json").read_text(encoding="utf-8") == "{not json"
    # an entry this version cannot read is written back unchanged when saving
    future = {"id": "st_future", "name": "未来", "style": "kara-align/style-v9"}
    ST._path().write_text(json.dumps([future]), encoding="utf-8")
    ST.save_style("我的", KaraokeStyle().model_dump(mode="json"))
    raw = json.loads(ST._path().read_text(encoding="utf-8"))
    assert [x["name"] for x in raw] == ["我的", "未来"] and raw[1] == future


def test_alternating_rows_share_one_indent_that_keeps_the_staircase():
    from types import SimpleNamespace as NS

    from kara_align.karaoke.ass import alternate_insets

    avail, indent, margin = 1440.0, 240.0, 240.0

    def g(ext, align):
        return ([], ext, 0.0, 0.0, 1.0, align)

    laid = [NS(show_from=0, show_to=5000), NS(show_from=3000, show_to=9000), NS(show_from=20000, show_to=25000),
            NS(show_from=0, show_to=9000)]
    geom = [g(1300, "left"), g(1100, "right"), g(600, "left"), g(900, "center")]
    ins = alternate_insets(laid, geom, indent, avail)
    left_x0, left_x1 = margin + ins[0], margin + ins[0] + 1300
    right_x1 = margin + avail - ins[1]
    right_x0 = right_x1 - 1100
    # the upper (left) line shown with the lower (right) one never reaches past it on either side
    assert left_x1 <= right_x1 and left_x0 <= right_x0
    assert ins[0] == ins[1] == 70  # the room (1440 − 1300) shared equally
    # one indent for the whole song: a short line shown alone later uses the same one (all left rows
    # start at the same x, all right rows end at the same x); centred rows have none
    assert ins[2] == 70 and ins[3] == 0.0
    short = [NS(show_from=0, show_to=5000), NS(show_from=3000, show_to=9000)]
    assert alternate_insets(short, [g(600, "left"), g(700, "right")], indent, avail) == [indent, indent]
    # a line longer than the room goes all the way to its edge
    assert alternate_insets([laid[0], laid[1]], [g(1500, "left"), g(400, "right")], indent, avail) == [0.0, 0.0]


def test_export_names_never_repeat(tmp_path):
    h = _project(tmp_path)
    h.project.name = "My.Song / AC:DC"
    a = S.export_path(h, S._export_stem(h) + "-karaoke", ".mp4")
    assert re.fullmatch(r"My\.Song _ AC_DC-karaoke-\d{8}-\d{6}\.mp4", a.name)
    a.write_bytes(b"x")
    b = S.export_path(h, S._export_stem(h) + "-karaoke", ".mp4")  # the same second: numbered
    assert b != a and (b.name.endswith("-2.mp4") or b.name[:-4] != a.name[:-4])
    # one still being written (its part file) also counts as taken
    b.with_name(f".{b.stem}.part.mp4").write_bytes(b"")
    assert S.export_path(h, S._export_stem(h) + "-karaoke", ".mp4") != b
