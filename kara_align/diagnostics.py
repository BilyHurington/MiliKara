"""Diagnostics: a log file, and a report a user can copy into a bug report.

The log (``<home>/logs/milikara.log``, rotated: a few MB at most) gets failed tasks and jobs with
their traceback and the server's own errors.  ``report()`` says what the program runs on (version,
system, Python, torch / CUDA / MPS, ffmpeg and libass, the relevant settings — never an API key), a
failure's details when asked for one, and the end of the log.
"""

import logging
import logging.handlers
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Optional

from . import __version__

log = logging.getLogger("kara_align")
LOG_NAME = "milikara.log"
_configured: Optional[Path] = None


def log_path() -> Path:
    from .project.store import home_dir

    return home_dir() / "logs" / LOG_NAME


def setup_logging() -> Optional[Path]:
    """Log to the file (once per process); the console keeps showing only warnings."""
    global _configured
    if _configured is not None:
        return _configured
    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    except OSError:
        return None
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    for name in ("kara_align", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.addHandler(handler)
        if lg.level == logging.NOTSET or lg.level > logging.INFO:
            lg.setLevel(logging.INFO)
    log.propagate = False
    log.info("MiliKara %s started (Python %s, %s)", __version__, platform.python_version(), platform.platform())
    _configured = path
    return path


def failure(what: str, error: str, detail: Optional[str]) -> None:
    """A failed task / job, with its traceback, into the log."""
    log.error("%s failed: %s\n%s", what, error, (detail or "").rstrip())


def _tail(path: Path, lines: int) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 64_000))
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def _torch() -> str:
    try:
        import torch
    except Exception:
        return "未安装"
    parts = [torch.__version__]
    try:
        if torch.cuda.is_available():
            from .gpu import describe

            parts.append(describe(torch))
        elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            parts.append("MPS")
        else:
            parts.append("CPU")
    except Exception as e:
        parts.append(f"（检测显卡出错：{e}）")
    return " · ".join(parts)


def _ffmpeg() -> str:
    try:
        from .audio.io import ffmpeg_path

        exe = ffmpeg_path()
        out = subprocess.run([exe, "-hide_banner", "-version"], capture_output=True, text=True, timeout=20,
                             encoding="utf-8", errors="replace").stdout
        first = out.splitlines()[0] if out else "?"
        filters = subprocess.run([exe, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=20,
                                 encoding="utf-8", errors="replace").stdout
        libass = "有 libass" if " subtitles " in filters else "没有 libass（不能生成字幕视频）"
        return f"{first} · {libass} · {exe}"
    except Exception as e:
        return f"找不到或无法运行（{e}）"


def _encoder() -> str:
    try:
        from .karaoke.render import prefer_hardware, video_encoder

        return f"{video_encoder('standard', hardware=prefer_hardware(True))[1]}（烧录进视频时）"
    except Exception as e:
        return f"?（{e}）"


def report(task: Optional[dict] = None, job: Optional[dict] = None) -> str:
    from . import settings as app_settings
    from . import updates

    lines = [
        "MiliKara 诊断信息",
        f"版本：{__version__}（{'离线版' if updates.portable_root() else '源码安装'}）",
        f"系统：{platform.platform()} · {platform.machine()}",
        f"Python：{platform.python_version()} · {sys.executable}",
        f"torch：{_torch()}",
        f"ffmpeg：{_ffmpeg()}",
        f"视频编码：{_encoder()}",
    ]
    try:
        s = app_settings.load()
        ai = f"开 · {s.ai.provider}" if s.ai.enabled else "关"
        lines.append(f"设置：AI 注音 {ai} · 人声分离 {'开' if s.simple.separate else '关'}（{s.simple.separation_preset}，"
                     f"{s.simple.separation_device}）· 校准 {s.simple.calibration}")
    except Exception as e:
        lines.append(f"设置：读取失败（{e}）")
    for name, item in (("任务", task), ("操作", job)):
        if item:
            lines += ["", f"失败的{name}：{item.get('kind') or item.get('name') or ''} {item.get('id', '')}".rstrip(),
                      f"错误：{item.get('error') or ''}"]
            if item.get("stages"):
                lines.append("步骤：" + " · ".join(f"{st.get('label') or st.get('key')} {st.get('status')}"
                                                 for st in item["stages"]))
            if item.get("detail"):
                lines += ["详细：", item["detail"].rstrip()]
    tail = _tail(log_path(), 60)
    if tail:
        lines += ["", f"日志（{log_path()} 的最后部分）：", tail]
    text = "\n".join(lines) + "\n"
    home = str(Path.home())
    return text.replace(home, "~") if len(home) > 3 else text  # (the user name stays private when shared)
