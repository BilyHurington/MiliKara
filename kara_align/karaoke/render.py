"""Preview frames and burned-in karaoke videos (ffmpeg + libass).

Both use the same ASS and the same renderer, so the preview is what the burned
video will look like. Times: the ASS is on the audio timeline; with a video
the audio starts ``video.audio_offset_s`` after the (normalized) video start.
"""

from __future__ import annotations

import functools
import json
import logging
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

from ..audio.io import AudioError, ffmpeg_path

log = logging.getLogger(__name__)


class RenderError(AudioError):
    pass


@functools.lru_cache(maxsize=1)
def _encoders() -> str:
    out = subprocess.run([ffmpeg_path(), "-hide_banner", "-encoders"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return out.stdout


def _bitrate(quality: str, size: tuple[int, int]) -> str:
    """For the encoders without a constant-quality mode: 10 / 16 Mbit/s at 1080p, by frame area."""
    w, h = size
    return f"{max(2, round((16 if quality == 'high' else 10) * w * h / (1920 * 1080)))}M"


def hardware_encoders(quality: str, size: tuple[int, int]) -> list[tuple[str, list[str]]]:
    """The GPU H.264 encoders to try, best first: NVIDIA, Intel, AMD, Apple."""
    hq = quality == "high"
    return [
        ("h264_nvenc", ["-preset", "p6" if hq else "p5", "-tune", "hq", "-rc", "vbr", "-cq", "18" if hq else "21",
                        "-b:v", "0", "-pix_fmt", "yuv420p"]),
        ("h264_qsv", ["-preset", "slow" if hq else "medium", "-global_quality", "18" if hq else "21", "-pix_fmt", "nv12"]),
        ("h264_amf", ["-quality", "quality", "-rc", "cqp", "-qp_i", "18" if hq else "21", "-qp_p", "20" if hq else "23",
                      "-pix_fmt", "yuv420p"]),
        ("h264_videotoolbox", ["-b:v", _bitrate(quality, size), "-pix_fmt", "yuv420p"]),
    ]


@functools.lru_cache(maxsize=8)
def _encoder_works(name: str, args: tuple[str, ...]) -> bool:
    """Listed is not usable (an NVENC build without an NVIDIA card, a driver too old …): encode a few frames."""
    if name not in _encoders():
        return False
    try:
        r = subprocess.run([ffmpeg_path(), "-v", "error", "-nostdin", "-f", "lavfi", "-i", "color=c=black:s=640x360:r=30:d=0.2",
                            "-c:v", name, *args, "-f", "null", "-"], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def software_encoder(quality: str) -> list[str]:
    if "libx264" in _encoders():
        preset, crf = ("veryfast", "20") if quality != "high" else ("medium", "17")
        return ["-c:v", "libx264", "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p"]
    return ["-c:v", "mpeg4", "-q:v", "2"]


def video_encoder(quality: str, size: tuple[int, int] = (1920, 1080), hardware: bool = False) -> list[str]:
    """The encoder of a burn: a working GPU encoder when ``hardware``, libx264 otherwise (and
    whenever no GPU encoder works); VideoToolbox also without libx264."""
    for name, args in hardware_encoders(quality, size):
        if (hardware or (name == "h264_videotoolbox" and "libx264" not in _encoders())) and _encoder_works(name, tuple(args)):
            return ["-c:v", name, *args]
    return software_encoder(quality)


def prefer_hardware(decodes_video: bool) -> bool:
    """The setting (on by default), where it helps: Apple Silicon's libx264 outruns VideoToolbox on a
    plain background, and gains on a decoded video only (measured: 60 s of 1080p with subtitles,
    black 6.4 s vs 8.6 s, a music video 10.3 s vs 8.7 s); a GPU encoder elsewhere takes the load off
    CPUs that are often slower."""
    import sys

    from .. import settings as app_settings

    try:
        on = app_settings.load().hardware_encoding
    except Exception:
        on = True
    return on and (decodes_video or sys.platform != "darwin")


def _subtitles_filter(ass_name: str) -> str:
    # run ffmpeg inside the temp dir so the file name needs no escaping; the bundled fonts' folder
    # (fontsdir: libass loads its fonts itself, whatever font provider it uses) is escaped
    from .fonts import fonts_dir

    d = fonts_dir()
    if d is None:
        return f"subtitles={ass_name}"
    return f"subtitles={ass_name}:fontsdir={filter_path(d)}"


def filter_path(p) -> str:
    """A path as a filter option value inside an ffmpeg filter graph, escaped at both levels: the
    option value (``\\ ' :`` — a drive letter's colon would end the option) and then the graph
    (``\\ ' [ ] , ;``).  "/" separators on Windows too."""
    import re

    v = Path(p).resolve().as_posix()
    v = re.sub(r"([\\':])", r"\\\1", v)
    return re.sub(r"([\\'\[\],;])", r"\\\1", v)


def even_size(size: tuple[int, int]) -> tuple[int, int]:
    """A frame size the H.264 / yuv420p encoders accept (both sides even)."""
    w, h = size
    return max(2, int(w) // 2 * 2), max(2, int(h) // 2 * 2)


@functools.lru_cache(maxsize=16)
def _probe_frame(path: str, mtime_ns: int, length: int) -> Optional[tuple[int, int]]:
    from ..audio.io import ffprobe_path

    probe = ffprobe_path()
    if not probe:
        return None
    r = subprocess.run([probe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,sample_aspect_ratio:stream_tags=rotate:stream_side_data=rotation",
                        "-of", "json", path], capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        st = json.loads(r.stdout or "{}")["streams"][0]
        w, h = int(st["width"]), int(st["height"])
    except (KeyError, IndexError, ValueError, TypeError):
        return None
    num, _, den = str(st.get("sample_aspect_ratio") or "1:1").partition(":")
    try:
        sar = float(num) / float(den) if float(num) > 0 and float(den) > 0 else 1.0
    except ValueError:
        sar = 1.0
    rot = 0.0
    for v in [(st.get("tags") or {}).get("rotate")] + [sd.get("rotation") for sd in st.get("side_data_list") or []]:
        try:
            rot = float(v) if v is not None else rot
        except (TypeError, ValueError):
            pass
    w = round(w * sar)  # square pixels: the width as displayed
    if int(round(abs(rot))) % 180 == 90:  # ffmpeg turns it upright while decoding
        w, h = h, w
    return max(2, w), max(2, h)


def frame_size(video: Path, fallback: tuple[int, int]) -> tuple[int, int]:
    """The size ``video`` is shown at: its sample aspect ratio applied (square pixels) and turned
    upright.  Subtitles are laid out for this size (PlayRes) and the video is scaled to it before
    they are drawn, so text is never stretched by non-square pixels."""
    try:
        st = Path(video).stat()
        return _probe_frame(str(video), st.st_mtime_ns, st.st_size) or fallback
    except OSError:
        return fallback


def _ass_seconds(t: str) -> float:
    h, m, sec = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(sec)


def _shown_at(ass_text: str, t: float) -> str:
    """The subtitles with only the events shown at ``t`` (pictures make a song's subtitles megabytes long:
    libass need not read them all for one frame)."""
    out = []
    for line in ass_text.splitlines():
        if line.startswith("Dialogue:"):
            try:
                _, start, end, _ = line.split(",", 3)
                if not (_ass_seconds(start) - 0.02 <= t <= _ass_seconds(end) + 0.02):
                    continue
            except ValueError:
                pass
        out.append(line)
    return "\n".join(out) + "\n"


def preview_png(ass_text: str, t_ms: int, size: tuple[int, int], video: Optional[Path] = None,
                audio_offset_s: float = 0.0, background: Optional[tuple[Path, str, Optional[int]]] = None) -> bytes:
    """One frame at audio time ``t_ms``: the video frame there, the background's (path, kind,
    duration_ms) frame there, or black."""
    w, h = size
    t = max(0.0, t_ms / 1000.0)
    with tempfile.TemporaryDirectory() as td:
        Path(td, "k.ass").write_text(_shown_at(ass_text, t), encoding="utf-8")
        out = Path(td, "p.png")
        # the frame gets pts = t so the subtitles filter draws the state at t
        # millisecond timebase first: a 1 fps source would round t to whole seconds
        vf = f"settb=1/1000,setpts=PTS-STARTPTS+{t:.3f}/TB,{_subtitles_filter('k.ass')}"
        if background is not None:
            from .background import cover_filter, input_args

            inp = input_args(background[0], background[1], at_s=t, duration_ms=background[2])  # type: ignore[arg-type]
            vf = f"{cover_filter(w, h)},{vf}"
        elif video is not None:
            inp = ["-ss", f"{t + audio_offset_s:.3f}", "-i", str(video)]
            vf = f"scale={w}:{h},{vf}"
        else:
            inp = ["-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r=1:d=1"]
        cmd = [ffmpeg_path(), "-v", "error", "-nostdin", "-y", *inp, "-vf", vf, "-frames:v", "1", str(out)]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=td)
        if r.returncode != 0 or not out.exists():
            raise RenderError(f"预览渲染失败：{r.stderr.strip()[-300:]}")
        return out.read_bytes()


def burn(ass_text: str, out_path: Path, size: tuple[int, int], duration_ms: int, *,
         video: Optional[Path] = None, audio: Optional[Path] = None, audio_offset_s: float = 0.0,
         use_video_audio: bool = False, quality: str = "standard", cancel=None,
         progress: Optional[Callable[[float, str], None]] = None,
         background: Optional[tuple[Path, str]] = None) -> Path:
    """Render subtitles into a video (the source video, a background, or black) of ``size``.

    ``size`` is the frame the subtitles were laid out for (their PlayRes, see frame_size());
    the source video is scaled to it (square pixels) and both sides are made even, as the
    yuv420p encoders require (odd sizes would fail).
    ``audio``: a file to use as the soundtrack (placed at ``audio_offset_s``);
    ``use_video_audio``: keep the source video's first audio stream instead.
    ``background``: (path, "image" | "video") shown instead: a picture, or a video looped for the
    whole song (its own sound is never used), scaled to cover the frame.
    """
    from ..interfaces import Cancelled

    w, h = even_size(size)
    dur = max(0.1, duration_ms / 1000.0)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # written next to the target and moved into place only when complete: a cancelled or failed
    # burn never leaves half a video, nor truncates an earlier one of the same name
    part = out_path.with_name(f".{out_path.stem}.part{out_path.suffix}")
    with tempfile.TemporaryDirectory() as td:
        Path(td, "k.ass").write_text(ass_text, encoding="utf-8")
        cmd = [ffmpeg_path(), "-v", "error", "-nostdin", "-y", "-progress", "pipe:1", "-nostats"]
        limit: list[str] = []
        if background is not None:
            from .background import cover_filter, input_args

            cmd += input_args(background[0], background[1])  # type: ignore[arg-type]
            vf = f"{cover_filter(w, h)},{_subtitles_filter('k.ass')}"
            limit = ["-t", f"{dur + audio_offset_s:.3f}"]  # an endless input: the song's length
        elif video is not None:
            cmd += ["-i", str(video)]
            vf = f"scale={w}:{h},setsar=1,{_subtitles_filter('k.ass')}"
        else:
            cmd += ["-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r=30:d={dur + audio_offset_s:.3f}"]
            vf = _subtitles_filter("k.ass")
        maps = ["-map", "0:v:0"]
        if audio is not None:
            if audio_offset_s > 0:
                cmd += ["-itsoffset", f"{audio_offset_s:.6f}"]
            cmd += ["-i", str(audio)]
            maps += ["-map", "1:a:0", "-c:a", "aac", "-b:a", "256k"]
        elif use_video_audio and video is not None:
            maps += ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "256k"]
        else:
            maps += ["-an"]
        encoder = video_encoder(quality, (w, h), prefer_hardware(video is not None or (background or ("", ""))[1] == "video"))
        total = dur + audio_offset_s
        while True:
            cmd_enc = cmd + ["-vf", vf, *maps, *limit, *encoder, "-movflags", "+faststart", "-f", "mp4", str(part.resolve())]
            rc, err = _run_burn(cmd_enc, td, part, total, cancel, progress, Cancelled)
            if rc == 0 and part.exists():
                break
            part.unlink(missing_ok=True)
            fallback = software_encoder(quality)
            if encoder == fallback:
                raise RenderError(f"烧录失败：{err.strip()[-400:]}")
            # a GPU encoder that passed the probe can still fail on this video (its size, the driver …)
            log.warning("GPU encoder %s failed, burning with %s: %s", encoder[1], fallback[1], err.strip()[-400:])
            encoder = fallback
            if progress:
                progress(0.0, "显卡编码失败，改用 CPU 编码")
        part.replace(out_path)
    return out_path


def _run_burn(cmd: list[str], td: str, part: Path, total: float, cancel, progress, Cancelled) -> tuple[int, str]:
    # stderr goes to a file: an undrained pipe could block ffmpeg
    with open(Path(td, "err.log"), "w+", encoding="utf-8", errors="replace") as err_file:
        proc = subprocess.Popen(cmd, cwd=td, stdout=subprocess.PIPE, stderr=err_file, text=True, encoding="utf-8", errors="replace", bufsize=1)
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if cancel is not None and getattr(cancel, "cancelled", False):
                    proc.terminate()
                    raise Cancelled()
                if line.startswith("out_time_us=") and progress:
                    try:
                        done = int(line.split("=", 1)[1]) / 1e6
                    except ValueError:
                        continue
                    progress(min(0.99, done / total), f"烧录中 {min(100, int(done / total * 100))}%")
            proc.wait()
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        finally:
            if proc.poll() is None:
                proc.kill()
                time.sleep(0.1)
        err_file.seek(0)
        return proc.returncode, err_file.read()


def ffmpeg_available() -> bool:
    return shutil.which(ffmpeg_path()) is not None
