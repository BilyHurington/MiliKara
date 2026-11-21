"""The song's cover as the picture behind the subtitles (audio only, lyrics from a music link)."""

import io
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from kara_align import service as S
from kara_align.karaoke import cover as C
from kara_align.lyrics.fetch.netease import _cover
from kara_align.lyrics.fetch.safe_http import is_allowed_host
from kara_align.models import SourceSnapshot

from .test_karaoke import _project

_needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _jpeg(color=(240, 180, 20), size=(600, 600)) -> bytes:
    im = Image.new("RGB", size, color)
    for x in range(size[0] // 3):  # something sharp in it
        for y in range(size[1] // 3):
            im.putpixel((x, y), (20, 40, 200))
    out = io.BytesIO()
    im.save(out, "JPEG")
    return out.getvalue()


def _mp3_with_cover(path: Path) -> Path:
    """A short mp3 whose ID3 tag carries an attached picture (and the plain tone beside it)."""
    here = path.parent
    tone = here / "tone.mp3"
    cover = here / "cover.png"
    Image.new("RGB", (200, 200), (240, 120, 40)).save(cover)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5",
                    "-c:a", "libmp3lame", "-q:a", "9", str(tone)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(tone), "-i", str(cover),
                    "-map", "0:a", "-map", "1:v", "-c:a", "copy", "-c:v", "mjpeg",
                    "-id3v2_version", "3", "-metadata:s:v", "title=Album cover",
                    "-metadata:s:v", "comment=Cover (front)", str(path)], check=True)
    return tone


def test_blurred_cover():
    im = Image.open(io.BytesIO(C.blurred_cover(_jpeg(), (1920, 1080))))
    assert im.size == (1920, 1080) and im.format == "JPEG"
    # dim is what keeps the cover from upstaging the words: more dim = darker, and the whole frame
    # is darker than the source (no sharp cover block left to catch the eye)
    flat = Image.open(io.BytesIO(C.blurred_cover(_jpeg(), (1920, 1080), blur=0, dim=0)))
    dim = Image.open(io.BytesIO(C.blurred_cover(_jpeg(), (1920, 1080), blur=0, dim=80)))
    assert sum(dim.getpixel((960, 540))) < sum(flat.getpixel((960, 540)))
    assert im.getextrema()[0][1] < 200  # the source's bright yellow is gone (blurred + dimmed)
    with pytest.raises(C.CoverError):
        C.blurred_cover(b"not an image")


def test_split_cover():
    im = Image.open(io.BytesIO(C.split_cover(_jpeg(), (1920, 1080), side="left")))
    assert im.size == (1920, 1080) and im.format == "JPEG"
    assert sum(im.getpixel((1500, 540))) == 0  # the lyric side is black
    assert sum(im.getpixel((345, 540))) > 0  # the cover panel is not black
    right = Image.open(io.BytesIO(C.split_cover(_jpeg(), (1920, 1080), side="right")))
    assert sum(right.getpixel((345, 540))) == 0  # now black on the left
    assert sum(right.getpixel((1500, 540))) > 0  # and the cover on the right
    with pytest.raises(C.CoverError):
        C.split_cover(b"not an image")


def test_covers_come_from_the_platforms_image_hosts():
    assert is_allowed_host("p2.music.126.net") and is_allowed_host("y.gtimg.cn")
    assert not is_allowed_host("example.com")
    assert _cover({"al": {"picUrl": "http://p1.music.126.net/a/b.jpg"}}) == "https://p1.music.126.net/a/b.jpg?param=1000y1000"
    assert _cover({"al": {}}) is None


def test_cover_background(tmp_path, monkeypatch):
    h = _project(tmp_path)
    with pytest.raises(S.ServiceError, match="没有可用的封面"):
        S.cover_background(h)  # pasted lyrics: no cover
    h.project.sources.append(SourceSnapshot(origin="netease", platform_song_id="123", text="x",
                                            fetched_meta={"cover_url": "https://p1.music.126.net/c.jpg"}))
    asked = []
    monkeypatch.setattr(C, "fetch_cover", lambda url: asked.append(url) or _jpeg())
    bg = S.cover_background(h)
    assert asked == ["https://p1.music.126.net/c.jpg"]
    assert bg.kind == "image" and (bg.width, bg.height) == (1920, 1080) and bg.filename == "歌曲封面（模糊背景）.jpg"
    assert S.picture(h)["source"] == "background"
    assert S.project_view(h)["view"]["cover"] is True
    # lyrics fetched before covers were kept: the platform is asked again
    h.project.sources[-1].fetched_meta = {}
    from kara_align.lyrics import fetch as F
    from kara_align.lyrics.fetch.types import FetchedSong

    monkeypatch.setattr(F, "fetch_song", lambda p, i: FetchedSong(p, i, cover_url="https://y.gtimg.cn/x.jpg"))
    S.cover_background(h)
    assert asked[-1] == "https://y.gtimg.cn/x.jpg"
    assert len(list((h.dir / "assets").glob("*.jpg"))) == 1  # the previous picture is gone


@_needs_ffmpeg
def test_embedded_cover_from_song_file(tmp_path):
    tone = _mp3_with_cover(tmp_path / "song.mp3")
    assert C.has_embedded_cover(tmp_path / "song.mp3") is True
    data = C.embedded_cover(tmp_path / "song.mp3")
    im = Image.open(io.BytesIO(data))
    assert im.size == (200, 200) and im.format == "JPEG"
    assert C.has_embedded_cover(tone) is False  # the plain tone has no picture
    assert C.embedded_cover(tone) is None


@_needs_ffmpeg
def test_cover_background_from_embedded(tmp_path, monkeypatch):
    h = _project(tmp_path)
    _mp3_with_cover(tmp_path / "song.mp3")
    S.add_audio(h, tmp_path / "song.mp3", "original")  # replace the wav with an mp3 that has a cover
    assert S.project_view(h)["view"]["cover"] is True  # the button is offered for pasted lyrics too
    bg = S.cover_background(h)
    assert bg.kind == "image" and (bg.width, bg.height) == (1920, 1080)
    assert S.picture(h)["source"] == "background"


@_needs_ffmpeg
def test_preview_fills_frame_with_cover_automatically(tmp_path):
    import numpy as np

    h = _project(tmp_path)
    _mp3_with_cover(tmp_path / "song.mp3")
    S.add_audio(h, tmp_path / "song.mp3", "original")
    # no explicit background: the style's "background" still fills the preview frame
    blur = np.asarray(Image.open(io.BytesIO(S.karaoke_preview(h, 1500, style={"background": {"mode": "blur"}}))).convert("RGB"))
    none = np.asarray(Image.open(io.BytesIO(S.karaoke_preview(h, 1500, style={"background": {"mode": "none"}}))).convert("RGB"))
    assert blur.mean() > 8  # the blurred cover lights the frame (a black preview stays near 0)
    assert blur.mean() > none.mean() + 10  # clearly brighter than plain black + subtitles
    # split mode: the cover panel lights its side while the lyric side stays black
    split = np.asarray(Image.open(io.BytesIO(S.karaoke_preview(h, 1500, style={"background": {"mode": "split"}}))).convert("RGB"))
    assert split[:, :950].mean() > 8 and split[:, 1500:].mean() < 8
