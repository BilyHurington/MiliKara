"""Saved subtitle styles ("预设").

The library lives in ``<KARA_ALIGN_HOME>/styles.json`` and is shared by every
project and by the simple mode.  The built-in styles (默认, 暖阳) are always
present and read-only; saving under their names is refused (save a copy under
another name).
"""

from __future__ import annotations

import json
import threading
from typing import Optional

from ..models import KaraokeStyle, new_id, utcnow
from ..project.store import atomic_write_text, home_dir

DEFAULT_ID = "default"
DEFAULT_NAME = "默认"

# The look tuned on a real project: pink sweep, romaji over every syllable, lines
# close to the bottom, shown 4 s early and held 2 s, easing in and out.
_DEFAULT: dict = {
    "layout": {"margin_v": 40, "line_spacing": 0, "margin_h": 240},
    "text": {"color_sung": "#ED35B3"},
    "ruby": {"script": "romaji", "target": "all"},
    "timing": {"lead_in_ms": 4000, "hold_ms": 2000},
}

WARM_ID = "warm"
WARM_NAME = "暖阳"

# Warm yellow / orange (tuned on わたぐも, 赤城みりあ): cream text turning orange, a
# golden glow that turns orange as it is sung, deep brown outlines that stay
# readable on bright stages, a rounded font, pale-gold translations along the
# top, soft yellow sparkles, and the song's title card in the top-left corner.
_WARM: dict = {
    **_DEFAULT,
    "text": {"font": "Hiragino Maru Gothic ProN", "bold": True, "color_unsung": "#FFF8E7", "color_sung": "#FF8A1E",
             "outline_color": "#6B2E00", "outline": 5.0, "shadow": 2.5, "shadow_color": "#3D1A00",
             "shadow_opacity": 50},
    "ruby": {"script": "romaji", "target": "all", "follow_colors": True},
    "glow": {"enabled": True, "color_unsung": "#FFC53D", "color_sung": "#FF7A00", "size": 10.0, "blur": 9.0,
             "strength": 75, "ruby": True},
    "translation": {"enabled": True, "position": "opposite", "size_pct": 62, "font": "Hiragino Sans GB",
                    "bold": True, "color": "#FFE9A8", "outline_color": "#6B2E00", "outline": 3.5, "shadow": 1.5,
                    "glow": True},
    "info": {"enabled": True, "position": "top-left", "fields": ["title", "artist", "album"], "size": 60},
    "effects": {"kind": "sparkle", "amount": 60, "size": 90, "color": "#FFE27A"},
}

_BUILTIN = ((DEFAULT_ID, DEFAULT_NAME, _DEFAULT), (WARM_ID, WARM_NAME, _WARM))
_lock = threading.Lock()


class StyleError(ValueError):
    pass


def _builtin(name: str, over: dict) -> KaraokeStyle:
    data = KaraokeStyle().model_dump()
    for key, part in over.items():
        data[key].update(part)
    data["preset"] = name
    return KaraokeStyle.model_validate(data)


def default_style() -> KaraokeStyle:
    return _builtin(DEFAULT_NAME, _DEFAULT)


def warm_style() -> KaraokeStyle:
    return _builtin(WARM_NAME, _WARM)


def _path():
    return home_dir() / "styles.json"


def _read_raw() -> list:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except Exception:  # unreadable: kept aside (never overwritten by the next save), start empty
        try:
            aside = _path().with_suffix(".broken.json")
            if aside.exists():  # an earlier one is kept too
                aside = _path().with_suffix(f".broken-{utcnow().replace(':', '')[:17]}-{new_id()[:4]}.json")
            _path().replace(aside)
        except OSError:
            pass
        return []
    return raw if isinstance(raw, list) else []


def _parse(item) -> Optional[dict]:
    try:
        return {"id": str(item["id"]), "name": str(item["name"]), "updated": item.get("updated"),
                "style": KaraokeStyle.model_validate(item["style"]).model_dump(mode="json")}
    except Exception:
        return None


def _load_user() -> list[dict]:
    return [x for x in (_parse(i) for i in _read_raw()) if x is not None]  # a broken entry is skipped


def _save_user(items: list[dict]) -> None:
    # entries this version cannot read (e.g. saved by a newer one) are written back unchanged
    unreadable = [i for i in _read_raw() if _parse(i) is None]
    atomic_write_text(_path(), json.dumps(items + unreadable, ensure_ascii=False, indent=1))


def list_styles() -> list[dict]:
    """[{id, name, builtin, updated, style}]: the built-in styles first."""
    builtin = [{"id": i, "name": n, "builtin": True, "updated": None, "style": _builtin(n, over).model_dump(mode="json")}
               for i, n, over in _BUILTIN]
    return builtin + [{**x, "builtin": False} for x in _load_user()]


def get_style(style_id: str) -> KaraokeStyle:
    for x in list_styles():
        if x["id"] == style_id:
            return KaraokeStyle.model_validate(x["style"])
    raise StyleError("没有这个预设")


def save_style(name: str, style: dict, style_id: Optional[str] = None) -> dict:
    """Create (no id) or overwrite (id) a saved style.  A new style whose name is
    already used replaces that one, so "保存" twice never makes duplicates."""
    name = (name or "").strip()
    if not name:
        raise StyleError("请给预设起个名字")
    for i, n, _ in _BUILTIN:
        # nor can a saved style be renamed to a built-in name (the list would show two of them)
        if style_id == i or name == n:
            raise StyleError(f"“{n}”是内置预设，不能修改，请换个名字另存")
    try:
        st = KaraokeStyle.model_validate(style, context={"strict": True})
    except Exception as e:
        raise StyleError(f"字幕样式无效：{e}") from e
    st.preset = name
    with _lock:
        items = _load_user()
        target = next((x for x in items if (style_id and x["id"] == style_id) or (not style_id and x["name"] == name)),
                      None)
        if style_id and target is None:
            raise StyleError("没有这个预设")
        entry = {"id": target["id"] if target else new_id("st"), "name": name, "updated": utcnow(),
                 "style": st.model_dump(mode="json")}
        items = [entry if (target and x["id"] == target["id"]) else x for x in items]
        if target is None:
            items.append(entry)
        _save_user(items)
    return {**entry, "builtin": False}


def delete_style(style_id: str) -> None:
    if any(style_id == i for i, _, _ in _BUILTIN):
        raise StyleError("内置预设不能删除")
    with _lock:
        items = _load_user()
        if not any(x["id"] == style_id for x in items):
            raise StyleError("没有这个预设")
        _save_user([x for x in items if x["id"] != style_id])
