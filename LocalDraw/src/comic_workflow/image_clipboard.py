"""Copy original generated PNGs to the Windows clipboard without saving a work."""
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from . import engine as e
from .paths import ROOT
from .repository import LOCK


def copy_images(repo, pid, image_ids):
    e.need(os.name == "nt", "图片复制目前支持 Windows；其他系统请使用下载原图")
    e.need(isinstance(image_ids, list) and 0 < len(image_ids) <= 256
           and all(isinstance(i, str) for i in image_ids)
           and len(set(image_ids)) == len(image_ids), "请选择有效的图片")
    cache = None
    try:
        with LOCK:
            project = repo.get(pid)
            e.need(project.get("quick_sample") or project.get("prompt_board"), "请从提示词出图的图片预览中复制")
            image_root = (repo.folder(pid) / "images").resolve()
            index = e.read(image_root / "index.json")
            available = {image["id"]: image for batch in index.get("batches", [])
                         for image in batch.get("images", [])}
            e.need(all(i in available for i in image_ids), "图片已不存在，请刷新后重试")
            sources = [(image_root / available[i]["file"]).resolve() for i in image_ids]
            e.need(all(p.is_relative_to(image_root) and p.is_file() and p.suffix.lower() == ".png"
                       for p in sources), "图片文件无效")
            e.need(sum(p.stat().st_size for p in sources) <= 512_000_000, "图片总大小超过 512 MB，请分次复制")
            cache = Path(tempfile.mkdtemp(prefix="local-draw-clipboard-"))
            files = []
            for n, source in enumerate(sources, 1):
                target = cache / f"prompt-image-{n:02}.png"
                shutil.copyfile(source, target)
                files.append(str(target))
            e.save(cache / "request.json", {"files": files})
        start_clipboard(cache)
        # The clipboard helper owns these files until the clipboard changes.
        return {"copied": len(image_ids)}
    except Exception:
        if cache:
            cleanup_cache(cache)
        raise


def cleanup_cache(cache):
    cache = Path(cache).resolve()
    e.need(cache.parent == Path(tempfile.gettempdir()).resolve()
           and cache.name.startswith("local-draw-clipboard-"), "无效剪贴板缓存目录")
    shutil.rmtree(cache, ignore_errors=True)


def start_clipboard(cache):
    script = (ROOT / "scripts/copy_prompt_images.ps1").read_text(encoding="utf-8")
    process = subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-STA", "-Command", script],
        env={**os.environ, "STUDIO_IMAGE_CLIPBOARD_CACHE": str(cache)},
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if (cache / "ready").exists():
            return
        if process.poll() is not None:
            raise ValueError("无法复制图片，剪贴板可能被其他程序占用，请重试")
        time.sleep(.05)
    process.kill()
    process.wait(timeout=5)
    raise ValueError("复制图片超时，请重试")
