"""Make the updater and the update files of a release (used by the packaging workflows).

    python packaging/updater/build.py package <folder> --platform windows|macos [--variant cpu|cuda|rocm]
        更新.bat / 更新.command and version.json into a portable package folder

    python packaging/updater/build.py release <out> --tag v1.2.0 [--wheel dir] [--fonts dir]
        the files a release needs for updating (platform independent):
          MiliKara-<version>-app.zip      the program: the wheel, both launchers + fontconfig files, the
                                          notes of every variant, the licence and manifest.json
          MiliKara-fonts-<id>.zip         the bundled fonts (for folders without them: v1.0.0)
          MiliKara-updater-windows.bat    the updater on its own (to put into a folder from before it)
          MiliKara-updater-macos.zip      the same for macOS (zipped: keeps it executable)
          manifest.json                   version, tag, and name / size / SHA-256 of each of the above

The updater files are update.py behind a one-line launcher that runs it with the folder's own Python:
a cmd line (Python skips it with -x) on Windows, an sh line inside a Python string on macOS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "packaging"))

UPDATER_NAME = {"windows": "更新.bat", "macos": "更新.command"}

# one line, ASCII only (cmd reads batch files in the console code page); everything after it is Python.
# The folder's Python runs this file with -x (skip the first line); a newer updater it fetched (.new) is
# moved over this file once Python has finished: the line is parsed as a whole before it runs.
BAT_HEADER = (
    '@echo off & setlocal & chcp 65001 >nul & set "PYTHONNOUSERSITE=1" & set "PYTHONUTF8=1" & set "PYTHONHOME=" '
    '& set "PYTHONPATH=" & set "VIRTUAL_ENV=" & (if not exist "%~dp0python\\python.exe" (echo Put this file into '
    'the MiliKara folder, next to MiliKara.bat and the python folder. & pause & exit /b 1)) '
    '& "%~dp0python\\python.exe" -x "%~f0" %* & (if exist "%~f0.new" move /y "%~f0.new" "%~f0" >nul) & exit /b'
)

# sh runs the first lines (the second is an empty command for sh and the start of a string for Python)
SH_HEADER = '''#!/bin/sh
"true" \'\'\'\\'
APP="$(cd "$(dirname "$0")" && pwd)"; PY="$APP/python/bin/python3"
if [ ! -x "$PY" ]; then echo "请把这个文件放进 MiliKara 文件夹（和 MiliKara.command、python 文件夹放在一起）再运行。"; read -r _; exit 1; fi
export PYTHONNOUSERSITE=1 PYTHONUTF8=1; unset PYTHONHOME PYTHONPATH VIRTUAL_ENV
exec "$PY" "$0" "$@"
\'\'\'
'''


def updater_source() -> str:
    return (HERE / "update.py").read_text(encoding="utf-8")


def make_updater(platform: str) -> bytes:
    src = updater_source()
    if platform == "windows":
        assert BAT_HEADER.isascii()
        return (BAT_HEADER + "\n" + src).replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")
    return (SH_HEADER + src).encode("utf-8")


def write_updater(platform: str, dst: Path) -> Path:
    dst.write_bytes(make_updater(platform))
    if platform == "macos":
        dst.chmod(0o755)
    return dst


def project_version() -> str:
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return re.search(r'^version\s*=\s*"([^"]+)"', text, re.M).group(1)


def fonts_id() -> str:
    """Identity of the bundled fonts: their pinned checksums (packaging/fetch_fonts.py)."""
    from fetch_fonts import FILES

    return hashlib.sha256("\n".join(d for _, d in sorted(FILES.values())).encode()).hexdigest()[:12]


def file_info(path: Path) -> dict:
    h = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"file": path.name, "size": path.stat().st_size, "sha256": h}


# ---------------------------------------------------------------------------------------------------


def cmd_package(a: argparse.Namespace) -> None:
    root = Path(a.folder)
    write_updater(a.platform, root / UPDATER_NAME[a.platform])
    state = {"version": project_version(), "platform": a.platform, "variant": a.variant if a.platform == "windows" else None,
             "components": {"app": project_version(), "fonts": fonts_id()}}
    (root / "version.json").write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{UPDATER_NAME[a.platform]} + version.json → {root}")


def build_wheel(out: Path) -> Path:
    subprocess.run(["uv", "build", "--wheel", "-q", "-o", str(out)], cwd=REPO_ROOT, check=True)
    return next(out.glob("*.whl"))


def cmd_release(a: argparse.Namespace) -> None:
    import notes

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    version = project_version()
    if a.tag.lstrip("v") != version and not a.allow_mismatch:
        raise SystemExit(f"the tag {a.tag} is not the version in pyproject.toml ({version})")
    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        wheel = next(Path(a.wheel).glob("*.whl")) if a.wheel else build_wheel(tmpd / "wheel")
        # fonts
        fonts_dir = Path(a.fonts) if a.fonts else tmpd / "fonts"
        if not a.fonts:
            subprocess.run([sys.executable, str(REPO_ROOT / "packaging" / "fetch_fonts.py"), str(fonts_dir)], check=True)
        fid = fonts_id()
        fonts_zip = out / f"MiliKara-fonts-{fid}.zip"
        font_files = {}
        with zipfile.ZipFile(fonts_zip, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(fonts_dir.iterdir()):
                if p.is_file() and not p.name.endswith(".part"):
                    z.write(p, p.name)
                    font_files[p.name] = p.stat().st_size
        # the updater on its own
        win = write_updater("windows", out / "MiliKara-updater-windows.bat")
        mac_zip = out / "MiliKara-updater-macos.zip"
        with zipfile.ZipFile(mac_zip, "w", zipfile.ZIP_DEFLATED) as z:
            info = zipfile.ZipInfo(UPDATER_NAME["macos"])
            info.external_attr = (0o100755 << 16)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, make_updater("macos"))
        manifest = {
            "format": 1, "name": "MiliKara", "version": version, "tag": a.tag,
            "full": f"https://github.com/BilyHurington/MiliKara/releases/tag/{a.tag}",
            "updater": {"version": _updater_version(), "windows": file_info(win), "macos": file_info(mac_zip)},
            "components": {"fonts": {"id": fid, **file_info(fonts_zip), "files": font_files}},
        }
        # the program package (it carries the manifest too, for updating without network)
        app_zip = out / f"MiliKara-{version}-app.zip"
        app_manifest = dict(manifest, components=dict(manifest["components"], app={"file": app_zip.name, "size": 0, "sha256": ""}))
        with zipfile.ZipFile(app_zip, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(wheel, wheel.name)
            for plat, launcher in (("windows", "MiliKara.bat"), ("macos", "MiliKara.command")):
                z.write(REPO_ROOT / "packaging" / plat / launcher, f"launchers/{plat}/{launcher}")
                z.write(REPO_ROOT / "packaging" / plat / "fonts.conf", f"launchers/{plat}/fonts.conf")
            for plat, variant, name in (("windows", "cpu", "windows-cpu"), ("windows", "cuda", "windows-cuda"),
                                        ("windows", "rocm", "windows-rocm"), ("macos", None, "macos")):
                z.writestr(f"notes/{name}.txt", notes.encode(plat, notes.render(plat, variant, a.tag)))
            z.write(REPO_ROOT / "LICENSE", "LICENSE")
            z.writestr("manifest.json", json.dumps(app_manifest, ensure_ascii=False, indent=1))
        manifest["components"]["app"] = file_info(app_zip)
        (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    for p in sorted(out.iterdir()):
        print(f"{p.stat().st_size:>12,}  {p.name}")


def _updater_version() -> int:
    return int(re.search(r"^UPDATER_VERSION = (\d+)", updater_source(), re.M).group(1))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("package")
    p.add_argument("folder")
    p.add_argument("--platform", choices=["windows", "macos"], required=True)
    p.add_argument("--variant", choices=["cpu", "cuda", "rocm"])
    p.set_defaults(fn=cmd_package)
    r = sub.add_parser("release")
    r.add_argument("out")
    r.add_argument("--tag", required=True)
    r.add_argument("--wheel", help="a folder with the wheel already built (default: build it with uv)")
    r.add_argument("--fonts", help="a folder with the fonts already fetched (default: fetch them)")
    r.add_argument("--allow-mismatch", action="store_true", help="a tag that is not the project version (tests)")
    r.set_defaults(fn=cmd_release)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    main()
