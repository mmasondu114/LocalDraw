"""Render the whole book, review together, and retry only unfinished slots."""

from . import engine as e, comfyui, drawing_settings, creative
from .character_binding import identity_hash, panel_characters
from .framing import source_text
from .reviewed_drawing import resumable_batches, record_review
from .task_control import checkpoint as task_checkpoint, active_time

CANDIDATES = 16
MAX_ATTEMPTS = 3


def draw_project(app, pid, request, update, jid):
    from .batch_drawing import story_fingerprint
    from .drawing_tools import prepare
    from .prompt_adapter import detailed

    repo = app.repo
    candidates = request.get("candidate_count", CANDIDATES)
    e.need(type(candidates) is int and 1 <= candidates <= 16, "每格候选数量须为 1–16")
    options = {}
    if request.get("steps") is not None:
        e.need(type(request["steps"]) is int and 1 <= request["steps"] <= 100, "步数须为 1–100")
        options["steps"] = request["steps"]
    if request.get("seed") is not None:
        e.need(type(request["seed"]) is int and 0 <= request["seed"] < 2**53, "种子须为 0–9007199254740991 的整数")
        options["seed"] = request["seed"]
    initial = repo.get(pid)
    e.need(e.digest(initial) == request["base_hash"], "作品已变化，请刷新后再开始")
    plan = (initial.get("plan") or {}).get("pages", [])
    targets = request.get("targets")
    e.need(
        plan and (targets or all(x["id"] in initial["pages"] for x in plan)),
        "请先确认全部分镜",
    )
    panels = [
        p
        for page in plan
        for p in initial["pages"].get(page["id"], {}).get("panels", [])
        if not targets or p["id"] in targets
    ]
    e.need(
        panels and (not targets or set(targets) == {p["id"] for p in panels}),
        "画格不存在",
    )
    fingerprint = story_fingerprint(initial)
    prefs = drawing_settings.load(repo, pid)
    prefs_hash = e.digest(prefs)
    cp = (
        prefs["checkpoint"]
        if prefs
        else request.get("checkpoint") or comfyui.config()["checkpoint"]
    )
    assignments = {
        p["id"]: drawing_settings.loras_for_panel(prefs, initial, p)
        if prefs
        else request.get("loras_by_panel", {}).get(p["id"], request.get("loras", []))
        for p in panels
    }
    folder = repo.folder(pid) / "runs" / jid
    report = {
        "mode": "phased",
        "candidates_per_panel": candidates,
        "total": len(panels),
        "completed": [],
        "skipped": [],
        "unresolved": [],
        "errors": [],
        "phases": [],
    }
    states = {}
    phase_times = {}

    def check():
        task_checkpoint()
        p = repo.get(pid)
        e.need(
            story_fingerprint(p) == fingerprint,
            "剧本或分镜已变化，停止本次任务，已有结果保留",
        )
        e.need(
            e.digest(drawing_settings.load(repo, pid)) == prefs_hash,
            "绘图设置已变化，停止本次任务，已有图片保留",
        )
        return p

    def progress(phase, done, total, unit):
        def emit(event):
            detail = event if isinstance(event, dict) else {"message": event}
            started = phase_times.setdefault(phase, active_time())
            elapsed = active_time() - started
            eta = elapsed / done * (total - done) if done else None
            update(
                {
                    **detail,
                    "message": phase + " · " + str(detail.get("message", "")),
                    "detail_progress": detail.get("progress"),
                    "eta": {"remaining_seconds": eta, "scope": "本阶段估算"},
                    "progress": {
                        "completed": done,
                        "total": total,
                        "unit": unit,
                        "eta_seconds": eta,
                        "eta_label": "本阶段估计剩余",
                    },
                }
            )

        return emit

    def checkpoint(phase=None):
        if phase and report.get("phase") != phase:
            report["phase"] = phase
            report["phases"].append(phase)
        report["slots"] = {
            key: {
                "attempts": s["attempts"],
                "settled": sorted(s["settled"]),
                "blocked": sorted(s["blocked"]),
                "waiting": [i["id"] for _, i in s["waiting"]],
            }
            for key, s in states.items()
        }
        e.save(folder / "all-drawing.json", report)

    def failure(stage, s, exc, slot=None):
        report["errors"].append(
            {
                "stage": stage,
                "panel": s["panel"]["id"],
                "slot": None if slot is None else slot + 1,
                "reason": str(exc),
                "time": e.stamp(),
            }
        )
        checkpoint()

    def review_waiting():
        task_checkpoint()
        waiting = [
            (s, b, i)
            for s in states.values()
            for b, i in s["waiting"]
            if s["review_attempts"].get(i["id"], 0) < MAX_ATTEMPTS
        ]
        if not waiting:
            return
        phase = "集中审核"
        checkpoint(phase)
        phase_times[phase] = active_time()
        app.comfy.stop()
        try:
            app.prepare_language(
                vision=True, update=progress(phase, 0, len(waiting), "张已处理")
            )
        except Exception as exc:
            for s, _, image in waiting:
                s["review_attempts"][image["id"]] = (
                    s["review_attempts"].get(image["id"], 0) + 1
                )
                failure("审核模型加载", s, exc, image["view_index"] - 1)
            return
        done = 0
        # Transient review failures go to the tail; never redraw a saved pending image.
        queue = list(waiting)
        while queue:
            s, batch, image = queue.pop(0)
            p = check()
            s["review_attempts"][image["id"]] = (
                s["review_attempts"].get(image["id"], 0) + 1
            )
            try:
                outcome = creative.review_image(
                    s["panel"],
                    image["positive"],
                    repo.folder(pid) / "images" / image["file"],
                    p.get("automation_policy", "")
                    + "\n本格原文："
                    + source_text(p, s["panel"]),
                    panel_characters(p, s["panel"]),
                    folder / "reviews" / image["id"],
                    progress(phase, done, len(waiting), "张已处理"),
                )
                record_review(repo, pid, batch["id"], image["id"], outcome)
                view = image["view_index"] - 1
                if outcome["verdict"] in ["pass", "uncertain"]:
                    s["settled"].add(view)
                else:
                    s["repairs"][view] = outcome.get("repair", "")
                s["waiting"].remove((batch, image))
            except Exception as exc:
                failure("审核", s, exc, image["view_index"] - 1)
                if s["review_attempts"][image["id"]] < MAX_ATTEMPTS:
                    queue.append((s, batch, image))
                    continue
            done += 1
            progress(phase, done, len(waiting), "张已处理")(
                "审核结果已保存；出错项目保留待重试"
            )
            checkpoint()

    def recover_pending(cancel_running=False):
        from .comfy_recovery import recover_batch

        for batch in comfyui.drawing_state(repo, pid)["batches"]:
            s = states.get(batch["panel_id"])
            if (
                not s
                or batch["id"] not in s["deferred"]
                or batch.get("status") not in ["running", "uncertain"]
            ):
                continue
            slots = batch.get("candidate_indices", list(range(batch.get("count", 6))))
            if not s["blocked"].intersection(slots):
                continue
            try:
                recovered = recover_batch(repo, pid, batch, cancel_running)
                if recovered["status"] in ["running", "uncertain"]:
                    continue
                s["blocked"].difference_update(slots)
                for image in recovered["images"]:
                    if image.get("review", {}).get("verdict") == "pending" and not any(
                        i["id"] == image["id"] for _, i in s["waiting"]
                    ):
                        s["waiting"].append((recovered, image))
                checkpoint()
            except Exception as exc:
                failure("核对超时任务", s, exc)

    def indices_for(s):
        pending_reviews = {image["view_index"] - 1 for _, image in s["waiting"]}
        return [
            i
            for i in range(candidates)
            if i not in s["settled"] | s["blocked"] | pending_reviews
            and s["attempts"][i] < MAX_ATTEMPTS
        ]

    try:
        index = comfyui.drawing_state(repo, pid)
        for panel in panels:
            key = panel["id"]
            selected = index["selections"].get(key)
            if (
                selected
                and selected["panel_hash"] == e.digest(panel)
                and not request.get("force")
            ):
                report["skipped"].append(key)
                continue
            s = {
                "panel": panel,
                "attempts": {i: 0 for i in range(candidates)},
                "settled": set(),
                "blocked": set(),
                "waiting": [],
                "repairs": {},
                "ready": False,
                "review_attempts": {},
                "deferred": set(),
            }
            states[key] = s
            for batch in resumable_batches(
                repo, pid, initial, panel, cp, assignments[key]
            ):
                if batch.get("status") in ["running", "uncertain"]:
                    # A saved submission can still run after a timeout. Do not queue replacements.
                    blocked = batch.get(
                        "candidate_indices", list(range(batch.get("count", 6)))
                    )
                    s["blocked"].update(blocked)
                    s["deferred"].add(batch["id"])
                    for i in blocked:
                        if i in s["attempts"]:
                            s["attempts"][i] += 1
                    failure(
                        "队列待核对",
                        s,
                        "存在未确认结束的任务，保留任务编号，不重复提交",
                    )
                if request.get("force"):
                    continue
                if batch.get("status") == "failed" and not batch["images"]:
                    for i in batch.get("candidate_indices", []):
                        if i in s["attempts"]:
                            s["attempts"][i] += 1
                for image in batch["images"]:
                    view = image.get("view_index", 0) - 1
                    if view not in s["attempts"]:
                        continue
                    if not (repo.folder(pid) / "images" / image["file"]).is_file():
                        s["blocked"].add(view)
                        failure("文件", s, "已有图片文件缺失，保留记录待核对", view)
                        continue
                    s["attempts"][view] += 1
                    verdict = image.get("review", {}).get("verdict")
                    if verdict in ["pass", "uncertain"]:
                        s["settled"].add(view)
                    elif verdict == "reject":
                        s["repairs"][view] = image["review"].get("repair", "")
                    else:
                        s["waiting"].append((batch, image))
        recover_pending()
        # Saved pending images are reviewed together with new candidates below.
        for round_no in range(MAX_ATTEMPTS):
            pending = [s for s in states.values() if indices_for(s)]
            unprepared = [s for s in pending if not s["ready"]]
            if unprepared:
                checkpoint("准备绘图描述")
            for n, s in enumerate(unprepared):
                p = check()
                key = s["panel"]["id"]
                try:
                    dp = p.get("drawing_prompts", {}).get(key)
                    if (
                        request.get("refresh_adaptation")
                        or not detailed(dp)
                        or dp.get("panel_hash") != e.digest(s["panel"])
                        or dp.get("identity_hash") != identity_hash(p, s["panel"])
                        or not dp["payload"].get("framing")
                    ):
                        prepare(
                            app,
                            pid,
                            key,
                            jid,
                            progress("准备绘图描述", n, len(unprepared), "格"),
                            drawing_checkpoint=cp,
                        )
                    s["ready"] = True
                except Exception as exc:
                    failure("绘图描述", s, exc)
                progress("准备绘图描述", n + 1, len(unprepared), "格")(
                    "已处理，失败项目留到下一轮"
                )
            work = [(s, i) for s in pending if s["ready"] for i in indices_for(s)]
            if work:
                phase = (
                    "全部预选出图"
                    if round_no == 0
                    else f"队尾重试与补画 · 第 {round_no} 轮"
                )
                checkpoint(phase)
                phase_times[phase] = active_time()
                app.model.stop()
                try:
                    app.comfy.start()
                    startup_error = None
                except Exception as exc:
                    startup_error = exc
                for n, (s, slot) in enumerate(work):
                    check()
                    key = s["panel"]["id"]
                    s["attempts"][slot] += 1
                    checkpoint()
                    previous_ids = {
                        b["id"] for b in comfyui.drawing_state(repo, pid)["batches"]
                    }
                    try:
                        if startup_error:
                            raise startup_error
                        batch = comfyui.run_batch(
                            repo,
                            pid,
                            key,
                            progress(phase, n, len(work), "张已处理"),
                            cp,
                            1,
                            "angles",
                            assignments[key],
                            [slot],
                            s["repairs"],
                            review_required=True,
                            render_options={**options, **({"seed": (options["seed"] + slot) % 2**53} if "seed" in options else {})},
                        )
                        s["waiting"].extend((batch, image) for image in batch["images"])
                    except Exception as exc:
                        # Inspect only this call's durable batch. Ambiguous submissions are not retried.
                        for b in comfyui.drawing_state(repo, pid)["batches"]:
                            if b["id"] not in previous_ids and b["panel_id"] == key:
                                if b.get("status") in ["running", "uncertain"]:
                                    s["blocked"].add(slot)
                                    s["deferred"].add(b["id"])
                                s["waiting"].extend((b, image) for image in b["images"])
                        failure("绘图", s, exc, slot)
                    progress(phase, n + 1, len(work), "张已处理")(
                        "已处理；出错图片将在本轮其他格完成后重试"
                    )
                    checkpoint()
            recover_pending(cancel_running=True)
            review_waiting()
        for key, s in states.items():
            report["completed"].append(key)
            missing = [i + 1 for i in range(candidates) if i not in s["settled"]]
            if missing:
                report["unresolved"].append(
                    {
                        "panel": key,
                        "views": missing,
                        "pending_reviews": [i["id"] for _, i in s["waiting"]],
                    }
                )
        check()
        checkpoint(
            "已处理完，等待选图"
            if not report["unresolved"]
            else "已处理完，部分候选未通过或待重试"
        )
        update(
            {
                "message": report["phase"],
                "progress": {
                    "completed": len(panels),
                    "total": len(panels),
                    "unit": "格已处理",
                },
                "runtime": None,
                "detail_progress": None,
            }
        )
        return report
    except Exception as exc:
        report["errors"].append({"reason": str(exc), "stage": "数据保护"})
        checkpoint("保留已完成结果，需处理数据冲突")
        raise
    finally:
        try:
            app.model.stop()
        finally:
            app.comfy.stop()
