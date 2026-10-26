"""Colour emoji and the user's pictures in the subtitles (karaoke.pictures): found, drawn, used."""

import io
import re

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from kara_align import service as S
from kara_align.karaoke import pictures as P
from kara_align.karaoke.info import legend_entries
from kara_align.karaoke.render import _shown_at

from .test_karaoke import _home, _project, needs_ffmpeg  # noqa: F401  (the fixture is used by name)
from .test_singers import _two_singers

needs_emoji = pytest.mark.skipif(P.emoji_font() is None, reason="no colour emoji font here (packaging/fetch_fonts.py)")


def _png(size=(40, 30), colour=(230, 40, 60, 255), hole=False) -> bytes:
    im = Image.new("RGBA", (size[0] + 10, size[1] + 10), (0, 0, 0, 0))
    im.paste(Image.new("RGBA", size, colour), (5, 5))
    if hole:
        im.paste(Image.new("RGBA", (10, 10), (0, 0, 0, 0)), (20, 15))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


@needs_emoji
def test_emoji_are_found_with_what_joins_them():
    runs = P.split_emoji("♪ありがとう🎤★👨‍👩‍👧🇯🇵👍🏽❤️❤1️⃣")
    assert runs == [("♪ありがとう", False), ("🎤", True), ("★", False), ("👨‍👩‍👧", True), ("🇯🇵", True),
                    ("👍🏽", True), ("❤️", True), ("❤", False), ("1️⃣", True)]
    assert P.split_emoji("A B") == [("A B", False)]
    font = P.emoji_font()
    for e in ("👨‍👩‍👧", "🇯🇵", "👍🏽", "❤️‍🔥", "1️⃣"):
        assert len(font.glyphs(e)) == 1, e  # one picture, not its pieces
        assert P.emoji_image(e) is not None


def test_outlines_go_round_regions_and_holes():
    square = np.zeros((4, 4), bool)
    square[1:3, 1:3] = True
    assert P._outlines(square) == [[(1, 1), (3, 1), (3, 3), (1, 3)]]
    ring = np.ones((3, 3), bool)
    ring[1, 1] = False
    loops = P._outlines(ring)
    assert sorted(len(lp) for lp in loops) == [4, 4]
    diagonal = np.eye(2, dtype=bool)  # two pixels touching at a corner
    assert sum(len(lp) for lp in P._outlines(diagonal)) == 8


def _rendered(paths: tuple, w: int, h: int) -> np.ndarray:
    """The frame libass draws for a drawing at (5, 5) on black."""
    from kara_align.karaoke.render import preview_png

    ass = ("[Script Info]\nScriptType: v4.00+\nPlayResX: {0}\nPlayResY: {1}\n\n[V4+ Styles]\n"
           "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
           "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
           "MarginR, MarginV, Encoding\nStyle: S,Arial,20,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,"
           "0,0,1,0,0,7,0,0,0,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
           "Effect, Text\n").format(w + 10, h + 10)
    ass += "".join(f"Dialogue: 0,0:00:00.00,0:00:05.00,S,,0,0,0,,{{\\an7\\pos(5,5)\\bord0\\shad0{t}\\p1}}{p}\n"
                   for t, p in paths)
    return np.asarray(Image.open(io.BytesIO(preview_png(ass, 1000, (w + 10, h + 10)))).convert("RGB")).astype(int)


def test_a_picture_is_kept_once_and_drawn_pixel_by_pixel(tmp_path):
    a = P.save_picture(_png(hole=True))
    assert a == P.save_picture(_png(hole=True))  # the same image: the same file
    assert (a["width"], a["height"]) == (40, 30)  # the transparent margin taken off
    paths = P.drawing(f"pic:{a['id']}", 40, 30)
    assert len(paths) == 1 and "\\1c&H3C28E6&" in paths[0][0]
    if needs_ffmpeg.args[0] is False:
        # the picture where it is, its hole and the frame around it black (the frame's colours are
        # subsampled: only away from the edges are they exact)
        im = _rendered(paths, 40, 30)
        assert abs(im[10, 10] - (230, 40, 60)).max() < 8 and abs(im[30, 40] - (230, 40, 60)).max() < 8
        assert im[20, 25].max() < 8 and im[1, 1].max() < 8 and im[38, 48].max() < 8
    with pytest.raises(ValueError):
        P.save_picture(b"not an image")
    assert P.picture_path("../etc") is None and P.picture_path("0" * 16) is None


def test_pictures_over_http(tmp_path):
    from kara_align.web.server import create_app

    client = TestClient(create_app(tmp_path / "projects"))
    r = client.post("/api/karaoke/pictures", files={"file": ("a.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    got = client.get(f"/api/karaoke/pictures/{pid}")
    assert got.status_code == 200 and got.headers["content-type"] == "image/png"
    assert client.get("/api/karaoke/pictures/nope").status_code == 404
    assert client.post("/api/karaoke/pictures", files={"file": ("a.txt", b"hello", "text/plain")}).status_code == 400


def _pictures(text: str) -> list[str]:
    return [e for e in text.splitlines() if e.startswith("Dialogue:") and "\\blur0\\1c" in e]


def test_singers_pictures_beside_the_lines_and_in_the_legend(tmp_path):
    h = _project(tmp_path)
    _two_singers(h)
    st = h.project.karaoke
    ann = P.save_picture(_png())["id"]
    st.singers.members[0].picture = ann
    st.singers.labels = "start"
    h.save()
    both, _ = S.karaoke_ass(h)
    assert _pictures(both) and any(e.endswith("}Ann") for e in both.splitlines())
    # the legend: the picture between the swatch and the name
    assert legend_entries(h.project, st)[0][3] == ann
    st.singers.label_content = "picture"
    h.save()
    text, _ = S.karaoke_ass(h)
    assert not any(e.endswith("}Ann") and "\\an3" in e for e in text.splitlines())  # the picture instead
    assert any(e.endswith("}Bo, b") and "\\an3" in e for e in text.splitlines())  # no picture: the name
    st.singers.label_content = "text"
    h.save()
    text, _ = S.karaoke_ass(h)
    assert any(e.endswith("}Ann") and "\\an3" in e for e in text.splitlines())
    # a picture this computer does not have: left out, said once
    st.singers.label_content = "both"
    st.singers.members[0].picture = "f" * 16
    h.save()
    text, warnings = S.karaoke_ass(h)
    assert any("找不到" in w for w in warnings)


@needs_emoji
def test_emoji_and_a_picture_at_the_ends_of_every_line(tmp_path):
    h = _project(tmp_path)
    st = h.project.karaoke
    st.decor.start, st.decor.end = "♪🎤", "★"
    st.decor.end_picture = P.save_picture(_png())["id"]
    h.save()
    text, _ = S.karaoke_ass(h)
    pics = _pictures(text)
    # per line: the emoji at the start, the picture at the end (several colours each); ♪ and ★ stay text
    assert len({re.search(r"\\pos\((\d+),", e).group(1) for e in pics}) >= 4
    assert any(e.endswith("}♪") for e in text.splitlines()) and any(e.endswith("}★") for e in text.splitlines())
    assert not any("🎤" in e for e in text.splitlines())
    # whole pixels: the pictures sit on integer positions
    assert all(re.search(r"\\pos\(\d+,\d+\)", e) for e in pics)


def test_a_package_carries_the_styles_pictures(tmp_path, monkeypatch):
    from kara_align.project import store

    h = _project(tmp_path)
    pid = P.save_picture(_png())["id"]
    h.project.karaoke.decor.start_picture = pid
    h.save()
    z = store.export_package(h.project, h.dir, tmp_path / "p.kara.zip", include_audio=False)
    P.picture_path(pid).unlink()
    store.import_package(z, tmp_path / "dst")
    assert P.picture_path(pid) is not None
    # one whose name is not its content is not taken
    assert not P.adopt_picture("0" * 16, _png())


def test_a_preview_reads_only_what_shows_then():
    head = "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    ev = ["Dialogue: 0,0:00:01.00,0:00:02.00,S,,0,0,0,,a", "Dialogue: 0,0:00:05.00,0:00:06.00,S,,0,0,0,,b"]
    out = _shown_at(head + "\n".join(ev), 1.5)
    assert ev[0] in out and ev[1] not in out and "Format:" in out
