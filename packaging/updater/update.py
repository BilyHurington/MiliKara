"""MiliKara updater: brings a portable MiliKara folder up to the latest release.

This is the Python part of 更新.bat (Windows) and 更新.command (macOS), which packaging/updater/build.py
makes by putting a one-line launcher in front of it; the launcher runs it with the folder's own Python.
It also works in folders from before the updater existed (v1.0.0, still named KiraKara): put the file
into the folder and double-click it.

Only what changed is downloaded: normally the program (about 1 MB); the fonts when they are missing
(folders from v1.0.0).  Python, the dependencies (torch …), ffmpeg and the models stay.  When a new
version needs other dependencies, it says so and points to the full package instead.

Before anything is replaced, the current files are moved to update/backup/<version>/; if the new version
does not start, they are put back.  Running it again offers "退回上一版".

Sources: the latest GitHub release (manifest.json and the files it lists), or a program package
(MiliKara-<version>-app.zip, and the fonts package if needed) put into the folder: no network needed.
MILIKARA_UPDATE_BASE replaces https://github.com/<repo>/releases (a mirror; also used by the tests).

Standard library only (certifi and packaging are used when the folder's Python has them).
"""


import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Optional

UPDATER_VERSION = 1
REPO = "BilyHurington/MiliKara"
RELEASES = f"https://github.com/{REPO}/releases"
USER_AGENT = f"MiliKara-updater/{UPDATER_VERSION}"
WIN = os.name == "nt"


class UpdateError(Exception):
    """A failure explained to the user (no traceback)."""


# ---------------------------------------------------------------------------------------------------
# output


def say(msg: str = "") -> None:
    print(msg, flush=True)


def ask(prompt: str, auto: Optional[bool]) -> bool:
    """Yes / no; Enter = yes.  ``auto``: the answer without asking (--yes, or no terminal)."""
    if auto is not None:
        return auto
    try:
        answer = input(f"{prompt}（按 Enter 继续，输入 n 取消）：").strip().lower()
    except EOFError:
        return False
    return answer not in ("n", "no", "否", "不")


def mb(n: float) -> str:
    return f"{n / 1e6:.1f} MB" if n >= 1e5 else f"{n / 1e3:.0f} KB"


# ---------------------------------------------------------------------------------------------------
# the folder


class Folder:
    """A portable MiliKara folder: where its Python, packages and program files are."""

    def __init__(self, root: Path):
        self.root = root
        if (root / "python" / "python.exe").exists():
            self.platform, self.py = "windows", root / "python" / "python.exe"
            self.site = root / "python" / "Lib" / "site-packages"
        elif (root / "python" / "bin" / "python3").exists():
            self.platform, self.py = "macos", root / "python" / "bin" / "python3"
            sites = sorted((root / "python" / "lib").glob("python3.*/site-packages"))
            if not sites:
                raise UpdateError(f"{root} 里的 Python 不完整（没有 site-packages）")
            self.site = sites[-1]
        else:
            raise UpdateError(f"这个文件夹里没有 MiliKara：{root}\n"
                              "请把更新程序放进 MiliKara（或旧版 KiraKara）的解压文件夹里，和 python 文件夹放在一起再运行。")
        self.update_dir = root / "update"

    # --- what is installed

    def app_dists(self) -> list[Path]:
        return sorted(p for p in self.site.glob("*.dist-info")
                      if re.match(r"(milikara|kirakara|kara_align|kara-align)-", p.name, re.I))

    def variant(self) -> Optional[str]:
        if self.platform != "windows":
            return None
        for p in self.site.glob("torch-*.dist-info"):
            return "cuda" if "+cu" in p.name else "rocm" if "+rocm" in p.name else "cpu"
        return "cpu"

    def state(self) -> dict:
        """version / platform / variant / label of what is here (version.json; else guessed: folders
        from before the updater have none)."""
        vj = self.root / "version.json"
        if vj.exists():
            with contextlib.suppress(Exception):
                d = json.loads(vj.read_text(encoding="utf-8"))
                d.setdefault("platform", self.platform)
                d.setdefault("variant", self.variant())
                d["label"] = f"MiliKara {d['version']}"
                return d
        version, label = "0", "未知版本"
        for dist in self.app_dists():
            m = re.match(r"([A-Za-z_\-]+)-(.+)\.dist-info$", dist.name)
            if not m:
                continue
            name, v = m.group(1).lower(), m.group(2)
            if name == "kirakara":  # the previews (published as v1.0.0) were built from version 0.1.0
                version, label = "1.0.0", "KiraKara 1.0.0 预览版"
            else:
                version, label = v, f"MiliKara {v}"
        return {"version": version, "platform": self.platform, "variant": self.variant(), "label": label,
                "components": {}}

    def launcher(self) -> Path:
        return self.root / ("MiliKara.bat" if self.platform == "windows" else "MiliKara.command")

    def env(self) -> dict:
        """The environment the launcher gives the program."""
        env = dict(os.environ, PYTHONNOUSERSITE="1", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
                   HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                   KARA_ALIGN_MODELS=str(self.root / "models"), KARA_ALIGN_FONTS=str(self.root / "fonts"))
        for k in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
            env.pop(k, None)
        return env

    def running(self) -> list[str]:
        """Command lines of a MiliKara (KiraKara) of this folder that is still running."""
        root = str(self.root).lower()
        try:
            if WIN:
                out = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ForEach-Object { $_.CommandLine }"],
                    capture_output=True, text=True, timeout=30).stdout
            else:
                out = subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True, timeout=30).stdout
        except Exception:
            return []
        return [ln for ln in out.splitlines() if "kara_align.cli" in ln and root in ln.lower()]


# ---------------------------------------------------------------------------------------------------
# versions and requirements


def vkey(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v.split("+")[0])[:4]) or (0,)


def unmet_requirements(wheel: Path) -> list[str]:
    """The new program's dependencies this Python does not have (a version that does not fit, or none).
    Checked with ``packaging`` when available; without it nothing is reported here (the start check
    after installing still catches a missing one)."""
    try:
        from importlib import metadata

        from packaging.requirements import Requirement
    except Exception:
        return []
    with zipfile.ZipFile(wheel) as z:
        meta = next(n for n in z.namelist() if n.endswith(".dist-info/METADATA"))
        lines = z.read(meta).decode("utf-8").splitlines()
    missing = []
    for ln in lines:
        if not ln.startswith("Requires-Dist:"):
            continue
        try:
            req = Requirement(ln.split(":", 1)[1].strip())
        except Exception:
            continue
        if req.marker is not None and not any(req.marker.evaluate({"extra": e}) for e in ("", "ml", "separation")):
            continue  # dev tools, or not for this system
        try:
            have = metadata.version(req.name)
        except metadata.PackageNotFoundError:
            missing.append(f"{req.name}（没有安装）")
            continue
        if req.specifier and not req.specifier.contains(have, prereleases=True):
            missing.append(f"{req.name} {req.specifier}（现在是 {have}）")
    return missing


# ---------------------------------------------------------------------------------------------------
# downloading


def ssl_context():
    import ssl

    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def base_url() -> str:
    return os.environ.get("MILIKARA_UPDATE_BASE", "").rstrip("/") or RELEASES


def open_url(url: str, headers: Optional[dict] = None, timeout: float = 30):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    ctx = ssl_context() if url.startswith("https:") else None
    return urllib.request.urlopen(req, timeout=timeout, context=ctx)


def get_json(url: str) -> Any:
    last: Exception = UpdateError("")
    for attempt in range(3):
        try:
            with open_url(url) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            time.sleep(1 + attempt)
    raise UpdateError(f"无法连接 GitHub（{last}）。\n"
                      "可以开着代理再试，或者从网盘下载程序更新包（MiliKara-版本-app.zip）放到这个文件夹里再运行。")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path, size: int, digest: str, label: str) -> Path:
    """Download to ``dest`` (resuming a .part left by an earlier attempt), checked against ``digest``."""
    if dest.exists() and dest.stat().st_size == size and sha256(dest) == digest:
        return dest
    part = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(4):
        have = part.stat().st_size if part.exists() else 0
        if have > size:
            part.unlink()
            have = 0
        try:
            with open_url(url, {"Range": f"bytes={have}-"} if have else None, timeout=60) as r:
                if have and getattr(r, "status", 200) != 206:
                    have = 0  # the server sent everything again
                with open(part, "ab" if have else "wb") as f:
                    got, shown = have, 0.0
                    while chunk := r.read(1 << 16):
                        f.write(chunk)
                        got += len(chunk)
                        if time.monotonic() - shown > 0.3 or got >= size:
                            shown = time.monotonic()
                            pct = 100 * got / size if size else 100
                            print(f"\r  下载{label}：{mb(got)} / {mb(size)}（{pct:.0f}%）", end="", flush=True)
            print(flush=True)
            if part.stat().st_size != size or sha256(part) != digest:
                part.unlink()
                raise UpdateError(f"{label}下载后校验不一致（文件不完整或被修改），请重试")
            part.replace(dest)
            return dest
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            print(flush=True)
            if attempt == 3:
                raise UpdateError(f"下载{label}失败：{e}") from e
            say(f"  网络中断，{2 + attempt * 2} 秒后继续下载……")
            time.sleep(2 + attempt * 2)
    raise UpdateError(f"下载{label}失败")


# ---------------------------------------------------------------------------------------------------
# what to install


class Release:
    """A release to install: its manifest and where its files come from (downloaded, or a program
    package put into the folder)."""

    def __init__(self, manifest: dict, files: dict[str, Path], online: bool):
        self.m = manifest
        self.files = files  # component name -> local zip (filled as they are fetched)
        self.online = online

    @property
    def version(self) -> str:
        return self.m["version"]

    def fetch(self, name: str, folder: Folder) -> Optional[Path]:
        if name in self.files:
            return self.files[name]
        comp = self.m["components"].get(name)
        if comp is None or not self.online:
            return None
        url = f"{base_url()}/download/{self.m['tag']}/{comp['file']}"
        label = {"app": "程序", "fonts": "字体"}.get(name, name)
        self.files[name] = download(url, folder.update_dir / "downloads" / comp["file"], comp["size"], comp["sha256"], label)
        return self.files[name]


def latest_online() -> Release:
    return Release(get_json(f"{base_url()}/latest/download/manifest.json"), {}, online=True)


def local_packages(folder: Folder) -> Optional[Release]:
    """A program package (and a fonts package) put into the folder, the newest one."""
    apps = sorted(folder.root.glob("MiliKara-*-app.zip"), key=lambda p: vkey(p.name))
    if not apps:
        return None
    app = apps[-1]
    try:
        with zipfile.ZipFile(app) as z:
            if z.testzip() is not None:
                raise UpdateError(f"{app.name} 已损坏，请重新下载")
            manifest = json.loads(z.read("manifest.json").decode("utf-8"))
    except (zipfile.BadZipFile, KeyError) as e:
        raise UpdateError(f"{app.name} 不是 MiliKara 的程序更新包") from e
    files = {"app": app}
    fonts = manifest["components"].get("fonts")
    if fonts and (folder.root / fonts["file"]).exists():
        files["fonts"] = folder.root / fonts["file"]
    return Release(manifest, files, online=False)


def fonts_ok(folder: Folder, fonts: Optional[dict]) -> bool:
    if not fonts:
        return True
    d = folder.root / "fonts"
    return all((d / name).exists() and (d / name).stat().st_size == size for name, size in fonts["files"].items())


def release_notes(tag: str) -> str:
    """The first lines of the release's description on GitHub (best effort)."""
    if os.environ.get("MILIKARA_UPDATE_BASE"):
        return ""
    try:
        with open_url(f"https://api.github.com/repos/{REPO}/releases/tags/{tag}", timeout=10) as r:
            body = json.loads(r.read().decode("utf-8")).get("body") or ""
    except Exception:
        return ""
    lines = [ln.rstrip() for ln in body.strip().splitlines()]
    return "\n".join(lines[:15]) + ("\n  ……" if len(lines) > 15 else "")


# ---------------------------------------------------------------------------------------------------
# installing (with a backup), rolling back


class Backup:
    """Files moved aside before an update: ``moved`` (paths relative to the folder, now under
    ``dir``) and ``added`` (paths the update created), in ``dir``/restore.json."""

    def __init__(self, folder: Folder, version: str):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.folder = folder
        self.dir = folder.update_dir / "backup" / f"{version}-{stamp}"
        self.moved: list[str] = []
        self.added: list[str] = []
        self.version = version

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "restore.json").write_text(json.dumps(
            {"version": self.version, "moved": self.moved, "added": self.added}, ensure_ascii=False, indent=1),
            encoding="utf-8")

    def move_aside(self, path: Path) -> None:
        if not path.exists():
            return
        rel = path.relative_to(self.folder.root).as_posix()
        dst = self.dir / "files" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(path), str(dst))
        except PermissionError as e:
            raise UpdateError(f"{rel} 正在被使用，无法替换：请先关闭 MiliKara（以及打开着这个文件夹里文件的程序）再试") from e
        self.moved.append(rel)
        self.save()

    def note_added(self, path: Path) -> None:
        rel = path.relative_to(self.folder.root).as_posix()
        if rel not in self.added and rel not in self.moved:
            self.added.append(rel)

    @staticmethod
    def latest(folder: Folder) -> Optional[dict]:
        base = folder.update_dir / "backup"
        found = sorted(base.glob("*/restore.json"), key=lambda p: p.stat().st_mtime) if base.exists() else []
        if not found:
            return None
        d = json.loads(found[-1].read_text(encoding="utf-8"))
        d["dir"] = found[-1].parent
        return d


def restore(folder: Folder, info: dict) -> None:
    """Undo an update: remove what it added, put back what it moved aside."""
    root, bdir = folder.root, Path(info["dir"])
    for rel in info.get("added", []):
        p = root / rel
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            p.unlink()
    for rel in info.get("moved", []):
        src, dst = bdir / "files" / rel, root / rel
        if dst.is_dir():
            shutil.rmtree(dst, ignore_errors=True)
        elif dst.exists():
            dst.unlink()
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    shutil.rmtree(bdir, ignore_errors=True)


def extract_wheel(folder: Folder, wheel: Path, backup: Backup) -> None:
    with zipfile.ZipFile(wheel) as z:
        tops = sorted({n.split("/")[0] for n in z.namelist()})
        for top in tops:
            backup.note_added(folder.site / top)
        z.extractall(folder.site)


def install_app(folder: Folder, rel: Release, app_zip: Path, backup: Backup, state: dict) -> None:
    root, site = folder.root, folder.site
    with tempfile.TemporaryDirectory(dir=folder.update_dir) as tmp:
        tmpd = Path(tmp)
        with zipfile.ZipFile(app_zip) as z:
            z.extractall(tmpd)
        wheel = next(tmpd.glob("*.whl"))
        # the program: aside, then the new one
        backup.move_aside(site / "kara_align")
        for dist in folder.app_dists():
            backup.move_aside(dist)
        extract_wheel(folder, wheel, backup)
        # launchers, notes, licence, fontconfig (the old names go too)
        plat = folder.platform
        old = ["KiraKara.bat", "MiliKara.bat"] if plat == "windows" else ["KiraKara.command", "MiliKara.command"]
        for name in old + ["使用说明.txt", "LICENSE-KiraKara.txt", "LICENSE-MiliKara.txt", "version.json"]:
            backup.move_aside(root / name)
        conf = root / "ffmpeg" / "etc" / "fonts" / "fonts.conf"
        backup.move_aside(conf)
        src = tmpd / "launchers" / plat
        launcher = folder.launcher()
        shutil.copy2(src / launcher.name, launcher)
        conf.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / "fonts.conf", conf)
        notes = tmpd / "notes" / (f"windows-{state.get('variant') or 'cpu'}.txt" if plat == "windows" else "macos.txt")
        shutil.copy2(notes, root / "使用说明.txt")
        shutil.copy2(tmpd / "LICENSE", root / "LICENSE-MiliKara.txt")
        for p in (launcher, conf, root / "使用说明.txt", root / "LICENSE-MiliKara.txt"):
            backup.note_added(p)
        if plat == "macos":
            launcher.chmod(0o755)
        backup.save()
    # bytecode, as the packages were built (the launcher does not write it)
    subprocess.run([str(folder.py), "-m", "compileall", "-q", str(site / "kara_align")],
                   env=folder.env(), capture_output=True)


def install_fonts(folder: Folder, fonts_zip: Path, backup: Backup) -> None:
    dst = folder.root / "fonts"
    with tempfile.TemporaryDirectory(dir=folder.update_dir) as tmp:
        with zipfile.ZipFile(fonts_zip) as z:
            z.extractall(tmp)
        backup.move_aside(dst)
        shutil.copytree(tmp, dst)
        dst.chmod(0o755)  # (the temporary folder it came from is private)
        backup.note_added(dst)
        backup.save()


def check_start(folder: Folder, version: str) -> Optional[str]:
    """Does the program start (its modules and dependencies load)?  None = yes, else what went wrong."""
    code = ("import kara_align, kara_align.cli, kara_align.web.server; print(kara_align.__version__)")
    try:
        r = subprocess.run([str(folder.py), "-c", code], env=folder.env(), capture_output=True, text=True,
                           timeout=300, cwd=str(folder.root))
    except subprocess.TimeoutExpired:
        return "启动检查超时"
    if r.returncode != 0:
        return (r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip() else "无法启动"
    got = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    if vkey(got) != vkey(version):
        return f"启动后的版本是 {got}，应为 {version}"
    return None


def write_state(folder: Folder, rel: Release, state: dict) -> None:
    fonts = rel.m["components"].get("fonts")
    comps = dict(state.get("components") or {})
    comps["app"] = rel.version
    if fonts and fonts_ok(folder, fonts):
        comps["fonts"] = fonts["id"]
    data = {"version": rel.version, "platform": folder.platform, "variant": state.get("variant"), "components": comps}
    (folder.root / "version.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def prune_backups(folder: Folder, keep: Path) -> None:
    base = folder.update_dir / "backup"
    for d in base.iterdir() if base.exists() else []:
        if d.is_dir() and d != keep:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------------------------------
# self update


def self_update(rel: Release, me: Path, args: list[str]) -> Optional[int]:
    """A newer updater in the release: fetch it, run it instead (returns its exit code), None = go on."""
    u = rel.m.get("updater") or {}
    if not rel.online or int(u.get("version", 0)) <= UPDATER_VERSION or not me.exists():
        return None
    info = u.get("windows" if WIN else "macos")
    if not info:
        return None
    name = info["file"]
    say("正在更新更新程序本身……")
    tmp = me.with_name(me.name + ".download")
    download(f"{base_url()}/download/{rel.m['tag']}/{name}", tmp, info["size"], info["sha256"], "更新程序")
    if name.endswith(".zip"):
        with zipfile.ZipFile(tmp) as z:
            member = next(n for n in z.namelist() if n.endswith((".command", ".bat")))
            data = z.read(member)
        tmp.unlink()
    else:
        data = tmp.read_bytes()
        tmp.unlink()
    if WIN:
        new = me.with_name(me.name + ".new")  # the launcher line moves it over this file when we are done
        new.write_bytes(data)
        return subprocess.run([sys.executable, "-x", str(new), *args, "--no-self-update"]).returncode
    me.write_bytes(data)  # the shell has handed over to Python: the file can be replaced
    me.chmod(0o755)
    os.execv(sys.executable, [sys.executable, str(me), *args, "--no-self-update"])
    return 0


# ---------------------------------------------------------------------------------------------------
# the steps


def wait_until_closed(folder: Folder, auto: Optional[bool]) -> None:
    while folder.running():
        say("\nMiliKara 还在运行。请先关闭它（关掉那个黑色窗口 / 终端窗口），再继续。")
        if auto is not None or not ask("关闭后", None):
            raise UpdateError("MiliKara 还在运行，没有更新")


def do_update(folder: Folder, rel: Release, state: dict, auto: Optional[bool], force: bool) -> int:
    fonts = rel.m["components"].get("fonts")
    newer = vkey(rel.version) > vkey(state["version"])
    need_app = newer or force
    need_fonts = not fonts_ok(folder, fonts)
    if not need_app and not need_fonts:
        say(f"已经是最新版本（{rel.version}）。")
        return 0
    if not newer and not force:
        say(f"程序已经是最新版本（{rel.version}），只需要补上缺少的部分。")
    notes = release_notes(rel.m.get("tag", "")) if rel.online and newer else ""
    if notes:
        say("\n更新内容：\n" + "\n".join("  " + ln for ln in notes.splitlines()))
    todo = []
    if need_app:
        c = rel.m["components"]["app"]
        todo.append(f"程序 {mb(c['size'])}")
    if need_fonts and fonts:
        if rel.online or "fonts" in rel.files:
            todo.append(f"字体 {mb(fonts['size'])}（{'旧版没有' if not (folder.root / 'fonts').exists() else '需要更新'}）")
        else:
            say("  （字体包没有放在文件夹里：这次先不更新字体，联网或放入 " + fonts["file"] + " 后再运行即可补上）")
            need_fonts = False
    if not todo:
        return 0
    say(("\n需要下载：" if rel.online else "\n将使用文件夹里的更新包：") + "，".join(todo))
    say("依赖（torch 等）、ffmpeg、模型和你的项目都保持不变。")
    if not ask("\n开始更新", auto):
        say("已取消。")
        return 1
    folder.update_dir.mkdir(exist_ok=True)
    app_zip = rel.fetch("app", folder) if need_app else None
    fonts_zip = rel.fetch("fonts", folder) if need_fonts else None
    if app_zip is not None:
        with tempfile.TemporaryDirectory(dir=folder.update_dir) as tmp:
            with zipfile.ZipFile(app_zip) as z:
                wheel_name = next(n for n in z.namelist() if n.endswith(".whl"))
                z.extract(wheel_name, tmp)
            missing = unmet_requirements(Path(tmp) / wheel_name)
        if missing:
            say("\n这个版本需要新的依赖，只更新程序不够：")
            for m in missing[:12]:
                say(f"  - {m}")
            full = rel.m.get("full") or f"{RELEASES}/latest"
            raise UpdateError(f"请下载完整离线包：{full}\n（解压到新文件夹即可；你的项目和设置保存在用户目录，不会丢失。）")
    wait_until_closed(folder, auto)
    say("\n正在更新……")
    backup = Backup(folder, state["version"])
    try:
        if app_zip is not None:
            install_app(folder, rel, app_zip, backup, state)
        if fonts_zip is not None:
            install_fonts(folder, fonts_zip, backup)
        problem = check_start(folder, rel.version if app_zip is not None else state["version"])
        if problem:
            raise UpdateError(f"新版本无法启动：{problem}")
    except BaseException as e:
        say("出错了，正在恢复原来的版本……")
        backup.save()
        restore(folder, {"dir": backup.dir, "moved": backup.moved, "added": backup.added})
        if isinstance(e, UpdateError):
            raise UpdateError(f"{e}\n已恢复到更新前的版本，可以照常使用。") from e
        raise
    vj = folder.root / "version.json"
    if vj.exists() and "version.json" not in backup.moved:
        backup.move_aside(vj)  # (a fonts-only update: the program's files stayed)
    backup.note_added(vj)
    write_state(folder, rel, state)
    backup.save()
    prune_backups(folder, backup.dir)
    shutil.rmtree(folder.update_dir / "downloads", ignore_errors=True)
    say(f"\n更新完成：MiliKara {rel.version if app_zip is not None else state['version']}。")
    if state["label"].startswith("KiraKara"):
        say(f"以后请双击 {folder.launcher().name} 启动（旧的启动文件已移到 update/backup；文件夹名可以自己改）。")
    else:
        say(f"双击 {folder.launcher().name} 启动。")
    say("新版本有问题时，再次运行更新程序，选择“退回上一版”。")
    return 0


def do_rollback(folder: Folder, auto: Optional[bool]) -> int:
    info = Backup.latest(folder)
    if not info:
        say("没有可以退回的版本（还没有用更新程序更新过）。")
        return 1
    if not ask(f"退回到 {info['version']}", auto):
        return 1
    wait_until_closed(folder, auto)
    restore(folder, info)
    say(f"已退回到 {info['version']}。")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="更新", description="更新 MiliKara 离线版")
    ap.add_argument("--root", help="MiliKara 文件夹（默认：更新程序所在的文件夹）")
    ap.add_argument("--check", action="store_true", help="只检查有没有新版本")
    ap.add_argument("--rollback", action="store_true", help="退回上一版")
    ap.add_argument("--force", action="store_true", help="版本相同也重新安装程序")
    ap.add_argument("--yes", action="store_true", help="不询问，直接进行")
    ap.add_argument("--no-self-update", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--version", action="version", version=f"MiliKara updater {UPDATER_VERSION}")
    args = ap.parse_args(argv)
    me = Path(__file__).resolve()
    auto = True if args.yes else (None if sys.stdin.isatty() else True)
    folder = Folder(Path(args.root).resolve() if args.root else me.parent)
    state = folder.state()
    variant = {"cpu": " · CPU 版", "cuda": " · NVIDIA 显卡版", "rocm": " · AMD 显卡版"}.get(state.get("variant") or "", "")
    say(f"MiliKara 更新程序\n当前：{state['label']}（{'Windows' if folder.platform == 'windows' else 'macOS'}{variant}）")
    if args.rollback:
        return do_rollback(folder, auto)
    backup = Backup.latest(folder)
    if backup and not args.check and auto is None:
        say(f"\n1. 检查更新\n2. 退回上一版（{backup['version']}）")
        choice = input("请选择（按 Enter 选 1）：").strip()
        if choice == "2":
            return do_rollback(folder, auto)
    rel = local_packages(folder)
    if rel is not None and vkey(rel.version) >= vkey(state["version"]):
        say(f"\n发现文件夹里的程序更新包：{rel.files['app'].name}")
        if not ask(f"用它更新到 {rel.version}（不联网）", auto):
            rel = None
    else:
        rel = None
    if rel is None:
        say("\n正在检查新版本……")
        rel = latest_online()
        if not args.no_self_update:
            code = self_update(rel, me, [a for a in (argv if argv is not None else sys.argv[1:])])
            if code is not None:
                return code
    say(f"最新：MiliKara {rel.version}")
    if args.check:
        say("有新版本，运行更新程序即可更新。" if vkey(rel.version) > vkey(state["version"]) else "已经是最新版本。")
        return 0
    return do_update(folder, rel, state, auto, args.force)


def run() -> None:
    code = 1
    try:
        code = main()
    except UpdateError as e:
        say(f"\n{e}")
    except KeyboardInterrupt:
        say("\n已取消。")
    except Exception as e:  # show what happened instead of closing the window
        import traceback

        traceback.print_exc()
        say(f"\n更新程序出错：{e}")
    if sys.stdin.isatty() and "--yes" not in sys.argv:
        with contextlib.suppress(EOFError, KeyboardInterrupt):
            input("\n按 Enter 关闭窗口")
    sys.exit(code)


if __name__ == "__main__":
    run()
