"""Single-image delete to local cache with 15-step undo and automatic permanent purge."""

from __future__ import annotations

import copy
import shutil
import uuid
from pathlib import Path

from . import comfyui, engine as e
from .repository import LOCK

MAX_DELETE_UNDO = 15


def cache_root(repo) -> Path:
    return repo.root.parent / "cache" / "deleted-images"


def _history_path(repo) -> Path:
    return cache_root(repo) / "history.json"


def _load_history(repo) -> list[dict]:
    path = _history_path(repo)
    if not path.is_file():
        return []
    try:
        data = e.read(path)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_history(repo, history: list[dict]) -> None:
    root = cache_root(repo)
    root.mkdir(parents=True, exist_ok=True)
    e.save(_history_path(repo), history)


def _batch_second_pass_source_id(batch: dict) -> tuple[bool, str | None]:
    sp_info = batch.get("second_pass") if isinstance(batch.get("second_pass"), dict) else None
    ro_sp = (batch.get("render_options") or {}).get("second_pass")
    is_sp = batch.get("kind") == "second_pass" or sp_info is not None or bool(ro_sp)
    if not is_sp:
        return (False, None)
    source_id = (
        batch.get("source_image_id")
        or (sp_info or {}).get("source_image_id")
        or (ro_sp.get("source_image_id") if isinstance(ro_sp, dict) else None)
    )
    return (True, str(source_id) if source_id else None)


def _safe_rel_path(base_dir: Path, rel: str) -> Path:
    clean = str(rel or "").replace("\\", "/").lstrip("/")
    e.need(bool(clean) and ".." not in Path(clean).parts, "无效图片路径")
    full = (base_dir / clean).resolve()
    e.need(full.is_relative_to(base_dir.resolve()), "无效图片路径")
    return full


def _ensure_no_active_job(app, pid: str) -> None:
    e.need(
        not any(control["job"].get("project") == pid for control in app.jobs.controls.values()),
        "当前正在出图，请等待本轮完成后再操作",
    )
    e.need(
        not any(
            job.get("project") == pid
            and job.get("status") in {"running", "pausing", "paused", "cancelling"}
            for job in app.jobs.listing(limit=None)
        ),
        "当前有进行中的出图任务，请完成后再操作",
    )


def undo_count(repo, pid: str) -> int:
    with LOCK:
        return sum(1 for item in _load_history(repo) if item.get("project") == pid)


def delete_image(app, pid: str, image_id: str, *, delete_variants: bool = True) -> dict:
    """Move an image (and its second-pass variants if base) into cache; purge oldest past 15 steps."""
    e.need(isinstance(pid, str) and bool(pid), "缺少会话标识")
    e.need(isinstance(image_id, str) and bool(image_id), "缺少图片标识")

    with app.jobs.lock, LOCK:
        _ensure_no_active_job(app, pid)
        folder = app.repo.folder(pid).resolve()
        images_dir = folder / "images"
        index = comfyui.drawing_state(app.repo, pid)
        batches = index.get("batches") or []

        target_batch = None
        target_img = None
        for batch in batches:
            for img in batch.get("images") or []:
                if img.get("id") == image_id:
                    target_batch = batch
                    target_img = img
                    break
            if target_img is not None:
                break
        e.need(target_img is not None and target_batch is not None, "找不到要删除的图片")

        target_is_sp, target_sp_src = _batch_second_pass_source_id(target_batch)
        if target_is_sp or not delete_variants:
            scope = "variant" if target_is_sp else "single"
            source_base_id = target_sp_src if target_is_sp else image_id
            target_ids = {image_id}
        else:
            scope = "base_group"
            source_base_id = image_id
            target_ids = {image_id}
            for batch in batches:
                is_sp, sp_src = _batch_second_pass_source_id(batch)
                if is_sp and sp_src == image_id:
                    for img in batch.get("images") or []:
                        if img.get("id"):
                            target_ids.add(img["id"])

        removed_items: list[dict] = []
        new_batches: list[dict] = []
        for bi, batch in enumerate(batches):
            imgs = batch.get("images") or []
            kept = []
            matched = []
            for ii, img in enumerate(imgs):
                if img.get("id") in target_ids:
                    matched.append((ii, img))
                else:
                    kept.append(img)
            if matched:
                batch_meta = {k: copy.deepcopy(v) for k, v in batch.items() if k != "images"}
                for ii, img in matched:
                    removed_items.append(
                        {
                            "batch_index": bi,
                            "batch_id": str(batch.get("id") or ""),
                            "batch_meta": batch_meta,
                            "image_index": ii,
                            "image": copy.deepcopy(img),
                        }
                    )
            if kept:
                new_batches.append({**batch, "images": kept})

        e.need(bool(removed_items), "找不到要删除的图片")

        # Move image files into cache/<entry_id>/files/<rel>
        entry_id = f"del-{e.stamp().replace(':', '-')}-{uuid.uuid4().hex[:8]}"
        root = cache_root(app.repo)
        entry_dir = root / entry_id
        files_root = entry_dir / "files"
        moved_files: list[dict] = []
        try:
            for item in removed_items:
                rel = str(item["image"].get("file") or "").replace("\\", "/").lstrip("/")
                if not rel or not images_dir.exists():
                    continue
                src = _safe_rel_path(images_dir, rel)
                if src.is_file() and not src.is_symlink():
                    dest = _safe_rel_path(files_root, rel)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src), str(dest))
                    moved_files.append({"rel": rel, "cached": f"files/{rel}"})
        except Exception:
            for rec in reversed(moved_files):
                try:
                    c_src = _safe_rel_path(entry_dir, rec["cached"])
                    orig = _safe_rel_path(images_dir, rec["rel"])
                    if c_src.is_file():
                        orig.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(c_src), str(orig))
                except Exception:
                    pass
            shutil.rmtree(entry_dir, ignore_errors=True)
            raise

        # Update second_pass_applied and board selections
        applied = dict(index.get("second_pass_applied") or {})
        removed_applied: dict[str, str] = {}
        if scope == "base_group":
            if image_id in applied:
                removed_applied[image_id] = applied.pop(image_id)
        elif scope == "variant" and target_sp_src and applied.get(target_sp_src) == image_id:
            removed_applied[target_sp_src] = applied.pop(target_sp_src)

        selections = dict(index.get("selections") or {})
        removed_selections: dict[str, object] = {}
        for panel_id, sel in list(selections.items()):
            sel_img_id = sel.get("image", {}).get("id") if isinstance(sel, dict) else sel
            if sel_img_id in target_ids:
                removed_selections[panel_id] = copy.deepcopy(sel)
                selections.pop(panel_id, None)

        new_index = {
            **index,
            "batches": new_batches,
            "selections": selections,
        }
        if applied or "second_pass_applied" in index:
            new_index["second_pass_applied"] = applied
        e.save(images_dir / "index.json", new_index)

        entry = {
            "id": entry_id,
            "project": pid,
            "target_image_id": image_id,
            "scope": scope,
            "source_base_id": source_base_id,
            "deleted_at": e.stamp(),
            "removed_items": removed_items,
            "removed_applied": removed_applied,
            "removed_selections": removed_selections,
            "moved_files": moved_files,
        }
        history = _load_history(app.repo)
        history.append(entry)
        evicted = 0
        while len(history) > MAX_DELETE_UNDO:
            oldest = history.pop(0)
            old_dir = root / str(oldest.get("id") or "")
            if old_dir.exists() and old_dir.resolve().is_relative_to(root.resolve()):
                shutil.rmtree(old_dir, ignore_errors=True)
            evicted += 1
        _save_history(app.repo, history)

        return {
            "deleted_image_id": image_id,
            "deleted_count": len(removed_items),
            "scope": scope,
            "cache_entry_id": entry_id,
            "undo_depth": sum(1 for item in history if item.get("project") == pid),
            "cache_total": len(history),
            "evicted": evicted,
        }


def undo_delete_image(app, pid: str) -> dict:
    """Restore the most recently deleted image entry for this session from cache."""
    e.need(isinstance(pid, str) and bool(pid), "缺少会话标识")

    with app.jobs.lock, LOCK:
        _ensure_no_active_job(app, pid)
        history = _load_history(app.repo)
        match_idx = None
        for idx in range(len(history) - 1, -1, -1):
            if history[idx].get("project") == pid:
                match_idx = idx
                break
        if match_idx is None:
            return {"restored": False, "remaining_undo": 0}

        entry = history[match_idx]
        root = cache_root(app.repo)
        entry_dir = root / str(entry.get("id") or "")
        folder = app.repo.folder(pid).resolve()
        images_dir = folder / "images"

        # Restore files back to <project>/images/
        for rec in entry.get("moved_files") or []:
            c_src = _safe_rel_path(entry_dir, rec["cached"])
            dest = _safe_rel_path(images_dir, rec["rel"])
            if c_src.is_file():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(c_src), str(dest))

        index = comfyui.drawing_state(app.repo, pid)
        batches = list(index.get("batches") or [])

        # Group removed items by batch_id in original order
        grouped: list[tuple[str, int, dict, list[tuple[int, dict]]]] = []
        by_bid: dict[str, int] = {}
        for item in entry.get("removed_items") or []:
            bid = str(item.get("batch_id") or "")
            if bid not in by_bid:
                by_bid[bid] = len(grouped)
                grouped.append(
                    (
                        bid,
                        int(item.get("batch_index", len(batches))),
                        copy.deepcopy(item.get("batch_meta") or {"id": bid}),
                        [],
                    )
                )
            grouped[by_bid[bid]][3].append((int(item.get("image_index", 0)), copy.deepcopy(item["image"])))

        for bid, b_idx, b_meta, items in sorted(grouped, key=lambda row: row[1]):
            target_batch = next((b for b in batches if str(b.get("id") or "") == bid), None)
            if target_batch is None:
                target_batch = {**b_meta, "images": []}
                batches.insert(max(0, min(b_idx, len(batches))), target_batch)
            imgs = list(target_batch.get("images") or [])
            existing_ids = {im.get("id") for im in imgs}
            for i_idx, img in sorted(items, key=lambda pair: pair[0]):
                if img.get("id") not in existing_ids:
                    imgs.insert(max(0, min(i_idx, len(imgs))), img)
                    existing_ids.add(img.get("id"))
            target_batch["images"] = imgs

        applied = dict(index.get("second_pass_applied") or {})
        for base_id, app_id in (entry.get("removed_applied") or {}).items():
            applied[base_id] = app_id

        selections = dict(index.get("selections") or {})
        for panel_id, sel in (entry.get("removed_selections") or {}).items():
            if panel_id not in selections:
                selections[panel_id] = copy.deepcopy(sel)

        new_index = {
            **index,
            "batches": batches,
            "selections": selections,
        }
        if applied or "second_pass_applied" in index:
            new_index["second_pass_applied"] = applied
        e.save(images_dir / "index.json", new_index)

        history.pop(match_idx)
        _save_history(app.repo, history)
        if entry_dir.exists() and entry_dir.resolve().is_relative_to(root.resolve()):
            shutil.rmtree(entry_dir, ignore_errors=True)

        return {
            "restored": True,
            "restored_image_id": entry.get("target_image_id"),
            "scope": entry.get("scope"),
            "source_base_id": entry.get("source_base_id"),
            "restored_count": len(entry.get("removed_items") or []),
            "remaining_undo": sum(1 for item in history if item.get("project") == pid),
        }
