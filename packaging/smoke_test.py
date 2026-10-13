"""Check a built portable MiliKara folder the way a user runs it, through its launcher only:

1. the WebUI starts (launcher without arguments) and answers /api/info;
2. a whole song goes through: lyrics (LRC) → vocal separation → alignment → karaoke video, offline,
   with the bundled models and ffmpeg;
3. the video has the lyrics drawn on it (libass found a Japanese font).

    python smoke_test.py <MiliKara folder> [<work folder>]

The test song is synthetic (tones), so the timing itself means nothing here; the model and every
step still run for real.  The Python running this script is only used to check the output (Pillow).
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

LRC = "[ti:smoke]\n[00:02.00]きみとあるいた\n[00:06.00]はるのみち\n[00:10.00]ゆめをみた\n"


def main() -> None:
    app = Path(sys.argv[1]).resolve()
    work = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else Path(tempfile.mkdtemp(prefix="milikara-smoke-"))
    work.mkdir(parents=True, exist_ok=True)
    launcher = app / ("MiliKara.bat" if os.name == "nt" else "MiliKara.command")
    env = {**os.environ, "KARA_ALIGN_HOME": str(work / "home"), "HF_HUB_CACHE": str(work / "hf-cache"),
           "HF_HOME": str(work / "hf-home"), "MILIKARA_NO_BROWSER": "1"}
    for k in ("KARA_ALIGN_MODELS", "KARA_ALIGN_FONTS", "KARA_ALIGN_FFMPEG", "FONTCONFIG_FILE", "VIRTUAL_ENV", "PYTHONPATH"):
        env.pop(k, None)
    models_before = _listing(app / "models")

    def kk(*args: str, timeout: float = 1800) -> str:
        print("$ MiliKara", *args, flush=True)
        t0 = time.monotonic()
        r = subprocess.run(_launch([str(launcher), *args]), env=env, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        print(r.stdout[-3000:], r.stderr[-3000:], sep="\n", flush=True)
        print(f"  ({time.monotonic() - t0:.1f}s, exit {r.returncode})", flush=True)
        if r.returncode != 0:
            raise SystemExit(f"failed: {' '.join(args)}")
        return r.stdout

    serve(launcher, env)

    ffmpeg = app / "ffmpeg" / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    song = work / "song.wav"
    # a "voice" (tones gliding every half second) over a quiet hum: enough for every step to have work
    subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "sine=f=220:d=16", "-f", "lavfi", "-i", "sine=f=660:d=16",
                    "-filter_complex", "[1]volume='0.6*gt(mod(t,0.5),0.25)':eval=frame[v];[0][v]amix=2,volume=2",
                    "-ar", "44100", "-ac", "2", str(song)], check=True)
    lrc = work / "lyrics.lrc"
    lrc.write_text(LRC, encoding="utf-8")
    proj = work / "proj"

    kk("--version")
    kk("init", str(proj), "--name", "smoke", "--mode", "lrc")
    kk("lyrics", str(proj), str(lrc))
    kk("audio", str(proj), str(song))
    # SMOKE_DEVICE=cpu: a machine without a usable GPU that still reports one (macOS CI: MPS in a VM)
    device = os.environ.get("SMOKE_DEVICE", "auto")
    # SMOKE_SEPARATION=0: not separated here, aligned on the song itself — the AMD (ROCm) package on a
    # runner without an AMD card, where its torch computes on the CPU far too slowly (one chunk of the
    # separator takes over 10 minutes there).  The separation itself is the CPU package's, checked there.
    separate = os.environ.get("SMOKE_SEPARATION", "1") != "0"
    if separate:
        kk("separate", str(proj), "--device", device)
    kk("calibrate", str(proj), "--confirm-zero")
    kk("readings", str(proj))
    kk("align", str(proj), "--role", "vocals" if separate else "original",
       *(["--set", f"device={device}"] if device != "auto" else []))
    out = kk("burn", str(proj), "--background", "black")
    video = Path(json.loads(out)["file"])
    assert video.is_file() and video.stat().st_size > 10_000, video

    # the first line is sung from 2 s on: a frame there has text on the black background
    frame = work / "frame.png"
    subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-ss", "3", "-i", str(video),
                    "-frames:v", "1", str(frame)], check=True)
    lit = _lit_fraction(frame)
    print(f"frame at 3 s: {lit:.4%} of pixels lit", flush=True)
    if lit < 0.001:
        raise SystemExit("no subtitles drawn on the video (no Japanese font found by libass?)")

    assert _listing(app / "models") == models_before, "the bundled models changed"
    hf = [p for p in (work / "hf-cache").rglob("*") if p.is_file()] if (work / "hf-cache").exists() else []
    assert not hf, f"something was downloaded into the Hugging Face cache: {hf[:5]}"
    print(f"OK: {video} ({video.stat().st_size:,} bytes)")


def serve(launcher: Path, env: dict) -> None:
    print("$ MiliKara   (WebUI)", flush=True)
    kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    proc = subprocess.Popen(_launch([str(launcher)]), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", **kw)
    try:
        url = None
        t0 = time.monotonic()
        lines: list[str] = []
        while time.monotonic() - t0 < 120:
            line = proc.stdout.readline()
            if not line:
                break
            lines.append(line)
            m = re.search(r"MiliKara WebUI: (http://\S+)", line)
            if m:
                url = m.group(1)
                break
        if not url:
            raise SystemExit("the WebUI did not start:\n" + "".join(lines))
        while time.monotonic() - t0 < 120:
            try:
                with urllib.request.urlopen(url + "/api/info", timeout=5) as r:
                    info = json.loads(r.read())
                print(f"  {url} answers: {json.dumps(info, ensure_ascii=False)[:300]}", flush=True)
                page = urllib.request.urlopen(url + "/", timeout=5).read()
                assert b"<div id=\"root\"" in page or b"id=root" in page, "no WebUI page"
                return
            except OSError:
                time.sleep(0.5)
        raise SystemExit(f"the WebUI at {url} did not answer")
    finally:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        else:
            import signal

            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except OSError:  # already ended
                pass
        proc.wait(30)


def _launch(cmd: list[str]):
    # a batch file with a quoted path and quoted arguments needs cmd /s (see kara_align.procs.command)
    if os.name == "nt":
        return f'cmd.exe /d /s /c "{subprocess.list2cmdline(cmd)}"'
    return cmd


def _lit_fraction(png: Path) -> float:
    from PIL import Image

    hist = Image.open(png).convert("L").histogram()
    return sum(hist[61:]) / sum(hist)


def _listing(d: Path) -> dict:
    return {str(p.relative_to(d)): p.stat().st_size for p in d.rglob("*") if p.is_file()}


if __name__ == "__main__":
    main()
