"""Reset a work's generated artwork without changing its story or prompts."""

import uuid

from . import comfyui, engine as e
from .repository import LOCK


def clear_images(app, pid, base_hash):
    with app.jobs.lock, LOCK:
        repo = app.repo
        project = repo.get(pid)
        e.need(e.digest(project) == base_hash, "作品已变化，请刷新后再清除图片")
        e.need(not project.get("quick_sample"), "请在提示词出图页面清除临时图片")
        e.need(not any(c["job"].get("project") == pid for c in app.jobs.controls.values()),
               "请等待当前任务结束，或取消任务后再清除图片")
        e.need(not any(j.get("project") == pid and j["status"] in
                       {"running", "pausing", "paused", "cancelling"}
                       for j in app.jobs.listing(limit=None)),
               "请先结束当前作品的任务再清除图片")
        state = comfyui.drawing_state(repo, pid)
        e.need(not any(b.get("status") in {"running", "queued", "submitted", "uncertain"}
                       for b in state["batches"]),
               "仍有出图任务尚未确认结束，请先核对任务状态")
        folder = repo.folder(pid).resolve()
        backup = folder / "cleared-images" / uuid.uuid4().hex
        paths = [folder / name for name in ("images", "layouts")]
        # Validate every resolved source and destination before moving anything.
        for path in [backup, *paths]:
            e.need(path.resolve().is_relative_to(folder) and not path.is_symlink(), "无效图片目录")
        count = sum(len(b.get("images", [])) for b in state["batches"])
        if not any(path.exists() for path in paths):
            return {"cleared": 0}
        backup.mkdir(parents=True)
        moved = []
        try:
            e.save(backup / "record.json", {"project": pid, "created": e.stamp(), "cleared": count})
            for path in paths:
                if path.exists():
                    path.rename(backup / path.name)
                    moved.append(path)
        except Exception:
            for path in reversed(moved):
                (backup / path.name).rename(path)
            raise
        return {"cleared": count, "backup": str(backup)}
