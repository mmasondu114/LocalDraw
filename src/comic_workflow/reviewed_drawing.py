"""Six viewpoints, semantic checks and bounded replacement of rejected images."""

from . import comfyui, creative, engine as e
from .repository import LOCK
from .character_binding import identity_hash, panel_characters


def resumable_batches(repo, pid, project, panel, checkpoint, loras):
    """Use completed image files only for the same story, identity and model."""
    identity = identity_hash(project, panel)
    from . import art_presets, drawing_settings

    prefs = drawing_settings.load(repo, pid)
    from .lora_binding import route
    loras = route(loras, project, panel, comfyui.lora_labels(), prefs)
    style_hash = art_presets.signature(prefs)
    candidates = [
        b
        for b in comfyui.drawing_state(repo, pid)["batches"]
        if b["panel_id"] == panel["id"]
        and b["panel_hash"] == e.digest(panel)
        and b.get("checkpoint") == checkpoint
        and b.get("loras", []) == loras
        and b.get("review_required") is True
        and b.get("art_preset_hash") == style_hash
        and not b.get("render_options", {}).get("diagnostic")
    ]
    # Older batches recorded prompt hashes; resolve identity from immutable
    # project history rather than assuming an old image depicts the new card.
    legacy_hashes = set()
    if any(not b.get("identity_hash") for b in candidates):
        snapshots = [project] + [
            e.read(f) for f in (repo.folder(pid) / "history").glob("*.json")
        ]
        for snapshot in snapshots:
            prompt = snapshot.get("drawing_prompts", {}).get(panel["id"])
            if prompt and prompt.get("identity_hash") == identity:
                legacy_hashes.add(e.digest(prompt))
    return [
        b
        for b in candidates
        if b.get("identity_hash") == identity
        or (not b.get("identity_hash") and b.get("prompt_hash") in legacy_hashes)
    ]


def record_review(repo, pid, batch_id, image_id, review):
    with LOCK:
        index = comfyui.drawing_state(repo, pid)
        batch = next(b for b in index["batches"] if b["id"] == batch_id)
        image = next(i for i in batch["images"] if i["id"] == image_id)
        image["review"] = review
        e.save(repo.folder(pid) / "images" / batch_id / "batch.json", batch)
        e.save(repo.folder(pid) / "images/index.json", index)


def reviewed_batch(app, pid, a, update, jid):
    repo = app.repo
    target = a["target"]
    p = repo.get(pid)
    panel = next(
        x for x in p["pages"][target.split("-")[0]]["panels"] if x["id"] == target
    )
    mode = a.get("mode", "angles")
    count = a.get("count", 6)
    e.need(count == 6 and mode == "angles", "此流程固定六种视角候选")
    # Build an adaptation automatically when none exists, retaining it as a logged candidate.
    # It changes drawing metadata only, never confirmed story panels or dialogue.
    dp = p.get("drawing_prompts", {}).get(target)
    from . import art_presets, drawing_settings

    preset = art_presets.get(drawing_settings.load(repo, pid))
    if (
        not dp
        or dp["panel_hash"] != e.digest(panel)
        or dp.get("identity_hash") != identity_hash(p, panel)
        or len(dp["payload"]["composition_variants"]) != 6
        or a.get("refresh_adaptation", False)
        or (preset and not dp["payload"].get("framing"))
    ):
        from .model import generate

        update({"message": "正在加载语言模型并准备六种取景描述", "progress": None})
        try:
            app.prepare_language(update=update)
            payload, metrics = generate(
                p,
                "drawing_prompt",
                target,
                "必须生成六个不同角度的完整英文提示词，严格保持本格文本事件、人物和道具。",
                repo.folder(pid) / "runs" / jid / "adaptation",
                update,
            )
            e.need(
                len(payload["composition_variants"]) == 6
                and len(set(payload["composition_variants"])) == 6,
                "模型未提供六种不同视角，请重试",
            )
            c = repo.propose(
                pid,
                "drawing_prompt",
                target,
                payload,
                e.digest(p),
                "用户请求六视角出图的自动绘图适配",
                metrics,
            )
            p = repo.accept(pid, c["id"])
        except Exception:
            app.model.stop()
            app.comfy.stop()
            raise
    pending = list(range(6))
    repairs = {}
    batches = []
    accepted = []
    uncertain = []
    resume = a.get("resume", False)
    attempts = {i: 0 for i in range(6)}
    settled = set()
    waiting = []
    work_performed = False
    if resume:
        history = resumable_batches(
            repo,
            pid,
            p,
            panel,
            a.get("checkpoint") or comfyui.config()["checkpoint"],
            a.get("loras") or [],
        )
        for previous in history:
            batches.append(previous["id"])
            unreviewed = []
            for image in previous["images"]:
                view = image.get("view_index", 0) - 1
                if view not in attempts:
                    continue
                e.need(
                    (repo.folder(pid) / "images" / image["file"]).is_file(),
                    "已有候选图片缺失，停止续跑以免重复生成",
                )
                attempts[view] += 1
                review = image.get("review", {})
                verdict = review.get("verdict")
                if verdict in ["pass", "uncertain"]:
                    settled.add(view)
                    (accepted if verdict == "pass" else uncertain).append(image["id"])
                elif verdict == "reject":
                    repairs[view] = review.get("repair", "")
                else:
                    unreviewed.append(image)
            if unreviewed:
                waiting.append({**previous, "images": unreviewed})
        pending = [v for v in range(6) if v not in settled and attempts[v] < 3]
    try:
        # One initial generation + at most two replacement rounds. Never unbounded GPU work.
        for attempt in range(3 + len(waiting)):
            if waiting:
                work_performed = True
                batch = waiting.pop(0)
                update(
                    {
                        "message": f"继续审核已保存的 {len(batch['images'])} 张图片，不重新绘制",
                        "progress": None,
                    }
                )
            else:
                if not pending:
                    break
                work_performed = True
                app.model.stop()
                e.need(
                    not app.model.status()["ready"],
                    "外部语言模型仍在占用显存，请先关闭",
                )
                update({"message": "正在启动绘图模型", "progress": None})
                app.comfy.start()
                round_number = (
                    max((attempts[v] for v in pending), default=attempt) + 1
                    if resume
                    else attempt + 1
                )
                update(f"第 {round_number} 轮：生成 {len(pending)} 张候选图")
                batch = comfyui.run_batch(
                    repo,
                    pid,
                    target,
                    update,
                    a.get("checkpoint"),
                    len(pending),
                    "angles",
                    a.get("loras"),
                    pending,
                    repairs,
                    review_required=True,
                )
                batches.append(batch["id"])
                for image in batch["images"]:
                    attempts[image["view_index"] - 1] += 1
            update({"message": "图片已生成，正在切换到视觉审核模型", "progress": None})
            app.prepare_language(vision=True, update=update)
            next_pending = []
            for i, image in enumerate(batch["images"]):
                update(
                    {
                        "message": f"对照文本审核视角 {image['view_index']}（{i + 1}/{len(batch['images'])}）",
                        "progress": {
                            "completed": i,
                            "total": len(batch["images"]),
                            "unit": "张（本轮审核）",
                        },
                    }
                )
                review = creative.review_image(
                    panel,
                    image["positive"],
                    repo.folder(pid) / "images" / image["file"],
                    "已记录取景策略（审核以此为准）："
                    + p.get("automation_policy", "")
                    + "\n本格原文："
                    + (
                        p["script_pages"][target.split("-")[0]]["panels"][
                            panel["number"] - 1
                        ]
                        if p.get("script_pages", {}).get(target.split("-")[0])
                        else p.get("script", p["brief"]["direction"])
                    ),
                    panel_characters(p, panel),
                    repo.folder(pid) / "runs" / jid / "reviews" / image["id"],
                    lambda message: update(
                        {
                            **(message if isinstance(message, dict) else {}),
                            "message": f"审核视角 {image['view_index']}：{message.get('message', '') if isinstance(message, dict) else message}",
                            "progress": {
                                "completed": i,
                                "total": len(batch["images"]),
                                "unit": "张（本轮审核）",
                            },
                        }
                    ),
                )
                record_review(repo, pid, batch["id"], image["id"], review)
                update(
                    {
                        "message": f"视角 {image['view_index']} 审核完成",
                        "progress": {
                            "completed": i + 1,
                            "total": len(batch["images"]),
                            "unit": "张（本轮审核）",
                        },
                    }
                )
                if review["verdict"] == "reject":
                    next_pending.append(image["view_index"] - 1)
                    repairs[image["view_index"] - 1] = review["repair"]
                elif review["verdict"] == "uncertain":
                    uncertain.append(image["id"])
                    settled.add(image["view_index"] - 1)
                else:
                    accepted.append(image["id"])
                    settled.add(image["view_index"] - 1)
            pending = (
                [v for v in range(6) if v not in settled and attempts[v] < 3]
                if resume
                else next_pending
            )
            if not pending and not waiting:
                break
        report = {
            "batches": batches,
            "passed": accepted,
            "uncertain": uncertain,
            "unresolved_views": [x + 1 for x in range(6) if x not in settled]
            if resume
            else [x + 1 for x in pending],
            "rounds": len(batches),
            "resumed": resume,
            "reused_complete": resume and not work_performed,
            "attempts_by_view": {str(v + 1): n for v, n in attempts.items()},
            "note": "明确不符已淘汰并最多补画两轮；不确定图待人工审核；未自动选图",
        }
        e.save(repo.folder(pid) / "runs" / jid / "drawing-review.json", report)
        return report
    finally:
        app.model.stop()
        # Release allocations even after a vision or render failure.
        app.comfy.stop()
