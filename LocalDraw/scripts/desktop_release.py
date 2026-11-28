"""Stage official program releases and commit them while the studio is offline."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath

from comic_workflow import engine
from comic_workflow.instance import InstanceLock
from comic_workflow.paths import ROOT
from comic_workflow.updates import VERSION, REPO

STATE = ROOT / "data/desktop-updates"
TREES = {"src", "scripts", "schemas", "workflows", "comfy_nodes", "runtime", "config"}
FILES = {"LocalDraw.exe", "LocalDraw.exe.config", "launch.py", "start_studio.py",
         "pyproject.toml", "README.md", "requirements.txt"}
LOCAL_CONFIG = {"config/installed-runtime.json", "config/settings.json", "config/comfyui.json"}
HIDDEN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def log(message):
    folder = ROOT / "data/logs"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "desktop-update.log").open("a", encoding="utf-8") as stream:
        print(time.strftime("%Y-%m-%d %H:%M:%S"), message, file=stream)
    engine.save(STATE / "status.json", {"message":message, "time":time.strftime("%Y-%m-%d %H:%M")})


def version(tag):
    if not isinstance(tag, str) or not re.fullmatch(r"v?[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise ValueError("正式版标签无效")
    return tuple(map(int, tag.lstrip("v").split(".")))


def hashfile(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def destination(name):
    parts = name.split("/")
    path = PureWindowsPath(name)
    if ("\\" in name or path.drive or any(p in {"", ".", ".."} or p.rstrip(" .") != p
            or re.search(r'[<>:"|?*\x00-\x1f]', p) or PureWindowsPath(p).is_reserved() for p in parts)
            or not (name in FILES or len(parts) > 1 and parts[0] in TREES)):
        raise ValueError("更新包包含不支持的路径：" + name)
    target = ROOT.joinpath(*parts)
    if target.resolve() != target.absolute():
        raise ValueError("更新目标包含文件链接：" + name)
    return target


def gh(args, *, timeout, stdout=subprocess.PIPE):
    exe = shutil.which("gh")
    if not exe and os.name == "nt":
        exe = next((str(p) for p in (
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/GitHub CLI/gh.exe",
            Path(os.environ.get("ProgramFiles", "")) / "GitHub CLI/gh.exe"
        ) if p.is_absolute() and p.is_file()), None)
    if not exe:
        raise RuntimeError("自动下载需要 GitHub CLI（gh）及私有仓库读取权限")
    result = subprocess.run([exe, *args], stdin=subprocess.DEVNULL, stdout=stdout, stderr=subprocess.PIPE,
                            creationflags=HIDDEN, timeout=timeout, env={**os.environ, "GH_PROMPT_DISABLED":"1"})
    if result.returncode:
        raise RuntimeError("无法访问 GitHub 正式版，请检查网络和 gh 登录状态")
    return result.stdout


def fetch_release():
    info = json.loads(gh(["api", "--hostname", "github.com", f"repos/{REPO}/releases/latest"], timeout=6))
    tag = info["tag_name"]
    if info["draft"] or info["prerelease"]:
        return
    if version(tag) <= version(VERSION):
        log(f"已检查 GitHub 正式版，当前版本 v{VERSION}")
        return
    number = tag.lstrip("v")
    name = f"LocalDraw-{number}-Update.zip"
    asset = next((a for a in info["assets"] if a["name"] == name), None)
    if not asset or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", asset.get("digest") or ""):
        raise ValueError("正式版缺少更新包或 GitHub SHA-256 校验值")
    if not 0 < asset["size"] <= 1024**3:
        raise ValueError("更新包大小无效")
    STATE.mkdir(parents=True, exist_ok=True)
    archive = STATE / name
    expected = asset["digest"][7:].lower()
    if not archive.is_file() or hashfile(archive) != expected:
        with tempfile.NamedTemporaryFile(dir=STATE, delete=False) as stream:
            part = Path(stream.name)
        try:
            with part.open("wb") as stream:
                gh(["api", "--hostname", "github.com", "-H", "Accept: application/octet-stream", f"repos/{REPO}/releases/assets/{int(asset['id'])}"],
                   timeout=90, stdout=stream)
            if part.stat().st_size != asset["size"] or hashfile(part) != expected:
                raise ValueError("正式版更新包校验失败")
            os.replace(part, archive)
        finally:
            part.unlink(missing_ok=True)
    engine.save(STATE / "pending.json", {"version":number, "sha256":expected})
    log(f"已下载并校验 v{number}，等待工作室空闲后安装")


@contextmanager
def exclusive(path, timeout=12):
    lock = InstanceLock(path)
    end = time.monotonic() + timeout
    while True:
        try:
            lock.__enter__()
            break
        except RuntimeError:
            if time.monotonic() >= end:
                raise TimeoutError("正在等待工作室释放文件")
            time.sleep(.15)
    try:
        yield
    finally:
        lock.__exit__(None, None, None)


def base_archive():
    name = f"LocalDraw-{VERSION}-Update.zip"
    cached = STATE / name
    return cached if cached.is_file() else ROOT.parent / "releases" / ("v" + VERSION) / name


def baseline():
    saved = STATE / "baseline.json"
    if saved.exists():
        record = engine.read(saved)
        if record["version"] == VERSION:
            return record["files"]
    old = base_archive()
    if old.is_file():
        with zipfile.ZipFile(old) as archive:
            return {i.filename:hashlib.sha256(archive.read(i)).hexdigest() for i in archive.infolist() if not i.is_dir()}
    return {}


def merge_local(target, name, original_hash, incoming, work):
    git = shutil.which("git")
    base = base_archive()
    if git and base.is_file() and target.suffix.lower() in {".py", ".js", ".css", ".html", ".md", ".ps1"}:
        with zipfile.ZipFile(base) as archive:
            original = archive.read(name)
        if hashlib.sha256(original).hexdigest() == original_hash:
            before, after = work / "merge-base", work / "merge-release"
            before.write_bytes(original)
            after.write_bytes(incoming)
            result = subprocess.run([git, "merge-file", "-p", "--", str(target), str(before), str(after)],
                                    capture_output=True, creationflags=HIDDEN, timeout=5)
            if result.returncode == 0:
                return result.stdout
    raise ValueError("本机与正式版修改发生冲突，已保留当前版本：" + name)


def rollback():
    journal = STATE / "installing.json"
    if not journal.exists():
        return
    record = engine.read(journal)
    for name, existed in reversed(record["files"]):
        target = destination(name)
        if existed:
            shutil.copy2(STATE / "backup" / name, target)
        else:
            target.unlink(missing_ok=True)
    journal.unlink()


def apply_pending(desktop_open):
    pending = STATE / "pending.json"
    rollback()
    if not pending.exists():
        return
    record = engine.read(pending)
    number = record["version"]
    if version(number) <= version(VERSION):
        pending.unlink()
        return
    archive = STATE / f"LocalDraw-{number}-Update.zip"
    if hashfile(archive) != record["sha256"]:
        raise ValueError("待安装更新包校验失败")
    old = baseline()
    manifest, changes, seen = {}, [], set()
    with zipfile.ZipFile(archive) as z, tempfile.TemporaryDirectory(dir=STATE) as tmp:
        members = [i for i in z.infolist() if not i.is_dir()]
        if sum(i.file_size for i in members) > 2*1024**3:
            raise ValueError("更新包解压大小无效")
        for info in members:
            name = info.filename
            target = destination(name)
            if name.casefold() in seen or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("更新包存在重复路径或链接")
            seen.add(name.casefold())
            content = z.read(info)
            digest = manifest[name] = hashlib.sha256(content).hexdigest()
            if name == "src/comic_workflow/updates.py" and not re.search(
                    rb'(?m)^VERSION\s*=\s*["\x27]' + re.escape(number.encode()) + rb'["\x27]\s*$', content):
                raise ValueError("更新包内程序版本与正式标签不一致")
            if name in LOCAL_CONFIG and target.exists():
                continue
            current = hashfile(target) if target.is_file() else None
            if current == digest or (current and old.get(name) == digest):
                continue
            if current and old and name not in old:
                if name.startswith("config/"):
                    continue
                raise ValueError("正式版新增的文件与本机自定义文件同名：" + name)
            if current and name in old and current != old[name]:
                content = merge_local(target, name, old[name], content, Path(tmp))
            if name == "LocalDraw.exe" and desktop_open:
                log("新版启动器已下载，将在窗口退出后安装")
                return
            staged = Path(tmp) / name
            if target.suffix == ".py":
                compile(content, name, "exec", dont_inherit=True)
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(content)
            changes.append((name, target.exists()))
        if not {"src/comic_workflow/updates.py", "src/comic_workflow/app_core.py", "launch.py"}.issubset(manifest):
            raise ValueError("更新包缺少程序入口")
        backup = STATE / "backup"
        for name, existed in changes:
            if existed:
                path = backup / name
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(destination(name), path)
        journal = STATE / "installing.json"
        engine.save(journal, {"files":changes})
        try:
            for name, existed in changes:
                target = destination(name)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(Path(tmp) / name, target)
            engine.save(STATE / "baseline.json", {"version":number,"files":manifest})
        except Exception:
            rollback()
            raise
        journal.unlink()
        pending.unlink()
        shutil.rmtree(backup, ignore_errors=True)
        log("已安装 GitHub 正式版 v" + number)
