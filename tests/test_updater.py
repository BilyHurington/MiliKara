"""The portable-package updater (packaging/updater): made into 更新.bat / 更新.command by build.py, run in a
MiliKara folder.  A folder from v1.0.0 (KiraKara, no fonts, no version.json) updated from a release served
from a local folder (MILIKARA_UPDATE_BASE), from a program package put into the folder, refused when the
new version needs other dependencies, put back when it does not start, rolled back, and updating itself."""

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging" / "updater"))
import build  # noqa: E402
import update  # noqa: E402

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the macOS form of the updater (the .bat is checked in CI)")


def make_wheel(d: Path, version: str, requires=("numpy>=1",)) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    whl = d / f"milikara-{version}-py3-none-any.whl"
    with zipfile.ZipFile(whl, "w") as z:
        z.writestr("kara_align/__init__.py", f'__version__ = "{version}"\n')
        meta = ["Name: milikara", f"Version: {version}", "Provides-Extra: ml"] + [f"Requires-Dist: {r}" for r in requires]
        z.writestr(f"milikara-{version}.dist-info/METADATA", "\n".join(meta) + "\n")
    return whl


def make_release(tmp: Path, version: str, requires=("numpy>=1",), updater_version=None) -> Path:
    """A release as GitHub would serve it: <base>/latest/download/manifest.json, <base>/download/<tag>/…"""
    base = tmp / f"rel-{version}"
    out = base / "download" / f"v{version}"
    fonts = tmp / "fonts-src"
    fonts.mkdir(exist_ok=True)
    (fonts / "NotoSansCJK-Bold.ttc").write_bytes(b"f" * 1000)
    (fonts / "LICENSE-NotoSansCJK.txt").write_text("OFL")
    wheel_dir = make_wheel(tmp / f"wheel-{version}", version, requires).parent
    subprocess.run([sys.executable, str(ROOT / "packaging" / "updater" / "build.py"), "release", str(out),
                    "--tag", f"v{version}", "--wheel", str(wheel_dir), "--fonts", str(fonts), "--allow-mismatch"],
                   check=True, capture_output=True)
    m = json.loads((out / "manifest.json").read_text())
    if updater_version:
        m["updater"]["version"] = updater_version
    m["version"] = version  # (the tag is the version here; pyproject may say otherwise)
    (base / "latest" / "download").mkdir(parents=True)
    (base / "latest" / "download" / "manifest.json").write_text(json.dumps(m))
    return base


def old_folder(tmp: Path) -> Path:
    """A v1.0.0 folder: KiraKara launcher, the program 0.1.0 as kirakara, no fonts, no version.json."""
    root = tmp / "中文 路径" / "KiraKara"
    site = root / "python" / "lib" / "python3.12" / "site-packages"
    (root / "python" / "bin").mkdir(parents=True)
    os.symlink(sys.executable, root / "python" / "bin" / "python3")
    (site / "kara_align").mkdir(parents=True)
    (site / "kara_align" / "__init__.py").write_text('__version__ = "0.1.0"\n')
    (site / "kirakara-0.1.0.dist-info").mkdir()
    (site / "kirakara-0.1.0.dist-info" / "METADATA").write_text("Name: kirakara\nVersion: 0.1.0\n")
    (root / "KiraKara.command").write_text("old launcher")
    (root / "使用说明.txt").write_text("old notes")
    (root / "ffmpeg" / "etc" / "fonts").mkdir(parents=True)
    (root / "ffmpeg" / "etc" / "fonts" / "fonts.conf").write_text("old conf")
    (root / "models").mkdir()
    (root / "models" / "mine.ckpt").write_text("a model the user downloaded")
    return root


def snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): (p.read_bytes() if p.is_file() and not p.is_symlink() else None)
            for p in sorted(root.rglob("*")) if "update" not in p.relative_to(root).parts}


@pytest.fixture
def started(monkeypatch):
    """The start check (the folder's Python is this test's, whose kara_align is the repository's)."""
    calls = []
    monkeypatch.setattr(update, "check_start", lambda folder, version: calls.append(version))
    return calls


def run(root: Path, base: Path | None, *args: str, monkeypatch) -> int:
    monkeypatch.setenv("MILIKARA_UPDATE_BASE", base.as_uri() if base else "file:///nonexistent")
    return update.main(["--root", str(root), "--yes", *args])


def test_old_folder_is_recognised(tmp_path):
    f = update.Folder(old_folder(tmp_path))
    st = f.state()
    assert (st["version"], st["label"], st["platform"]) == ("1.0.0", "KiraKara 1.0.0 预览版", "macos")


def test_update_an_old_folder_then_roll_back(tmp_path, monkeypatch, started):
    root = old_folder(tmp_path)
    before = snapshot(root)
    base = make_release(tmp_path, "1.1.0")
    assert run(root, base, monkeypatch=monkeypatch) == 0
    site = root / "python" / "lib" / "python3.12" / "site-packages"
    assert (site / "kara_align" / "__init__.py").read_text() == '__version__ = "1.1.0"\n'
    assert not (site / "kirakara-0.1.0.dist-info").exists() and (site / "milikara-1.1.0.dist-info").exists()
    assert not (root / "KiraKara.command").exists() and os.access(root / "MiliKara.command", os.X_OK)
    assert "MiliKara" in (root / "使用说明.txt").read_text() and (root / "LICENSE-MiliKara.txt").exists()
    assert (root / "ffmpeg/etc/fonts/fonts.conf").read_text() != "old conf"
    assert (root / "fonts" / "NotoSansCJK-Bold.ttc").stat().st_size == 1000  # the fonts v1.0.0 did not have
    assert (root / "models" / "mine.ckpt").exists()  # never touched
    assert json.loads((root / "version.json").read_text())["version"] == "1.1.0"
    assert started == ["1.1.0"]
    # nothing to do the second time
    assert run(root, base, monkeypatch=monkeypatch) == 0 and started == ["1.1.0"]
    # back to exactly what it was
    assert update.main(["--root", str(root), "--yes", "--rollback"]) == 0
    assert snapshot(root) == before


def test_a_program_package_in_the_folder_needs_no_network(tmp_path, monkeypatch, started):
    root = old_folder(tmp_path)
    rel = make_release(tmp_path, "1.1.0") / "download" / "v1.1.0"
    shutil.copy(next(rel.glob("MiliKara-*-app.zip")), root)  # (named after pyproject's version)
    assert run(root, None, monkeypatch=monkeypatch) == 0
    assert (root / "MiliKara.command").exists()
    assert not (root / "fonts").exists()  # its fonts package was not put there: left for later
    # with the fonts package there too, they are added
    shutil.copy(next(rel.glob("MiliKara-fonts-*.zip")), root)
    assert run(root, None, monkeypatch=monkeypatch) == 0
    assert (root / "fonts" / "NotoSansCJK-Bold.ttc").exists()


def test_other_dependencies_need_the_full_package(tmp_path, monkeypatch, started):
    root = old_folder(tmp_path)
    before = snapshot(root)
    base = make_release(tmp_path, "1.2.0", requires=("numpy>=1", "surely-not-installed>=1", "numpy>=999; extra == 'ml'"))
    with pytest.raises(update.UpdateError, match="完整离线包"):
        run(root, base, monkeypatch=monkeypatch)
    assert snapshot(root) == before and not started


def test_a_version_that_does_not_start_is_put_back(tmp_path, monkeypatch):
    root = old_folder(tmp_path)
    before = snapshot(root)
    monkeypatch.setattr(update, "check_start", lambda folder, version: "ImportError: no module named x")
    with pytest.raises(update.UpdateError, match="已恢复到更新前的版本"):
        run(root, make_release(tmp_path, "1.1.0"), monkeypatch=monkeypatch)
    assert snapshot(root) == before


def test_the_windows_variants(tmp_path):
    """CPU / NVIDIA / AMD folders are told apart by their torch; the program package has each one's notes."""
    root = tmp_path / "MiliKara"
    site = root / "python" / "Lib" / "site-packages"
    site.mkdir(parents=True)
    (root / "python" / "python.exe").write_bytes(b"")
    for torch, variant in (("2.14.1+cpu", "cpu"), ("2.14.1+cu128", "cuda"), ("2.14.0+rocm10.1.0", "rocm"), ("2.14.1", "cpu")):
        for d in site.glob("torch-*.dist-info"):
            d.rmdir()
        (site / f"torch-{torch}.dist-info").mkdir()
        assert update.Folder(root).variant() == variant, torch
    rel = make_release(tmp_path, "1.1.0")
    with zipfile.ZipFile(next((rel / "download" / "v1.1.0").glob("MiliKara-*-app.zip"))) as z:
        notes = {n: z.read(n).decode("utf-8-sig") for n in z.namelist() if n.startswith("notes/")}
    assert sorted(notes) == ["notes/macos.txt", "notes/windows-cpu.txt", "notes/windows-cuda.txt", "notes/windows-rocm.txt"]
    assert "AMD 显卡版" in notes["notes/windows-rocm.txt"] and "RX 7000" in notes["notes/windows-rocm.txt"]


def test_the_updater_files(tmp_path):
    bat = build.make_updater("windows")
    first = bat.split(b"\r\n")[0]
    assert first.isascii() and first.startswith(b"@echo off") and b'-x "%~f0"' in first
    assert b"\n" not in bat.replace(b"\r\n", b"")  # CRLF throughout
    # the macOS file runs through sh with the folder's Python
    root = old_folder(tmp_path)
    cmd = build.write_updater("macos", root / "更新.command")
    r = subprocess.run(["sh", str(cmd), "--version"], capture_output=True, text=True)
    assert r.returncode == 0 and "MiliKara updater" in r.stdout
    # outside a MiliKara folder: says where it belongs
    lone = tmp_path / "alone"
    lone.mkdir()
    build.write_updater("macos", lone / "更新.command")
    r = subprocess.run(["sh", str(lone / "更新.command")], capture_output=True, text=True, input="\n")
    assert r.returncode == 1 and "MiliKara 文件夹" in r.stdout


def test_the_updater_updates_itself(tmp_path, monkeypatch):
    root = old_folder(tmp_path)
    me = build.write_updater("macos", root / "更新.command")
    base = make_release(tmp_path, "1.1.0", updater_version=update.UPDATER_VERSION + 1)
    # the release's updater file says it is newer
    rel = base / "download" / "v1.1.0"
    newer = build.make_updater("macos").replace(b"UPDATER_VERSION = 1", f"UPDATER_VERSION = {update.UPDATER_VERSION + 1}".encode())
    with zipfile.ZipFile(rel / "MiliKara-updater-macos.zip", "w") as z:
        z.writestr("更新.command", newer)
    m = json.loads((base / "latest/download/manifest.json").read_text())
    m["updater"]["macos"] = build.file_info(rel / "MiliKara-updater-macos.zip")
    (base / "latest/download/manifest.json").write_text(json.dumps(m))
    env = dict(os.environ, MILIKARA_UPDATE_BASE=base.as_uri())
    r = subprocess.run(["sh", str(me), "--check", "--yes"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "正在更新更新程序本身" in r.stdout and "有新版本" in r.stdout
    assert f"UPDATER_VERSION = {update.UPDATER_VERSION + 1}" in me.read_text() and os.access(me, os.X_OK)


def test_the_app_knows_its_portable_folder_and_asks_only_when_allowed(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from kara_align import settings as app_settings
    from kara_align import updates
    from kara_align.web.server import create_app

    root = tmp_path / "MiliKara"
    (root / "python" / "bin").mkdir(parents=True)
    (root / "version.json").write_text("{}")
    assert updates.portable_root(str(root / "python" / "bin" / "python3")) == root
    assert updates.portable_root(str(tmp_path / "venv" / "bin" / "python3")) is None
    monkeypatch.setenv("KARA_ALIGN_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(updates, "_fetch", lambda: "9.0.0")
    updates._cache.clear()
    c = TestClient(create_app(tmp_path / "projects"))
    r = c.get("/api/update").json()
    assert r["enabled"] and r["newer"] and r["latest"] == "9.0.0" and not r["portable"]
    app_settings.update({"check_updates": False})
    monkeypatch.setattr(updates, "_fetch", lambda: (_ for _ in ()).throw(AssertionError("asked GitHub")))
    assert c.get("/api/update").json() == {"enabled": False, "current": updates.__version__}
    assert c.get("/api/settings").json()["check_updates"] is False
