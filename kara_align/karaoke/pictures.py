"""Pictures in the subtitles: colour emoji and the user's own images (a singer's picture, a symbol at the
start / end of every line).

libass draws no colour glyphs and no images, so a picture is drawn as ASS vector drawings of its pixels:
scaled to the size it is shown at (the subtitles' frame is the video's, so one drawing unit is one pixel),
reduced to a few dozen colours, and each colour one event of rectangles (rows of equal pixels, merged with
the same rows below).  The preview, the burnt video and the exported ASS all show it the same way.

Emoji come from Noto Color Emoji (bundled, ``packaging/fetch_fonts.py``): its bitmaps (CBDT, PNG) and its
ligatures (a family, a flag, a skin tone is one glyph), read with fontTools; Apple Color Emoji (sbix) where
it is missing (a source checkout on macOS without the fonts fetched).
"""

from __future__ import annotations

import functools
import hashlib
import io
import re
import threading
from pathlib import Path
from typing import Optional

from PIL import Image

EMOJI_FILE = "NotoColorEmoji.ttf"
_SYSTEM_EMOJI = ["/System/Library/Fonts/Apple Color Emoji.ttc", "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
                 "/usr/share/fonts/noto/NotoColorEmoji.ttf", "/usr/share/fonts/google-noto-emoji/NotoColorEmoji.ttf"]

# characters shown as emoji even without U+FE0F (Unicode Emoji_Presentation, Basic Multilingual Plane);
# above it every character the emoji font has counts (★ ♪ ♡ ❤ without U+FE0F stay text: a font's symbol)
_BMP_EMOJI = (
    (0x231A, 0x231B), (0x23E9, 0x23EC), (0x23F0, 0x23F0), (0x23F3, 0x23F3), (0x25FD, 0x25FE), (0x2614, 0x2615),
    (0x2648, 0x2653), (0x267F, 0x267F), (0x2693, 0x2693), (0x26A1, 0x26A1), (0x26AA, 0x26AB), (0x26BD, 0x26BE),
    (0x26C4, 0x26C5), (0x26CE, 0x26CE), (0x26D4, 0x26D4), (0x26EA, 0x26EA), (0x26F2, 0x26F3), (0x26F5, 0x26F5),
    (0x26FA, 0x26FA), (0x26FD, 0x26FD), (0x2705, 0x2705), (0x270A, 0x270B), (0x2728, 0x2728), (0x274C, 0x274C),
    (0x274E, 0x274E), (0x2753, 0x2755), (0x2757, 0x2757), (0x2795, 0x2797), (0x27B0, 0x27B0), (0x27BF, 0x27BF),
    (0x2B1B, 0x2B1C), (0x2B50, 0x2B50), (0x2B55, 0x2B55),
)
_VS16, _ZWJ, _KEYCAP = 0xFE0F, 0x200D, 0x20E3
_COLOURS = 64  # colours of one picture (with its transparency): flat for emoji, fine for a small photo
MAX_PICTURE_BYTES = 10 * 1024 * 1024
MAX_PICTURE_SIDE = 512  # kept at most this big (shown far smaller)
PICTURE_ID = re.compile(r"^[0-9a-f]{16}$")


# ------------------------------------------------------------------ emoji


class EmojiFont:
    """The bitmaps and ligatures of a colour emoji font (CBDT or sbix)."""

    def __init__(self, path: str):
        from fontTools.ttLib import TTCollection, TTFont

        self.path = path
        font = TTCollection(path, lazy=True).fonts[0] if path.lower().endswith(".ttc") else TTFont(path, lazy=True)
        self.font = font
        self.cmap = font.getBestCmap() or {}
        self.ligs: dict[str, list[tuple[tuple[str, ...], str]]] = {}
        if "GSUB" in font:
            for lookup in font["GSUB"].table.LookupList.Lookup:
                for st in lookup.SubTable:
                    st = getattr(st, "ExtSubTable", st)
                    for first, ls in (getattr(st, "ligatures", None) or {}).items():
                        for lig in ls:
                            self.ligs.setdefault(first, []).append((tuple(lig.Component), lig.LigGlyph))
        for v in self.ligs.values():
            v.sort(key=lambda x: -len(x[0]))  # the longest sequence first
        self._lock = threading.Lock()

    def glyphs(self, cluster: str) -> list[str]:
        """The glyphs of one emoji (a ligature where the font has one; U+FE0F is only a request)."""
        gl = [g for g in (self.cmap.get(ord(c)) for c in cluster) if g]
        out, i = [], 0
        while i < len(gl):
            for comps, lig in self.ligs.get(gl[i], ()):
                if tuple(gl[i + 1:i + 1 + len(comps)]) == comps:
                    out.append(lig)
                    i += 1 + len(comps)
                    break
            else:
                out.append(gl[i])
                i += 1
        # what joins pieces the font has no ligature for is not drawn
        return [g for g in out if g not in (self.cmap.get(_ZWJ), self.cmap.get(_KEYCAP))] or out

    def png(self, glyph: str) -> Optional[bytes]:
        f = self.font
        with self._lock:
            if "CBDT" in f:
                for strike in f["CBDT"].strikeData:
                    if glyph in strike and getattr(strike[glyph], "imageData", None):
                        return strike[glyph].imageData
            if "sbix" in f:
                strikes = f["sbix"].strikes
                for ppem in sorted(strikes, reverse=True):
                    g = strikes[ppem].glyphs.get(glyph)
                    if g is not None and g.graphicType == "png " and g.imageData:
                        return g.imageData
        return None

    def has(self, c: int) -> bool:
        return c in self.cmap


@functools.lru_cache(maxsize=1)
def emoji_font() -> Optional[EmojiFont]:
    from .fonts import fonts_dir

    d = fonts_dir()
    for p in ([str(d / EMOJI_FILE)] if d is not None else []) + _SYSTEM_EMOJI:
        if Path(p).is_file():
            try:
                return EmojiFont(p)
            except Exception:
                continue
    return None


def _presented(cp: int, font: Optional[EmojiFont]) -> bool:
    if cp < 0x10000:
        return any(a <= cp <= b for a, b in _BMP_EMOJI)
    return font is not None and font.has(cp) and not (0xE0000 <= cp <= 0xE007F)


def split_emoji(text: str) -> list[tuple[str, bool]]:
    """``text`` as runs of (text, is one emoji): an emoji is a character shown as one (or followed by
    U+FE0F, a keycap's digit, a flag's two letters) with what joins it (skin tone, U+FE0F, U+200D and the
    next emoji, a subdivision flag's tags)."""
    font = emoji_font()
    cps = [ord(c) for c in text]
    out: list[tuple[str, bool]] = []
    plain = ""
    i = 0
    while i < len(cps):
        cp = cps[i]
        nxt = cps[i + 1] if i + 1 < len(cps) else None
        start = False
        if 0x1F1E6 <= cp <= 0x1F1FF:
            start = True
        elif chr(cp) in "0123456789#*" and (nxt == _KEYCAP or (nxt == _VS16 and i + 2 < len(cps) and cps[i + 2] == _KEYCAP)):
            start = True
        elif _presented(cp, font) or (nxt == _VS16 and (font is None or font.has(cp))):
            start = True
        if not start:
            plain += chr(cp)
            i += 1
            continue
        j = i + 1
        if 0x1F1E6 <= cp <= 0x1F1FF and j < len(cps) and 0x1F1E6 <= cps[j] <= 0x1F1FF:
            j += 1  # a flag: two letters
        while j < len(cps):
            c = cps[j]
            if c in (_VS16, _KEYCAP) or 0x1F3FB <= c <= 0x1F3FF or 0xE0020 <= c <= 0xE007F:
                j += 1
            elif c == _ZWJ and j + 1 < len(cps):
                j += 2
            else:
                break
        if plain:
            out.append((plain, False))
            plain = ""
        out.append(("".join(map(chr, cps[i:j])), True))
        i = j
    if plain:
        out.append((plain, False))
    return out


def has_emoji(text: str) -> bool:
    return any(e for _, e in split_emoji(text))


@functools.lru_cache(maxsize=256)
def emoji_image(cluster: str) -> Optional[Image.Image]:
    """One emoji as a square RGBA picture (its drawing centred), None when the font has none."""
    font = emoji_font()
    if font is None:
        return None
    parts = []
    for g in font.glyphs(cluster):
        data = font.png(g)
        if data:
            im = Image.open(io.BytesIO(data)).convert("RGBA")
            box = im.getbbox()
            if box:
                parts.append(im.crop(box))
    if not parts:
        return None
    h = max(p.height for p in parts)
    w = sum(p.width for p in parts)
    side = max(w, h)
    pad = max(1, round(side * 0.04))
    out = Image.new("RGBA", (side + 2 * pad, side + 2 * pad), (0, 0, 0, 0))
    x = pad + (side - w) // 2
    for p in parts:
        out.paste(p, (x, pad + (side - p.height) // 2), p)
        x += p.width
    return out


# ------------------------------------------------------------------ the user's pictures


def pictures_dir() -> Path:
    from ..project.store import home_dir

    return home_dir() / "pictures"


def save_picture(data: bytes) -> dict:
    """Keep an uploaded image (PNG, JPEG, WebP, GIF's first frame …) as a PNG at most MAX_PICTURE_SIDE big:
    {id, width, height}.  The same image twice is one file."""
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        raise ValueError("无法读取这张图片（支持 PNG、JPEG、WebP、GIF）") from None
    im = im.convert("RGBA")
    if max(im.size) > MAX_PICTURE_SIDE:
        im.thumbnail((MAX_PICTURE_SIDE, MAX_PICTURE_SIDE), Image.Resampling.LANCZOS)
    box = im.getbbox()  # transparent margins would only take room beside the lyrics
    if box is None:
        raise ValueError("这张图片是全透明的")
    im = im.crop(box)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    png = buf.getvalue()
    pid = hashlib.sha256(png).hexdigest()[:16]
    path = pictures_dir() / f"{pid}.png"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(png)
        tmp.replace(path)
    return {"id": pid, "width": im.width, "height": im.height}


def picture_path(pid: str) -> Optional[Path]:
    if not PICTURE_ID.match(pid or ""):
        return None
    p = pictures_dir() / f"{pid}.png"
    return p if p.is_file() else None


def referenced(style) -> set[str]:
    """The pictures a karaoke style uses (its singers', its symbols')."""
    if style is None:
        return set()
    ids = {m.picture for m in style.singers.members} | {style.decor.start_picture, style.decor.end_picture}
    return {i for i in ids if i}


def adopt_picture(pid: str, data: bytes) -> bool:
    """Keep a picture that came in a project package (named by its own content: anything else is
    dropped).  True when it is here now."""
    if not PICTURE_ID.match(pid) or hashlib.sha256(data).hexdigest()[:16] != pid:
        return False
    path = pictures_dir() / f"{pid}.png"
    if not path.exists():
        try:
            Image.open(io.BytesIO(data)).verify()
        except Exception:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
    return True


@functools.lru_cache(maxsize=64)
def _picture_image(path: str, mtime: float) -> Optional[Image.Image]:
    try:
        return Image.open(path).convert("RGBA")
    except Exception:
        return None


def picture_image(pid: str) -> Optional[Image.Image]:
    p = picture_path(pid)
    return _picture_image(str(p), p.stat().st_mtime) if p is not None else None


# ------------------------------------------------------------------ pictures as drawings


def source(key: str) -> Optional[Image.Image]:
    """The picture of a key: "emoji:<the emoji>" or "pic:<id>"."""
    kind, _, rest = key.partition(":")
    if kind == "emoji":
        return emoji_image(rest)
    if kind == "pic":
        return picture_image(rest)
    return None


def aspect(key: str) -> float:
    """Width / height of a picture (1 for an emoji or a missing one)."""
    im = source(key)
    return im.width / im.height if im is not None and im.height else 1.0


def _outlines(mask) -> list[list[tuple[int, int]]]:
    """The outline of the pixels set in ``mask`` (rows × columns) as closed paths of pixel corners, the
    region on the right going round (holes the other way round): every boundary edge once, straight runs
    as one segment."""
    import numpy as np

    p = np.pad(mask, 1)
    m = p[1:-1, 1:-1]
    nxt: dict[tuple[int, int], list[tuple[int, int]]] = {}

    def add(edges, dx0, dy0, dx1, dy1):
        for y, x in zip(*np.nonzero(edges)):
            nxt.setdefault((int(x) + dx0, int(y) + dy0), []).append((int(x) + dx1, int(y) + dy1))

    add(m & ~p[:-2, 1:-1], 0, 0, 1, 0)  # top: left to right
    add(m & ~p[1:-1, 2:], 1, 0, 1, 1)  # right: down
    add(m & ~p[2:, 1:-1], 1, 1, 0, 1)  # bottom: right to left
    add(m & ~p[1:-1, :-2], 0, 1, 0, 0)  # left: up
    loops = []
    while nxt:
        start = next(iter(nxt))
        path, cur = [start], start
        while True:
            outs = nxt[cur]
            nx = outs.pop()
            if not outs:
                del nxt[cur]
            cur = nx
            if cur == start:
                break
            path.append(cur)
        # straight runs: only the corners
        n = len(path)
        loops.append([path[i] for i in range(n)
                      if (path[i][0] - path[i - 1][0]) * (path[(i + 1) % n][1] - path[i][1])
                      != (path[i][1] - path[i - 1][1]) * (path[(i + 1) % n][0] - path[i][0])])
    return loops


@functools.lru_cache(maxsize=512)
def drawing(key: str, w: int, h: int) -> tuple[tuple[str, str], ...]:
    """The picture ``w`` × ``h`` pixels as (colour tags, drawing) per colour: the outline of that colour's
    pixels from (0, 0), drawn with ``\\an7`` at the picture's top-left corner (on whole pixels: crisp, no
    seams between colours)."""
    import numpy as np

    im = source(key)
    if im is None or w < 1 or h < 1:
        return ()
    im = im.resize((w, h), Image.Resampling.LANCZOS)
    alpha = np.asarray(im.getchannel("A"))
    q = im.quantize(colors=_COLOURS, method=Image.Quantize.FASTOCTREE)
    pal = q.getpalette(rawmode="RGBA") or []
    idx = np.asarray(q)
    shown = alpha >= 12
    out = []
    for c in sorted(set(np.unique(idx[shown]).tolist())):
        r_, g_, b_, a_ = pal[4 * c:4 * c + 4] if len(pal) >= 4 * c + 4 else (0, 0, 0, 255)
        if a_ < 12:
            continue
        loops = _outlines(shown & (idx == c))
        path = " ".join("m {} {} l {}".format(*lp[0], " ".join(f"{x} {y}" for x, y in lp[1:])) for lp in loops)
        out.append((f"\\1c&H{b_:02X}{g_:02X}{r_:02X}&\\1a&H{255 - a_:02X}&", path))
    return tuple(out)
