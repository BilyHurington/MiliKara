"""Fonts for karaoke subtitles: catalog, resolution and text measurement.

Rendering happens in libass (ffmpeg ``subtitles`` filter), which finds fonts
through fontconfig. We resolve family names through fontconfig too
(``fc-list`` / ``fc-match``), so the file we measure with is the file libass
draws with. Without fontconfig, common font folders are scanned.

libass sizes fonts like VSFilter: the ASS font size is the height
``usWinAscent + usWinDescent`` (OS/2), not the em size. :class:`Measurer`
applies the same conversion before measuring advances with Pillow.
"""

from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

# preferred defaults, first match wins (mac, windows, linux)
DEFAULT_FAMILIES = [
    "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Hiragino Kaku Gothic Pro", "Yu Gothic", "Meiryo",
    "Noto Sans CJK JP", "Source Han Sans JP", "Noto Sans JP", "Noto Sans CJK SC", "Source Han Sans",
    "Hiragino Sans GB", "Microsoft YaHei", "MS Gothic",
]
# the fonts MiliKara ships (Noto Sans CJK: Japanese and Chinese in one collection, SIL OFL), in <app>/fonts
# or $KARA_ALIGN_FONTS; the default on Windows / Linux, and whenever a style's font lacks characters
BUNDLED_JP, BUNDLED_SC = "Noto Sans CJK JP", "Noto Sans CJK SC"

# Chinese fonts for translations, first installed one that has every character wins (windows, mac, linux)
HAN_FAMILIES = [
    BUNDLED_SC, "Microsoft YaHei", "DengXian", "PingFang SC", "Hiragino Sans GB", "Noto Sans CJK SC", "Source Han Sans SC",
    "Noto Sans SC", "Source Han Sans CN", "WenQuanYi Micro Hei", "SimHei", "Microsoft JhengHei", "Noto Sans CJK TC",
]
_FONT_DIRS = ["/System/Library/Fonts", "/Library/Fonts", "~/Library/Fonts",
              os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts"),
              os.path.join(os.environ.get("LOCALAPPDATA", "~/AppData/Local"), "Microsoft/Windows/Fonts"),  # per-user installs
              "/usr/share/fonts", "/usr/local/share/fonts", "~/.fonts", "~/.local/share/fonts"]


@dataclass(frozen=True)
class FontFace:
    family: str  # name written into the ASS file
    names: tuple[str, ...]  # all family names incl. localized ones
    path: str
    index: int
    bold: bool


def _fc_run(args: list[str]) -> subprocess.CompletedProcess:
    """A fontconfig query; a hung or failing one counts as "nothing found" (never blocks a render)."""
    try:
        return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
    except (subprocess.TimeoutExpired, OSError):
        return subprocess.CompletedProcess(args, 1, "", "")


def _fc(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def fonts_dir() -> Optional[Path]:
    """The folder of the bundled fonts: $KARA_ALIGN_FONTS, else the ``fonts`` folder of the app itself
    (a source checkout), else ``<home>/fonts``; None when none of them has a font."""
    from ..project.store import home_dir

    env = os.environ.get("KARA_ALIGN_FONTS")
    for d in ([Path(env).expanduser()] if env else [Path(__file__).resolve().parents[2] / "fonts", home_dir() / "fonts"]):
        if d.is_dir() and any(f.suffix.lower() in (".ttc", ".otc", ".ttf", ".otf") for f in d.iterdir()):
            return d
    return None


@functools.lru_cache(maxsize=1)
def bundled_faces() -> tuple[FontFace, ...]:
    """The bundled fonts' faces (the monospaced variants of the collection left out)."""
    d = fonts_dir()
    if d is None:
        return ()
    return tuple(f for f in _faces_in(sorted(d.iterdir())) if "Mono" not in f.family)


def bundled(family: str) -> bool:
    return any(family == f.family or family in f.names for f in bundled_faces())


@functools.lru_cache(maxsize=1)
def list_faces() -> tuple[FontFace, ...]:
    """Faces that can render Japanese (kana + kanji): the system's and the bundled ones."""
    return tuple(_system_faces()) + tuple(f for f in bundled_faces())


def _system_faces() -> list[FontFace]:
    faces: list[FontFace] = []
    if _fc("fc-list"):
        out = _fc_run(["fc-list", ":lang=ja", "family", "file", "index", "weight"]).stdout
        for line in out.splitlines():
            # "<file>: <family,family>:index=0:weight=80"
            try:
                file, rest = line.split(": ", 1)
            except ValueError:
                continue
            parts = rest.split(":")
            names = tuple(n.strip() for n in parts[0].split(",") if n.strip())
            if not names or names[0].startswith("."):
                continue
            props = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
            weight = _first_int(props.get("weight", "80"))
            faces.append(FontFace(names[0], names, file, _first_int(props.get("index", "0")), weight >= 180))
    else:
        faces = _scan_dirs()
    d = fonts_dir()  # (the bundled ones are added by list_faces, also when fontconfig knows their folder)
    return [f for f in faces if d is None or Path(f.path).parent.resolve() != d.resolve()]


def _first_int(v: str) -> int:
    v = v.strip().strip("[]").split()[0]
    try:
        return int(float(v))
    except ValueError:
        return 0


def _scan_dirs() -> list[FontFace]:
    """The font folders read directly (no fontconfig: Windows).  Reading every font takes seconds, so
    what each file holds is kept in ``<home>/cache/fonts.json`` by path, size and modification time."""
    files = []
    for d in _FONT_DIRS:
        root = Path(os.path.expanduser(d))
        if root.is_dir():
            files += [f for f in root.rglob("*") if f.suffix.lower() in (".ttf", ".otf", ".ttc", ".otc")]
    return _faces_in(files, cache=True)


_SCAN_CACHE_VERSION = 1


def _scan_cache_path() -> Path:
    from ..project.store import home_dir

    return home_dir() / "cache" / "fonts.json"


def _faces_in(files: list[Path], *, cache: bool = False) -> list[FontFace]:
    import json

    known: dict = {}
    if cache:
        try:
            data = json.loads(_scan_cache_path().read_text(encoding="utf-8"))
            if data.get("version") == _SCAN_CACHE_VERSION:
                known = data.get("files", {})
        except (OSError, ValueError):
            pass
    faces: list[FontFace] = []
    seen: dict = {}
    for f in files:
        try:
            st = f.stat()
        except OSError:
            continue
        key, sig = str(f), [st.st_size, int(st.st_mtime)]
        entry = known.get(key)
        if entry is None or entry.get("sig") != sig:
            entry = {"sig": sig, "faces": _read_faces(f)}
        seen[key] = entry
        faces += [FontFace(fam, tuple(names), key, index, bold) for fam, names, index, bold in entry["faces"]]
    if cache and seen != known:
        try:
            from ..project.store import atomic_write_text

            atomic_write_text(_scan_cache_path(), json.dumps({"version": _SCAN_CACHE_VERSION, "files": seen},
                                                             ensure_ascii=False))
        except OSError:
            pass
    return faces


def _read_faces(f: Path) -> list:
    """[family, names, index, bold] of the faces in a font file that have kana and kanji."""
    from fontTools.ttLib import TTCollection, TTFont

    try:
        fonts = TTCollection(str(f), lazy=True).fonts if f.suffix.lower() in (".ttc", ".otc") else [TTFont(str(f), lazy=True)]
    except Exception:
        return []
    out = []
    for i, font in enumerate(fonts):
        try:
            cmap = font.getBestCmap() or {}
            if ord("あ") not in cmap or ord("漢") not in cmap:
                continue
            name = font["name"]
            fam = name.getBestFamilyName() or f.stem
            # every family name, localized ones too (a preset may say 游ゴシック or メイリオ)
            others = {r.toUnicode(errors="ignore").strip() for r in name.names if r.nameID in (1, 16)} - {fam, ""}
            weight = font["OS/2"].usWeightClass if "OS/2" in font else 400
            out.append([fam, [fam, *sorted(others)], i, weight >= 600])
        except Exception:
            continue
    return out


def families() -> list[dict]:
    """Family list for the UI: [{family, names, bold_available}]."""
    out: dict[str, dict] = {}
    for f in list_faces():
        e = out.setdefault(f.family, {"family": f.family, "names": list(f.names), "bold": False})
        e["bold"] = e["bold"] or f.bold
    return sorted(out.values(), key=lambda e: (e["family"] not in DEFAULT_FAMILIES, e["family"].lower()))


def installed(family: str) -> bool:
    """A font family this machine has (by any of its names).  With no font list at all (no
    fontconfig, nothing scanned) every family is assumed present, as libass will decide."""
    faces = list_faces()
    return not faces or any(family == f.family or family in f.names for f in faces)


def default_family() -> str:
    # Windows / Linux: the bundled font (every character, the same look everywhere; no font scan needed)
    if sys.platform != "darwin" and bundled(BUNDLED_JP):
        return BUNDLED_JP
    names = {f.family for f in list_faces()}
    for fam in DEFAULT_FAMILIES:
        if fam in names:
            return fam
    return next(iter(sorted(names)), "Sans")


def fc_escape(family: str) -> str:
    """A family name as a literal in a fontconfig pattern ("-" starts the size, ":" a property,
    "," separates families; "\\" escapes)."""
    return re.sub(r"([\\\-:,=])", r"\\\1", family)


@functools.lru_cache(maxsize=64)
def resolve(family: str, bold: bool) -> tuple[str, int]:
    """Font file + face index libass will use for ``family`` (fontconfig match)."""
    family = family or default_family()
    own = [f for f in bundled_faces() if family == f.family or family in f.names]
    if own:  # the bundled font: the very file libass is given (fontsdir), not whatever fontconfig has
        best = sorted(own, key=lambda f: f.bold != bold)[0]
        return best.path, best.index
    if _fc("fc-match"):
        pattern = f"{fc_escape(family)}:weight={'bold' if bold else 'regular'}"
        out = _fc_run(["fc-match", "-f", "%{file}|%{index}", pattern])
        if out.returncode == 0 and "|" in out.stdout:
            file, idx = out.stdout.rsplit("|", 1)
            return file, _first_int(idx or "0")
    cands = [f for f in list_faces() if family in f.names] or list(list_faces())
    if not cands:
        raise RuntimeError("找不到可用于日文的字体")
    best = sorted(cands, key=lambda f: f.bold != bold)[0]
    return best.path, best.index


def lacking(family: str, bold: bool, text: str) -> str:
    """The characters of ``text`` the font ``family`` has no glyph for (in order, once each; "" when
    the font's character map cannot be read)."""
    try:
        have = _charmap(*resolve(family, bold))
    except Exception:
        return ""
    if not have:
        return ""
    return "".join(dict.fromkeys(c for c in text if not c.isspace() and ord(c) not in have))


def covering_family(text: str, bold: bool, prefer: list[str]) -> Optional[str]:
    """The first family of ``prefer`` installed here that has every character of ``text``."""
    names = {n for f in list_faces() for n in f.names}
    for fam in prefer:
        if fam in names and not lacking(fam, bold, text):
            return fam
    return None


def covering(family: str, bold: bool, text: str, prefer: Sequence[str], *, whole: bool = False) -> str:
    """``family``, or — where the characters it lacks would be drawn by a fallback of libass's own
    choosing (not macOS) — the first of ``prefer`` that has every character of ``text``.  ``whole``:
    also on macOS, for text that must look even (names: a character filled in from PingFang comes out
    narrower and in another style than its neighbours)."""
    if not text or (system_han_fallback() and not whole) or not lacking(family, bold, text):
        return family
    return covering_family(text, bold, list(prefer)) or family


def system_han_fallback() -> bool:
    """Whether the fallback libass uses for missing Chinese characters is known and measured (macOS:
    PingFang through CoreText).  Elsewhere libass picks a font of its own, drawn at that font's own
    scale: the characters it fills in come out bigger or smaller than their neighbours."""
    return _mac_han_fallback() is not None


@functools.lru_cache(maxsize=32)
def _charmap(path: str, index: int) -> frozenset[int]:
    from fontTools.ttLib import TTFont

    try:
        return frozenset((TTFont(path, fontNumber=index, lazy=True).getBestCmap() or {}).keys())
    except Exception:
        return frozenset()


def _is_han(cp: int) -> bool:
    return 0x3400 <= cp <= 0x9FFF or 0xF900 <= cp <= 0xFAFF or 0x20000 <= cp <= 0x3FFFF or 0x3000 <= cp <= 0x303F


@functools.lru_cache(maxsize=1)
def _mac_han_fallback() -> Optional[str]:
    """macOS: libass there asks CoreText for a missing glyph, which answers PingFang for Chinese
    characters; that font is hidden from fontconfig, so it is looked up by file."""
    if sys.platform != "darwin":
        return None
    import glob

    for pat in ("/System/Library/Fonts/PingFang.ttc",
                "/System/Library/AssetsV2/com_apple_MobileAsset_Font*/*/AssetData/PingFang.ttc"):
        found = sorted(glob.glob(pat))
        if found:
            return found[0]
    return None


@functools.lru_cache(maxsize=4)
def _pingfang_sc(path: str, bold: bool) -> Optional[int]:
    from fontTools.ttLib import TTCollection

    try:
        fonts = TTCollection(path, lazy=True).fonts
        best = None
        for i, f in enumerate(fonts):
            if (f["name"].getBestFamilyName() or "") == "PingFang SC":
                w = f["OS/2"].usWeightClass
                if best is None or abs(w - (600 if bold else 400)) < best[0]:
                    best = (abs(w - (600 if bold else 400)), i)
        return best[1] if best else None
    except Exception:
        return None


@functools.lru_cache(maxsize=1024)
def _fc_fallback(family: str, bold: bool, cp: int) -> Optional[tuple[str, int]]:
    """The font fontconfig offers for a character ``family`` lacks (what libass uses with fontconfig)."""
    if not _fc("fc-match"):
        return None
    pattern = f"{fc_escape(family)}:charset={cp:x}:weight={'bold' if bold else 'regular'}"
    out = _fc_run(["fc-match", "-f", "%{file}|%{index}", pattern])
    if out.returncode != 0 or "|" not in out.stdout:
        return None
    file, idx = out.stdout.rsplit("|", 1)
    if "lastresort" in Path(file).name.lower():  # placeholder glyphs for everything: not a real fallback
        return None
    return file, _first_int(idx or "0")


class Measurer:
    """Advance widths of text as libass will lay it out, in pixels.

    Characters the font has no glyph for are drawn by libass with a fallback font (at the same
    ASS size, i.e. with that font's own em scale); they are measured with the font libass is
    likely to pick: on macOS PingFang for Chinese characters (CoreText), otherwise the font
    fontconfig matches for the family with that character.  Text is measured as it will be
    written (escape_text: backslashes and braces full-width)."""

    def __init__(self, family: str, bold: bool, size: float, *, _face: Optional[tuple[str, int]] = None):
        from fontTools.ttLib import TTFont
        from PIL import ImageFont

        self.family = family or default_family()
        self.bold = bold
        path, index = _face or resolve(self.family, bold)
        self._face = (path, index)
        tt = TTFont(path, fontNumber=index, lazy=True)
        upem = tt["head"].unitsPerEm
        os2 = tt["OS/2"] if "OS/2" in tt else None
        win = (os2.usWinAscent + os2.usWinDescent) if os2 else 0
        if not win:
            hhea = tt["hhea"]
            win = hhea.ascent - hhea.descent
        self.em_scale = upem / win  # ASS size → em size (libass: ass_face_set_size)
        # where the glyphs sit in the line box (0 = its top, 1 = its bottom): the typographic ascender /
        # descender, which for CJK fonts is the ideographic em box (splitting a character in bands)
        self.ink = (0.0, 1.0)
        if os2 is not None and os2.usWinAscent and win:
            top = (os2.usWinAscent - os2.sTypoAscender) / win
            bottom = (os2.usWinAscent - os2.sTypoDescender) / win
            if 0 <= top < bottom <= 1.05:
                self.ink = (top, min(1.0, bottom))
        self.size = size
        self._font = ImageFont.truetype(path, size=max(1, round(size * self.em_scale * 4)), index=index)
        self._k = 1 / 4  # measure at 4x for sub-pixel accuracy
        self._fallbacks: dict[tuple[str, int], "Measurer"] = {}

    def _fallback(self, ch: str) -> Optional["Measurer"]:
        cp = ord(ch)
        for m in self._fallbacks.values():  # a font already used for this text's script first
            if cp in _charmap(*m._face):
                return m
        cands: list[tuple[str, int]] = []
        mac = _mac_han_fallback() if _is_han(cp) else None
        if mac is not None:
            idx = _pingfang_sc(mac, self.bold)
            if idx is not None:
                cands.append((mac, idx))
        fc = _fc_fallback(self.family, self.bold, cp)
        if fc is not None:
            cands.append(fc)
        for face in cands:
            if face != self._face and cp in _charmap(*face):
                try:
                    m = Measurer(self.family, self.bold, self.size, _face=face)
                except Exception:
                    continue
                self._fallbacks[face] = m
                return m
        return None

    def width(self, text: str) -> float:
        if not text:
            return 0.0
        from .ass import escape_text

        text = escape_text(text)
        have = _charmap(*self._face)
        if not have or all(ord(c) in have or c.isspace() for c in text):
            return self._font.getlength(text) * self._k
        # runs of characters the font has / lacks; a lacking run is measured with its fallback font
        total, run, run_font = 0.0, "", self
        for c in text:
            font = self if (ord(c) in have or c.isspace()) else (self._fallback(c) or self)
            if font is not run_font and run:
                total += run_font._font.getlength(run) * run_font._k
                run = ""
            run_font = font
            run += c
        if run:
            total += run_font._font.getlength(run) * run_font._k
        return total
