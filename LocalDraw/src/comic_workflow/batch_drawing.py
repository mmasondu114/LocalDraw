"""Sequential whole-project drawing, retaining reviewed images and user choices."""

from . import comfyui, engine as e
from .reviewed_drawing import reviewed_batch
from . import drawing_settings
from .telemetry import drawing_durations, record_sample
import time
import statistics


def story_fingerprint(project):
    return e.digest(
        {
            key: project.get(key)
            for key in ["brief", "canon", "plan", "pages", "script", "script_source"]
        }
    )


def available_views(index, panel, checkpoint, loras, style_hash=None):
    return {
        image.get("view_index")
        for batch in index["batches"]
        if batch["panel_id"] == panel["id"]
        and batch["panel_hash"] == e.digest(panel)
        and batch["checkpoint"] == checkpoint
        and batch.get("loras", []) == loras
        and batch.get("art_preset_hash") == style_hash
        and not batch.get("render_options", {}).get("diagnostic")
        for image in batch["images"]
        if image.get("review", {}).get("verdict") in ["pass", "uncertain"]
        and image.get("view_index") in range(1, 7)
    }


def draw_project(app, pid, request, update, jid):
    p = app.repo.get(pid)
    e.need(e.digest(p) == request["base_hash"], "作品已变化，请刷新后再开始")
    planned = [page["id"] for page in (p.get("plan") or {}).get("pages", [])]
    e.need(
        planned and all(page in p["pages"] for page in planned),
        "请先在第二步确认所有页面的分镜",
    )
    panels = [panel for page in planned for panel in p["pages"][page]["panels"]]
    e.need(panels, "尚无已确认画格")
    prefs = drawing_settings.load(app.repo, pid)
    from .art_presets import signature

    style_hash = signature(prefs)
    checkpoint = (
        prefs["checkpoint"]
        if prefs
        else request.get("checkpoint") or comfyui.config()["checkpoint"]
    )
    assignments = {
        panel["id"]: drawing_settings.loras_for_panel(prefs, p, panel)
        if prefs
        else request.get("loras_by_panel", {}).get(panel["id"], [])
        for panel in panels
    }
    fingerprint = story_fingerprint(p)
    result = {
        "completed": [],
        "skipped": [],
        "errors": [],
        "unresolved": [],
        "total": len(panels),
    }
    folder = app.repo.folder(pid) / "runs" / jid
    durations = drawing_durations()
    started = time.monotonic()
    done_seconds = 0
    pending_count = 0
    initial_index = comfyui.drawing_state(app.repo, pid)
    for panel in panels:
        selected = initial_index["selections"].get(panel["id"])
        if not (
            (selected and selected["panel_hash"] == e.digest(panel))
            or len(
                available_views(
                    initial_index,
                    panel,
                    checkpoint,
                    assignments[panel["id"]],
                    style_hash,
                )
            )
            == 6
        ):
            pending_count += 1
    current_detail = None
    for number, panel in enumerate(panels):
        e.need(
            story_fingerprint(app.repo.get(pid)) == fingerprint,
            "绘图期间分镜或角色资料发生变化，已停止；已有图片保留，请刷新",
        )
        target = panel["id"]
        loras = assignments[target]

        def report(message):
            nonlocal current_detail
            detail = message if isinstance(message, dict) else {"message": message}
            if "progress" in detail:
                current_detail = detail["progress"]
            remaining = (
                statistics.median(durations[-5:]) * pending_count
                - (time.monotonic() - started - done_seconds)
                if durations
                else None
            )
            update(
                {
                    "message": f"第 {number + 1}/{len(panels)} 格 · {target} · {detail.get('message') or (detail.get('runtime') or {}).get('phase', '')}",
                    "progress": {
                        "completed": number,
                        "total": len(panels),
                        "unit": "格已处理",
                    },
                    "detail_progress": current_detail,
                    "runtime": detail.get("runtime"),
                    "eta": {
                        "remaining_seconds": remaining,
                        "basis": "已完成同类画格的实测耗时，包含审核和补画，后续拒绝率可能改变估计",
                    },
                }
            )

        report("准备绘制")
        index = comfyui.drawing_state(app.repo, pid)
        selected = index["selections"].get(target)
        if (selected and selected["panel_hash"] == e.digest(panel)) or len(
            available_views(index, panel, checkpoint, loras, style_hash)
        ) == 6:
            result["skipped"].append(target)
        else:
            try:
                panel_started = time.monotonic()
                outcome = reviewed_batch(
                    app,
                    pid,
                    {
                        "target": target,
                        "checkpoint": checkpoint,
                        "loras": loras,
                        "resume": True,
                    },
                    report,
                    jid + "/" + target,
                )
                duration = time.monotonic() - panel_started
                if not outcome.get("reused_complete"):
                    durations.append(duration)
                    record_sample("drawing-panel", {"seconds": duration})
                pending_count -= 1
                done_seconds = time.monotonic() - started
                result["completed"].append(target)
                if outcome["unresolved_views"]:
                    result["unresolved"].append(
                        {"panel": target, "views": outcome["unresolved_views"]}
                    )
            except Exception as exc:
                result["errors"].append({"panel": target, "reason": str(exc)})
                e.save(folder / "all-drawing.json", result)
                # Do not send another prompt after an unknown/failed GPU operation.
                raise ValueError(
                    f"{target} 绘制中断：{exc}。已有图片和选图保留，可重试剩余画格"
                ) from exc
        e.need(
            story_fingerprint(app.repo.get(pid)) == fingerprint,
            "绘图期间分镜发生变化，已停止；旧图不能用于新分镜",
        )
        e.save(folder / "all-drawing.json", result)
        update(
            {
                "message": f"已处理 {number + 1}/{len(panels)} 格",
                "progress": {
                    "completed": number + 1,
                    "total": len(panels),
                    "unit": "格已处理",
                },
                "detail_progress": None,
                "runtime": None,
                "eta": {
                    "remaining_seconds": 0
                    if not pending_count
                    else statistics.median(durations[-5:]) * pending_count
                    if durations
                    else None,
                    "basis": "同类画格实测耗时",
                },
            }
        )
    return result
