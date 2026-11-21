"""The song's cover as the picture behind the subtitles ("封面背景"), for songs with audio only.

The cover comes from the music platform the lyrics were fetched from (NetEase / QQ Music give it
with the song).  Two looks (see ``KaraokeBackground``): "blur" — the cover blurred and dimmed over
the whole frame; "split" — the cover as a panel on one side with the lyrics on the other.
"""

from __future__ import annotations

import io
import math
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover
    from ..models import KaraokeStyle, Project

COVER_MAX_BYTES = 10 * 1024 * 1024


class CoverError(ValueError):
    pass


def fetch_cover(url: str) -> bytes:
    """The cover image (only from the platforms' image hosts, as the lyrics are fetched)."""
    from ..lyrics.fetch.safe_http import SafeClient
    from ..lyrics.fetch.types import FetchError

    try:
        with SafeClient(max_bytes=COVER_MAX_BYTES) as client:
            r = client.get(url)
    except FetchError as e:
        raise CoverError(f"无法下载封面：{e}") from e
    if r.status_code != 200 or not r.content:
        raise CoverError(f"无法下载封面（HTTP {r.status_code}）")
    return r.content


def has_embedded_cover(path, *, timeout: float = 20.0) -> bool:
    """Whether an audio file carries a cover of its own (ID3 / FLAC / M4A attached picture)."""
    from ..audio.io import AudioError, ffprobe_path, run_tool

    probe = ffprobe_path()
    if probe is None:
        return False
    try:
        code, out, _ = run_tool([probe, "-v", "error", "-select_streams", "v:0",
                                 "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
                                timeout=timeout, what="ffprobe")
    except (AudioError, OSError):
        return False
    return code == 0 and b"video" in out.lower()


def embedded_cover(path, *, timeout: float = 30.0) -> bytes | None:
    """The cover embedded in an audio file, decoded to JPEG, or ``None`` when there is none.

    Uses the ffmpeg the project already runs for audio, so no extra dependency."""
    from ..audio.io import AudioError, ffmpeg_path, run_tool

    try:
        code, out, _ = run_tool(
            [ffmpeg_path(), "-v", "error", "-nostdin", "-y", "-i", str(path),
             "-map", "0:v:0", "-frames:v", "1", "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "3", "pipe:1"],
            timeout=timeout, what="ffmpeg")
    except (AudioError, OSError):
        return None
    return out if code == 0 and out else None


def _open_cover(data: bytes):
    from PIL import Image, ImageOps

    try:
        return ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    except Exception as e:
        raise CoverError("封面不是可以读取的图片") from e


def split_side(project, style) -> "Optional[str]":
    """Which side the "split" cover panel is on (the lyrics take the other side), or None when there is
    no panel.

    An explicit split background decides; with nothing uploaded and no video of the song behind the
    lyrics, the style's own ``background`` setting does (the panel is rendered at preview / burn time,
    see ``service._cover_jpeg_file``)."""
    b = project.background
    if b is not None:
        return (b.side or style.background.side or "left") if b.cover == "split" else None
    if style.background.mode != "split":
        return None
    # a video is the picture: the cover panel is not drawn (as service._video_file decides)
    v = project.video
    orig = project.asset("original")
    if v is not None and orig is not None and v.audio_sha256 == orig.sha256:
        return None
    return style.background.side or "left"


def split_geometry(size: tuple[int, int] = (1920, 1080), side: str = "left") -> dict:
    """Where the cover panel sits in a "split" background, and how big it is (px, un-scaled at ``size``).

    The lyrics run on the other side (the frame width minus ``panel``)."""
    W, H = size
    panel = round(W * 0.36)
    side_len = round(min(panel * 0.72, H * 0.56))
    cx = (panel - side_len) // 2 if side == "left" else W - panel + (panel - side_len) // 2
    cy = (H - side_len) // 2
    return {"panel": panel, "side_len": side_len, "cx": cx, "cy": cy}


def _rounded_cover(im, side_len: int):
    """The cover, centre-cropped square, resized to ``side_len``, with a rounded mask."""
    from PIL import Image, ImageDraw

    sq = min(im.width, im.height)
    cover = im.crop(((im.width - sq) // 2, (im.height - sq) // 2, (im.width + sq) // 2, (im.height + sq) // 2))
    cover = cover.resize((side_len, side_len), Image.LANCZOS)
    radius = round(side_len * 0.04)
    mask = Image.new("L", (side_len, side_len), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, side_len - 1, side_len - 1), radius, fill=255)
    return cover, mask, radius


def blurred_cover(data: bytes, size: tuple[int, int] = (1920, 1080), blur: int = 45, dim: int = 50) -> bytes:
    """A JPEG of ``size``: the cover blurred over the whole frame and dimmed so the words stand out.

    ``blur`` (0–100) is how much the cover is softened; ``dim`` (0–100) is how dark the frame is
    (a black overlay, the key to keeping the cover from upstaging the lyrics)."""
    from PIL import Image, ImageEnhance, ImageFilter

    im = _open_cover(data)
    W, H = size
    # the backdrop: cover filling the frame, blurred, a little more saturated
    s = max(W / im.width, H / im.height) * 1.08
    bg = im.resize((math.ceil(im.width * s), math.ceil(im.height * s)), Image.LANCZOS)
    x0, y0 = (bg.width - W) // 2, (bg.height - H) // 2
    bg = bg.crop((x0, y0, x0 + W, y0 + H))
    radius = max(W, H) * blur / 800.0
    if radius > 0:
        bg = bg.filter(ImageFilter.GaussianBlur(radius))
    bg = ImageEnhance.Color(bg).enhance(1.15)
    # one darkness mask: a uniform dim plus a gradient darker towards the bottom (where the lyrics are)
    dim_val = round(255 * dim / 100 * 0.85)
    shade = Image.new("L", (1, H))
    for y in range(H):
        t = max(0.0, (y / H - 0.55) / 0.45)
        shade.putpixel((0, y), max(dim_val, int(115 * t * t)))
    bg = Image.composite(Image.new("RGB", (W, H)), bg, shade.resize((W, H)))
    out = io.BytesIO()
    bg.save(out, "JPEG", quality=92)
    return out.getvalue()


def split_cover(data: bytes, size: tuple[int, int] = (1920, 1080), side: str = "left") -> bytes:
    """A JPEG of ``size``: black, with the cover (rounded, softly shadowed) as a panel on ``side``."""
    from PIL import Image, ImageDraw, ImageFilter

    im = _open_cover(data)
    W, H = size
    geo = split_geometry(size, side)
    side_len = geo["side_len"]
    cover, mask, radius = _rounded_cover(im, side_len)
    bg = Image.new("RGB", (W, H), (0, 0, 0))
    pad = round(side_len * 0.12)
    shadow = Image.new("L", (side_len + 2 * pad, side_len + 2 * pad), 0)
    ImageDraw.Draw(shadow).rounded_rectangle((pad, pad, pad + side_len, pad + side_len), radius, fill=150)
    shadow = shadow.filter(ImageFilter.GaussianBlur(pad / 2.5))
    cx, cy = geo["cx"], geo["cy"]
    bg.paste(Image.new("RGB", shadow.size), (cx - pad, cy - pad + round(side_len * 0.03)), shadow)
    bg.paste(cover, (cx, cy), mask)
    out = io.BytesIO()
    bg.save(out, "JPEG", quality=92)
    return out.getvalue()
