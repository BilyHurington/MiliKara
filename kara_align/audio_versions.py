"""The sound of a karaoke video, and making several at once (原唱, 伴唱, 人声 20 %, 无声).

A choice is a list of versions, each giving one video:

- ``original``: the song as it is;
- ``instrumental``: the vocals removed (the separated accompaniment);
- ``mix``: the vocals lowered to ``vocal_keep_pct``;
- ``none``: no sound.

Before v1.2.0 one version was kept as a plain string; :func:`normalize` reads both.
"""

from __future__ import annotations

from typing import Any, Literal

AudioVersion = Literal["original", "instrumental", "mix", "none"]
ORDER: tuple[str, ...] = ("original", "instrumental", "mix", "none")
NEEDS_STEMS = ("instrumental", "mix")


def normalize(value: Any) -> list[str]:
    """A string (the one version kept before v1.2.0) or a list → the versions, once each, in ORDER."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ValueError("视频声音应为列表，例如 [\"original\", \"instrumental\"]")
    bad = [v for v in value if v not in ORDER]
    if bad:
        raise ValueError(f"未知的视频声音：{bad[0]}（可选 {' / '.join(ORDER)}）")
    if not value:
        raise ValueError("至少选择一种视频声音")
    return [v for v in ORDER if v in value]


def label(version: str, vocal_keep_pct: float) -> str:
    return {"original": "原唱", "instrumental": "伴唱", "none": "无声"}.get(
        version, f"人声 {round(vocal_keep_pct)}%")


def plan(versions: list[str], vocal_keep_pct: float, stems: bool) -> tuple[list[dict], list[str]]:
    """The videos to make: [{"version", "label", "audio" (for karaoke_burn), "vocal_keep_pct"}], and
    warnings.  Without separated stems the versions that need them are left out (the original sound
    instead when nothing is left); the same sound is made once."""
    out: list[dict] = []
    warnings: list[str] = []
    seen: set[tuple] = set()
    lost = [v for v in versions if v in NEEDS_STEMS and not stems]
    for v in versions:
        if v in lost:
            continue
        pct = 0.0 if v == "instrumental" else float(vocal_keep_pct)
        audio = "mix" if v in NEEDS_STEMS else v
        key = (audio, pct if audio == "mix" else None)
        if key in seen:  # 人声 0% is the 伴唱 already chosen
            continue
        seen.add(key)
        out.append({"version": v, "label": label(v, pct), "audio": audio, "vocal_keep_pct": pct})
    if lost:
        names = "、".join(label(v, vocal_keep_pct) for v in lost)
        if out:
            warnings.append(f"没有人声分轨：{names} 的视频没有生成")
        else:
            warnings.append("没有人声分轨，视频使用原声")
            out.append({"version": "original", "label": label("original", 0), "audio": "original",
                        "vocal_keep_pct": float(vocal_keep_pct)})
    return out, warnings
