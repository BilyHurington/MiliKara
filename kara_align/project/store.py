"""Project persistence: project directory, portable package, paths.

Layout of a project directory::

    <dir>/project.json      user data (lyrics, calibration, AI round trips, results …)
    <dir>/assets/<sha>.ext  audio referenced by content hash
    <dir>/exports/          files written by export commands

Caches (emissions, separation) live under :func:`cache_dir` and are never part
of a project; clearing them never loses user data.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Optional

from ..models import FMT_PROJECT, PROJECT_ID_PATTERN, SCHEMA_VERSION, SHA256_PATTERN, Project, utcnow

PROJECT_FILE = "project.json"
MAX_PROJECT_JSON_BYTES = 64 * 1024 * 1024


class ProjectError(Exception):
    pass


def home_dir() -> Path:
    return Path(os.environ.get("KARA_ALIGN_HOME", Path.home() / ".kara_align")).expanduser()


def models_dir(kind: str = "") -> Path:
    """Where model files are kept and loaded from (always passed explicitly to the loaders, never a
    library's default cache): ``$KARA_ALIGN_MODELS``, else the ``models`` folder of the app itself
    (a source checkout or an unpacked app — everything in one place, ready to run offline), else
    ``<home>/models`` when installed as a package (its folder may not be writable)."""
    env = os.environ.get("KARA_ALIGN_MODELS")
    if env:
        root = Path(env).expanduser()
    else:
        app = Path(__file__).resolve().parents[2]
        root = app / "models" if (app / "pyproject.toml").exists() or (app / "models").is_dir() else home_dir() / "models"
    p = root / kind if kind else root
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_dir(kind: str = "") -> Path:
    p = home_dir() / "cache"
    if kind:
        p = p / kind
    p.mkdir(parents=True, exist_ok=True)
    return p


def projects_root() -> Path:
    p = home_dir() / "projects"
    p.mkdir(parents=True, exist_ok=True)
    return p


def is_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(SHA256_PATTERN, value) is not None


def is_project_id(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(PROJECT_ID_PATTERN, value) is not None


def inside(root: Path, path: Path) -> bool:
    """``path`` resolves to somewhere below ``root`` (never ``root`` itself or outside it)."""
    r, p = Path(root).resolve(), Path(path).resolve()
    return r in p.parents


def timestamped(path: Path, label: str = "broken") -> Path:
    """A free name next to ``path`` for keeping a copy aside (never overwrites an earlier one)."""
    path = Path(path)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    stem, suffix = (path.name[: -len(path.suffix)], path.suffix) if path.suffix else (path.name, "")
    for n in range(1000):
        cand = path.with_name(f"{stem}.{label}-{stamp}{'-' + str(n) if n else ''}{suffix}")
        if not cand.exists():
            return cand
    return path.with_name(f"{stem}.{label}-{stamp}-{os.getpid()}-{time.time_ns()}{suffix}")


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())  # the data is on disk before the name points at it (power loss)
        _replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _replace(src: str, dst: Path) -> None:
    """os.replace; on Windows it fails while another thread has the target open (a reader), so a
    few short retries."""
    for attempt in range(20):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if os.name != "nt" or attempt == 19:
                raise
            time.sleep(0.05)


def project_to_json(project: Project) -> str:
    return project.model_dump_json(indent=2)


def parse_project_json(text: str, project_id: Optional[str] = None) -> Project:
    """Parse (untrusted) project JSON, checking format and version."""
    if len(text.encode("utf-8")) > MAX_PROJECT_JSON_BYTES:
        raise ProjectError("项目文件过大")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ProjectError(f"项目 JSON 解析失败: {e}") from e
    if not isinstance(data, dict) or data.get("format") != FMT_PROJECT:
        raise ProjectError("不是 kara-align 项目文件 (format 字段不匹配)")
    version = data.get("version")
    if not isinstance(version, int) or version > SCHEMA_VERSION:
        raise ProjectError(f"不支持的项目版本: {version!r}（当前支持 ≤ {SCHEMA_VERSION}）")
    data = migrate(data)
    if project_id is not None:  # an imported project gets its own id (never the one in the file)
        data["id"] = project_id
    try:
        return Project.model_validate(data)
    except Exception as e:  # pydantic.ValidationError
        raise ProjectError(f"项目内容校验失败: {e}") from e


def migrate(data: dict) -> dict:
    """Upgrade older schema versions in place (only v1 exists so far)."""
    data["version"] = SCHEMA_VERSION
    return data


def save_project(project: Project, project_dir: Path) -> Path:
    project_dir = Path(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "assets").mkdir(exist_ok=True)
    project.updated = utcnow()
    text = project_to_json(project)
    # never write a file that would not load again (e.g. a wrongly typed field)
    try:
        Project.model_validate_json(text)
    except Exception as e:  # pydantic.ValidationError
        raise ProjectError(f"项目数据无效，已拒绝保存以免损坏项目文件：{e}") from e
    path = project_dir / PROJECT_FILE
    if path.exists():
        shutil.copyfile(path, project_dir / (PROJECT_FILE + ".bak"))
    atomic_write_text(path, text)
    return path


def read_project_file(path: Path, project_id: Optional[str] = None) -> Project:
    """Parse a project file; every way it can be unreadable is a :class:`ProjectError`."""
    try:
        if path.stat().st_size > MAX_PROJECT_JSON_BYTES:
            raise ProjectError("项目文件过大")
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as e:
        raise ProjectError(f"项目文件不是 UTF-8 文本：{path.name}") from e
    except OSError as e:
        raise ProjectError(f"无法读取项目文件 {path.name}：{e.strerror or e}") from e
    return parse_project_json(text, project_id)


def load_project(project_dir: Path) -> Project:
    path = Path(project_dir)
    if path.is_dir():
        path = path / PROJECT_FILE
    if not path.exists():
        raise ProjectError(f"找不到项目文件: {path}")
    try:
        project = read_project_file(path)
    except ProjectError as first:
        # a damaged project.json (e.g. cut off by a crash): the copy kept by the previous save is used;
        # the damaged file is set aside (never overwritten) so the next save does not replace the backup
        bak = path.with_name(PROJECT_FILE + ".bak")
        if path.name != PROJECT_FILE or not bak.exists():
            raise
        try:
            project = read_project_file(bak)
        except ProjectError:
            raise first from None
        try:
            path.replace(timestamped(path))
            shutil.copyfile(bak, path)
        except OSError:
            pass
    refresh_asset_paths(project, path.parent)
    return project


def refresh_asset_paths(project: Project, project_dir: Path) -> None:
    """Mark assets whose files are missing (path=None → UI asks for re-upload)."""
    for a in project.audio + ([project.video] if project.video else []):
        if a.path and not (project_dir / a.path).exists():
            a.path = None
        if a.path is None:
            # an asset with the same content might already be in assets/
            for cand in (project_dir / "assets").glob(f"{a.sha256}.*"):
                a.path = str(PurePosixPath("assets") / cand.name)
                break


def asset_abspath(project_dir: Path, rel: Optional[str]) -> Optional[Path]:
    if not rel:
        return None
    p = (Path(project_dir) / rel).resolve()
    root = Path(project_dir).resolve()
    if root not in p.parents and p != root:
        raise ProjectError("资源路径越出项目目录")
    return p


# ---------------------------------------------------------------------------
# portable package (.kara.zip)
# ---------------------------------------------------------------------------


def export_package(project: Project, project_dir: Path, out_path: Path, include_audio: bool = True) -> Path:
    """Write a portable zip: project.json plus (optionally) referenced audio, and the pictures its
    subtitle style uses (``pictures/<id>.png``).

    Never includes model weights, caches, keys or absolute paths.
    """
    from ..karaoke.pictures import picture_path, referenced

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(PROJECT_FILE, project_to_json(project))
        if include_audio:
            for a in project.audio + ([project.video] if project.video else []):
                src = asset_abspath(project_dir, a.path)
                if src and src.exists():
                    zf.write(src, arcname=str(PurePosixPath("assets") / src.name), compress_type=zipfile.ZIP_STORED)
        for pid in sorted(referenced(project.karaoke)):
            src = picture_path(pid)
            if src is not None:
                zf.write(src, arcname=f"pictures/{pid}.png", compress_type=zipfile.ZIP_STORED)
    return out_path


def import_package(zip_path: Path, dest_dir: Path, max_total_bytes: int = 4 * 1024**3,
                   project_id: Optional[str] = None) -> Project:
    """Extract a portable package safely (no path traversal, size-capped)."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as e:
        raise ProjectError("不是有效的项目包（.zip 文件无法读取）") from e
    with zf:
        total = 0
        names = zf.namelist()
        if PROJECT_FILE not in names:
            raise ProjectError("包内缺少 project.json")
        for info in zf.infolist():
            name = PurePosixPath(info.filename)
            if info.is_dir():
                continue
            if name.is_absolute() or ".." in name.parts:
                raise ProjectError(f"包内路径不安全: {info.filename}")
            picture = len(name.parts) == 2 and name.parts[0] == "pictures" and name.suffix == ".png"
            if not (str(name) == PROJECT_FILE or (len(name.parts) == 2 and name.parts[0] == "assets") or picture):
                continue  # ignore anything unexpected
            total += info.file_size
            if total > max_total_bytes:
                raise ProjectError("包内容过大")
            if picture:  # the subtitle style's pictures: kept with the others (never in the project folder)
                from ..karaoke.pictures import MAX_PICTURE_BYTES, adopt_picture

                if info.file_size <= MAX_PICTURE_BYTES:
                    adopt_picture(name.stem, zf.read(info))
                continue
            target = dest_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
    project = read_project_file(dest_dir / PROJECT_FILE, project_id)
    refresh_asset_paths(project, dest_dir)
    save_project(project, dest_dir)
    return project
