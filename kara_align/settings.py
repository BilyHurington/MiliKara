"""App-wide settings (not per project): AI provider and the simple-mode pipeline.

Stored in ``<KARA_ALIGN_HOME>/settings.json`` (mode 0600).  The API key is
never sent back to the browser and never written into a project or package.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from typing import Any, Literal, Optional
from urllib.parse import urlsplit

from pydantic import Field, ValidationError, field_validator, model_validator

from . import audio_versions
from .audio_versions import AudioVersion
from .karaoke.styles import warm_style as simple_default_style
from .models import KaraokeStyle, _Base
from .project.store import atomic_write_text, home_dir, timestamped

log = logging.getLogger(__name__)

# an environment variable holding a key: never an arbitrary one (PATH, HOME, other secrets …)
API_KEY_ENV_PATTERN = r"^[A-Z][A-Z0-9_]*(KEY|TOKEN)$"

# "manual": the prompt is copied into any web chat and the reply pasted back (no key, nothing to install)
AiProvider = Literal["manual", "claude", "codex", "openai"]


class CliChoice(_Base):
    """Which copy of a CLI to run (reading/cli_locate.py): "auto" (PATH, then a desktop app's own copy,
    then WSL), "path", "app", "wsl:<distribution>" or "custom" (``path``)."""

    where: str = Field(default="auto", max_length=200)
    path: str = Field(default="", max_length=1000)

    @field_validator("where")
    @classmethod
    def _where(cls, v: str) -> str:
        v = (v or "auto").strip()
        if v in ("auto", "path", "app", "custom") or v.startswith("wsl:"):
            return v
        raise ValueError(f"未知的运行位置：{v}")


class AiSettings(_Base):
    # AI readings on / off (off: rule readings only; the detailed mode's copy / paste round trip is
    # always there); used by the simple mode's tasks and the detailed mode's one-click button
    enabled: bool = False
    provider: AiProvider = "manual"
    model: str = ""  # empty: the CLI's own default; required for the API
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""  # stored locally only; GET returns has_api_key instead
    api_key_env: str = "OPENAI_API_KEY"  # used when no key is stored
    timeout_s: int = Field(default=600, ge=30, le=3600)
    # where Claude Code / Codex run from (found automatically, or chosen / typed by hand)
    claude_cli: CliChoice = Field(default_factory=CliChoice)
    codex_cli: CliChoice = Field(default_factory=CliChoice)

    @model_validator(mode="before")
    @classmethod
    def _from_none(cls, data: Any) -> Any:
        """Settings from before the switch: provider "none" meant off (and is the manual round trip now)."""
        if isinstance(data, dict):
            if data.get("provider") == "none":
                data = {**data, "provider": "manual", "enabled": False}
            elif "enabled" not in data and data.get("provider") in ("claude", "codex", "openai"):
                data = {**data, "enabled": True}
        return data

    @field_validator("base_url")
    @classmethod
    def _http_url(cls, v: str) -> str:
        parts = urlsplit(v.strip())
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("API 地址必须是 http:// 或 https:// 开头的网址")
        if parts.username or parts.password:
            raise ValueError("API 地址里不能包含账号密码")
        return v.strip()

    @field_validator("api_key_env")
    @classmethod
    def _env_name(cls, v: str) -> str:
        if not re.fullmatch(API_KEY_ENV_PATTERN, v):
            raise ValueError("环境变量名只能是大写字母、数字和下划线，并以 KEY 或 TOKEN 结尾（如 OPENAI_API_KEY）")
        return v


class TaskStyleOptions(_Base):
    """The subtitle choices of one simple-mode task (step 4); the last ones used are kept in the
    settings, so the next task starts from them."""

    # defaults: the glow template in orange + yellow, translation and title card on, hiragana over kanji
    # ("default" = the style edited in the settings page)
    source: Literal["template", "saved", "default"] = "template"
    template: Literal["plain", "glow"] = "glow"
    color: str = "#FF8A1E"  # main theme colour
    secondary: str = "#F5C400"  # second theme colour; "" = one colour
    saved_id: str = ""  # a saved style (source == "saved")
    translation: Optional[bool] = True  # shown when the lyrics have one; None = as the style says
    song_info: Optional[bool] = True  # title card at the start; None = as the style says
    ruby: Literal["style", "hiragana", "katakana", "romaji", "off"] = "hiragana"
    ruby_target: Optional[Literal["kanji", "all"]] = "kanji"  # None = as the style says
    # the effect as each syllable is sung, chosen on its own (the glow template's sparkles can be turned
    # off without leaving the template); None = as the style says
    effects: Optional[Literal["none", "pulse", "ring", "shine", "sparkle", "petals", "hearts", "ball"]] = None
    # countdown dots before the first line / after a long pause; None = as the style says
    countdown_intro: Optional[bool] = None
    countdown_interlude: Optional[bool] = None
    # the sound of the videos, one video each (audio_versions); None = the settings' choice
    video_audio: Optional[list[AudioVersion]] = None
    vocal_keep_pct: Optional[float] = Field(default=None, ge=0.0, le=100.0)  # "mix": None = the settings' level

    @field_validator("video_audio", mode="before")
    @classmethod
    def _audio_versions(cls, v: Any) -> Any:
        return None if v is None else audio_versions.normalize(v)


class SimpleSettings(_Base):
    default_mode: Literal["plain", "lrc"] = "lrc"
    separate: bool = True
    separation_preset: str = "melband-roformer"
    separation_device: Literal["auto", "cpu"] = "auto"
    # LRC offset of new tasks: "manual" = mark the first line when adding; "auto" = detected after
    # separation, asked only when unsure (pipeline.stage_calibrate)
    calibration: Literal["manual", "auto"] = "manual"

    @field_validator("separation_preset")
    @classmethod
    def _known_preset(cls, v: str) -> str:
        from .audio.separation import PRESET_NAMES

        if v not in PRESET_NAMES:
            raise ValueError(f"未知的分离预设 {v}（可选 {', '.join(PRESET_NAMES)}）")
        return v

    # the complete subtitle style of new tasks (layout, colours, ruby, timing);
    # output.vocal_keep_pct is taken from vocal_keep_pct below
    karaoke: KaraokeStyle = Field(default_factory=simple_default_style)
    auto_export: bool = True
    video_audio: list[AudioVersion] = Field(default_factory=lambda: ["original"])  # one video each
    vocal_keep_pct: float = Field(default=20.0, ge=0.0, le=100.0)
    quality: Literal["standard", "high"] = "standard"

    @field_validator("video_audio", mode="before")
    @classmethod
    def _audio_versions(cls, v: Any) -> Any:
        return audio_versions.normalize(v)
    # last choices of the new-task form (saved as they change)
    task_style: TaskStyleOptions = Field(default_factory=TaskStyleOptions)


class AppSettings(_Base):
    version: int = 1
    ai: AiSettings = Field(default_factory=AiSettings)
    simple: SimpleSettings = Field(default_factory=SimpleSettings)
    # look for a newer version (the latest GitHub release) when the app is opened
    check_updates: bool = True
    # burn videos with the graphics card's encoder where one works (karaoke/render.py)
    hardware_encoding: bool = True

    @model_validator(mode="before")
    @classmethod
    def _old_switch(cls, data: Any) -> Any:
        """The simple mode's own "AI readings" switch (before ai.enabled) turned it off for tasks."""
        if isinstance(data, dict) and isinstance(data.get("ai"), dict) and isinstance(data.get("simple"), dict):
            ai, simple = data["ai"], data["simple"]
            if "enabled" not in ai and simple.get("ai_readings") is False:
                data = {**data, "ai": {**ai, "enabled": False,
                                       "provider": "manual" if ai.get("provider") == "none" else ai.get("provider", "manual")}}
        return data


_lock = threading.Lock()


def settings_path():
    return home_dir() / "settings.json"


def load() -> AppSettings:
    p = settings_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return AppSettings()
    except (OSError, ValueError):  # unreadable: the app still starts; the file is kept for inspection
        _set_aside(p)
        return AppSettings()
    if not isinstance(data, dict):
        _set_aside(p)
        return AppSettings()
    return _validate_leniently(data)


def _set_aside(p) -> None:
    try:  # a new name each time: an earlier broken copy is never overwritten
        p.replace(timestamped(p))
    except OSError:
        pass


def _validate_leniently(data: dict) -> AppSettings:
    """A value that is no longer valid (an option renamed, a range narrowed) goes back to its default;
    everything else — the API key above all — is kept."""
    for _ in range(100):
        try:
            return AppSettings.model_validate(data)
        except ValidationError as e:
            dropped = False
            for err in e.errors():
                dropped = _drop(data, err.get("loc") or ()) or dropped
            if not dropped:
                break
    log.warning("settings.json could not be read, using the defaults")
    ai = data.get("ai") if isinstance(data.get("ai"), dict) else {}
    key = ai.get("api_key") if isinstance(ai.get("api_key"), str) else ""
    return AppSettings(ai=AiSettings(api_key=key))


def _drop(data: Any, loc: tuple) -> bool:
    """Remove the value at ``loc`` (the innermost field of a dict; a whole list when the error is
    inside one).  Returns whether something was removed."""
    cur = data
    parent, key = None, None
    for part in loc:
        if isinstance(cur, dict) and isinstance(part, str) and part in cur:
            parent, key = cur, part
            cur = cur[part]
        elif isinstance(cur, list) and isinstance(part, int):
            break  # inside a list: the list itself goes back to its default
        else:
            break
    if parent is not None and key is not None:
        log.warning("settings.json: invalid value for %s, using the default", ".".join(map(str, loc)))
        del parent[key]
        return True
    return False


def save(s: AppSettings) -> None:
    p = settings_path()
    atomic_write_text(p, json.dumps(s.model_dump(mode="json"), ensure_ascii=False, indent=2))
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def update(patch: dict) -> AppSettings:
    """Merge a partial update.  ``ai.api_key`` is only replaced when given;
    ``ai.clear_api_key: true`` removes it."""
    with _lock:
        cur = load().model_dump(mode="json")
        simple_patch = dict(patch.get("simple") or {})
        if simple_patch.pop("reset_karaoke", False):
            simple_patch["karaoke"] = simple_default_style().model_dump(mode="json")
            cur["simple"].pop("karaoke", None)
        ai_patch = dict(patch.get("ai") or {})
        clear = bool(ai_patch.pop("clear_api_key", False))
        if not ai_patch.get("api_key"):
            ai_patch.pop("api_key", None)
        for key, sub in (("ai", ai_patch), ("simple", simple_patch)):
            cur[key] = _merge(cur[key], sub)
        for key in ("check_updates", "hardware_encoding"):
            if key in patch:
                cur[key] = bool(patch[key])
        if clear:
            cur["ai"]["api_key"] = ""
        s = AppSettings.model_validate(cur)
        save(s)
        return s


def _merge(base: dict, over: dict) -> dict:
    """Nested merge, so a partial style ({"karaoke": {"timing": {...}}}) keeps the rest."""
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def public(s: AppSettings) -> dict:
    """Settings as sent to the browser (no secret)."""
    d = s.model_dump(mode="json")
    key = d["ai"].pop("api_key")
    d["ai"]["has_api_key"] = bool(key)
    d["ai"]["env_key_present"] = bool(os.environ.get(s.ai.api_key_env or ""))
    return d


def api_key(s: AiSettings) -> str:
    return s.api_key or os.environ.get(s.api_key_env or "", "")
