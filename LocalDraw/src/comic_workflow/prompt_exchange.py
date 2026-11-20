"""Versioned storyboard exchange and literal external-prompt rendering."""

import copy
import json
import uuid
from . import engine as e, comfyui
from .batch_drawing import story_fingerprint
from .repository import LOCK, safe_id
from .task_control import checkpoint, active_time
from .prompt_preflight import WRITING_GUIDANCE, with_render_plan, visual_cast

FORMAT = "comic-prompt-exchange/v1"


def panels(project):
    return [(page, panel) for page in project.get("pages", {}).values() for panel in page["panels"]]


def export_storyboard(repo, pid):
    project = repo.get(pid)
    items = panels(project)
    planned = (project.get("plan") or {}).get("pages", [])
    e.need(items and planned and all(p["id"] in project["pages"] for p in planned), "请先确认全部分镜，再导出提示词交换文件")
    cast = {c["id"]: c for c in project.get("canon", {}).get("characters", [])}
    scenes = {s["id"]: s for s in project.get("canon", {}).get("scenes", [])}
    return {
        "format": FORMAT, "project_id": pid, "storyboard_hash": story_fingerprint(project),
        "title": project.get("brief", {}).get("title", pid),
        "instructions": "给 Grok 或其他外部提示词编写工具：你的任务是把已确认的文字分镜转换为逐格绘图提示词。请直接填写 panels 中每格的 positive 和 negative，不要续写剧本、重排分镜或生成图片。绘图模型实际只接收这一格的 positive / negative，不会另外读取 storyboard。按下方 writing_process 和 prompt_writing_guidance 执行。返回可直接重新导入本工作室的完整 JSON 对象，不附加 Markdown 围栏、分析说明、代码或工作流。",
        "writing_process": [
            "先通读全部分镜，理解各格之间的动作推进、景别变化、人物出入场以及道具转移；再逐格填写，不要先写一段统一长模板粘贴到所有画格。",
            "每格先读 action、description、shot、angle、characters 中的 in_this_panel，再结合 visible_character_profiles 核对人物外观；角色卡只提供辨认依据，本格明确的服装、状态与动作优先。资料有冲突时不要把互相矛盾的描述同时写进提示词。",
            "按‘本格景别与角度 → 可见人数和主要动作 → 每位出镜人物的位置与简短外观 → 必要环境’组织 positive，绘图描述使用清晰英文。negative 填英文错误项或空字符串。",
            "完成后逐格自查：人数是否正确；有没有把未出镜角色写进来；耳朵、光环、发色和服装是否各自归属；动作与道具数量是否清楚；景别是否落实；是否重复了无关长段落；是否使用了依赖前文的省略说法。再比较相邻格，确认分镜里的差异确实写进了提示词。",
            "只填写 positive、negative，保留其余原始字段与分镜资料；format、project_id、storyboard_hash、panel_id、page_id 必须原样保留。默认填写全部 panels，不新增、合并、重复、重编号或漏掉画格。只有用户明确要求处理部分画格时，才删除未处理的 panels 条目；保留的每一格都必须有非空 positive 和字符串 negative。",
        ],
        "prompt_writing_guidance": WRITING_GUIDANCE,
        "panels": [{
            "page_id": page["id"], "panel_id": panel["id"],
            "storyboard": {
                "purpose": panel.get("purpose", ""), "action": panel.get("main_instant", ""),
                "description": panel.get("drawing_description", ""),
                "characters": [{"character": copy.deepcopy(cast.get(role["character_id"], {"id": role["character_id"]})),
                                "in_this_panel": copy.deepcopy(role)} for role in panel.get("characters", [])],
                "scene": copy.deepcopy(scenes.get(panel.get("scene_id"), {})),
                "shot": panel.get("shot", ""), "angle": panel.get("angle", ""),
                "aspect_ratio": panel.get("aspect_ratio", ""),
                "visible_character_profiles": visual_cast(project, panel),
                "dialogue": [copy.deepcopy(t) for t in page.get("lettering", []) if t["panel_id"] == panel["id"]],
            },
            "positive": "", "negative": "",
        } for page, panel in items],
    }


def export_download(repo, pid):
    """Attach portable pages only to an explicit human download."""
    with LOCK:
        project = repo.get(pid)
        document = export_storyboard(repo, pid)
        document["portable_storyboard"] = {
            "format": "local-draw-story-v1",
            "brief": {key: value for key, value in project["brief"].items() if key != "character_refs"},
            "canon": project["canon"],
            "pages": [project["pages"][key] for key in sorted(project["pages"])],
        }
        return document


def preview(repo, pid, document):
    e.need(isinstance(document, dict), "文件顶层须是 JSON 对象")
    e.need(len(json.dumps(document, ensure_ascii=False)) <= 2_000_000, "文件超过 2 MB，请分批导入")
    project = repo.get(pid)
    expected = {p["id"]: (page, p) for page, p in panels(project)}
    errors = []
    if document.get("format") != FORMAT:
        errors.append("文件格式不符，请使用导出的 JSON 文件填写提示词")
    if document.get("project_id") != pid:
        errors.append("文件属于其他作品")
    if document.get("storyboard_hash") != story_fingerprint(project):
        errors.append("分镜或角色资料已变化，请重新导出后填写；旧文件不会覆盖当前版本")
    rows = document.get("panels")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
        errors.append("panels 须包含 1–1000 个画格")
        rows = []
    seen, normalized = set(), []
    for n, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            errors.append(f"第 {n} 条画格格式错误")
            continue
        key = row.get("panel_id")
        if not isinstance(key, str) or key not in expected:
            errors.append(f"第 {n} 条画格编号不存在")
            continue
        if key in seen:
            errors.append(f"{key} 重复出现")
            continue
        seen.add(key)
        page, panel = expected[key]
        if row.get("page_id", page["id"]) != page["id"]:
            errors.append(f"{key} 所属页不正确")
        for field in ("positive", "negative"):
            value = row.get(field)
            if not isinstance(value, str) or len(value) > 20000 or (field == "positive" and not value.strip()):
                errors.append(f"{key} 的 {field} 须为文本，正向不能为空，每项最多 20000 字符")
        if isinstance(row.get("positive"), str) and isinstance(row.get("negative"), str):
            normalized.append({"panel_id": key, "page_id": page["id"], "panel_hash": e.digest(panel),
                               "summary": panel.get("main_instant", ""),
                               "positive": row["positive"], "negative": row["negative"]})
    missing = sorted(set(expected) - seen)
    return {**with_render_plan(repo, pid, project, normalized), "valid": not errors, "errors": errors, "missing": missing, "panels": normalized,
            "total": len(expected), "preview_hash": e.digest(document), "storyboard_hash": story_fingerprint(project)}


def save_import(repo, pid, request):
    e.need(request.get("confirm") is True, "请先预览并确认导入")
    with LOCK:
        result = preview(repo, pid, request.get("document"))
        e.need(result["valid"], "；".join(result["errors"]))
        e.need(result["preview_hash"] == request.get("preview_hash"), "文件已改变，请重新预览")
        bundle = {"id": uuid.uuid4().hex, "created": e.stamp(), "project_id": pid,
                  "storyboard_hash": result["storyboard_hash"], "panels": result["panels"]}
        folder = repo.folder(pid) / "prompt-imports"
        e.save(folder / (bundle["id"] + ".json"), bundle)
        e.save(folder / "current.json", {"id": bundle["id"]})
        return bundle


def load_import(repo, pid, bundle_id):
    bundle = e.read(repo.folder(pid) / "prompt-imports" / (safe_id(bundle_id) + ".json"))
    e.need(bundle.get("project_id") == pid and isinstance(bundle.get("panels"), list), "提示词导入记录无效")
    return bundle


def latest_bundle(repo, pid):
    folder = repo.folder(pid) / "prompt-imports"
    current = e.read(folder / "current.json") if (folder / "current.json").exists() else None
    return load_import(repo, pid, current["id"]) if current else None


def current_import(repo, pid):
    bundle = latest_bundle(repo, pid)
    if not bundle:
        return {"bundle": None}
    project = repo.get(pid)
    return {"bundle": bundle, "stale": bundle["storyboard_hash"] != story_fingerprint(project),
            **with_render_plan(repo, pid, project, bundle["panels"])}


def effective_negative(row, settings):
    panel = settings.get("negative_by_panel", {}).get(row["panel_id"], row["negative"])
    common = settings.get("common_negative", "")
    return "\n".join(part for part in (panel, common) if part)


def prepare_import_settings(repo, settings, model, targets, bundle):
    from .characters import CharacterLibrary
    from .character_prompt_cleanup import default_visual_prompt
    from .clothing_policy import resolve_selections
    from .quick_sample import selected_loras
    e.need(isinstance(settings, dict), "出图设置无效")
    roles = settings.get("characters", [])
    e.need(isinstance(roles, list) and len(roles) <= 4, "最多选择 4 个角色")
    cards, selected, seen = [], [], set()
    library = CharacterLibrary(repo.root.parent / "characters")
    for role in roles:
        e.need(isinstance(role, dict) and isinstance(role.get("name"), str), "角色选择无效")
        card = library.get(role["name"])
        e.need(card["revision"] == role.get("revision"), "角色卡已更新，请重新打开出图设置")
        e.need(card["id"] not in seen, "角色重复")
        seen.add(card["id"])
        selected.append({"name": card["name"], "revision": card["revision"],
                         "prompt": default_visual_prompt(card["profile"]) or card["name"]})
        cards.append(card)
    counts = settings.get("counts", {})
    e.need(isinstance(counts, dict) and counts.keys() <= set(targets) and
           all(type(n) is int and 1 <= n <= 16 for n in counts.values()), "每格张数须为 1–16")
    common = settings.get("common_negative", "")
    edits = settings.get("negative_by_panel", {})
    e.need(isinstance(common, str) and len(common) <= 20000, "通用负面提示词最多 20000 字")
    e.need(isinstance(edits, dict) and edits.keys() <= set(targets) and
           all(isinstance(v, str) and len(v) <= 20000 for v in edits.values()), "逐格负面提示词无效")
    suffixes = {}
    for row in bundle["panels"]:
        if row["panel_id"] not in targets:
            continue
        e.need(len(effective_negative(row, settings)) <= 20000, row["panel_id"] + " 合并后的负面提示词最多 20000 字")
        if selected:
            looks = resolve_selections(row["positive"], selected, cards)
            suffix = ", ".join(s["prompt"] for s in looks if s["prompt"])
            e.need(len(row["positive"]) + len(suffix) + 1 <= 20000, row["panel_id"] + " 加入角色后提示词过长")
            suffixes[row["panel_id"]] = suffix
    return (selected_loras(settings.get("loras", []), model),
            {"selected_characters": selected, "character_suffixes": suffixes,
             "common_negative": common, "negative_by_panel": dict(edits),
             "candidate_slots_by_panel": {key: list(range(n)) for key, n in counts.items()}})


def prepare_request(repo, pid, request):
    from . import drawing_settings
    project = repo.get(pid)
    bundle = load_import(repo, pid, request["bundle"])
    e.need(bundle["storyboard_hash"] == story_fingerprint(project), "分镜或角色资料已变化，请重新导入")
    targets = request.get("targets") if request.get("targets") is not None else [r["panel_id"] for r in bundle["panels"]]
    e.need(isinstance(targets, list) and targets and all(isinstance(t, str) for t in targets) and len(set(targets)) == len(targets)
           and set(targets) <= {r["panel_id"] for r in bundle["panels"]}, "请选择导入文件中的画格")
    count = request.get("candidate_count", 2)
    e.need(type(count) is int and 1 <= count <= 16, "每格候选数量须为 1–16")
    steps, seed = request.get("steps"), request.get("seed")
    e.need(steps is None or type(steps) is int and 1 <= steps <= 100, "步数须为 1–100")
    e.need(seed is None or type(seed) is int and 0 <= seed < 2**53, "种子须为 0–9007199254740991 的整数")
    prefs = drawing_settings.load(repo, pid)
    from .image_size import settings as size_settings
    size = size_settings({**(prefs or {}), **{k: request[k] for k in ("resolution", "aspect_ratio") if k in request}})
    model = request.get("checkpoint") or (prefs or {}).get("checkpoint") or comfyui.config()["checkpoint"]
    e.need(isinstance(model, str) and model, "请选择绘图模型")
    explicit_loras, setup = None, {}
    if "import_setup" in request:
        explicit_loras, setup = prepare_import_settings(repo, request["import_setup"], model, targets, bundle)
    from . import anima_consistency, qwen_edit
    references = request.get("references_by_panel", {})
    e.need(isinstance(references, dict) and set(references) <= set(targets),
           "一致性参考只能指定本次绘制的画格")
    e.need(not references or model in {anima_consistency.CHECKPOINT, qwen_edit.CHECKPOINT},
           "固定图像参考目前仅支持 Anima Delta v6 和 Qwen Image Edit")
    editing = model == qwen_edit.CHECKPOINT
    if editing:
        e.need(set(references) == set(targets), 'Qwen 编辑的每个目标画格都须明确选择参考图')
    edit_snapshot = {'edit_recipe': copy.deepcopy(qwen_edit.RECIPE), 'edit_models': qwen_edit.freeze_assets()} if editing else {}
    frozen, cached = {}, {}
    for panel_id, image_id in references.items():
        if editing:
            panel = next(p for _, p in panels(project) if p['id'] == panel_id)
            frozen[panel_id] = qwen_edit.freeze_references(repo, pid, panel, image_id)
            continue
        e.need(isinstance(image_id, str) and image_id, "请选择有效的一致性参考图")
        if image_id not in cached:
            cached[image_id] = anima_consistency.freeze_reference(repo, pid, image_id)
        frozen[panel_id] = copy.deepcopy(cached[image_id])
    # Freeze the resolved per-panel choices with the job, including for resume.
    loras_by_panel = {}
    for _, panel in panels(project):
        if panel["id"] in targets:
            loras_by_panel[panel["id"]] = explicit_loras if explicit_loras is not None else (
                drawing_settings.loras_for_panel({**prefs, "checkpoint": model}, project, panel) if prefs else [])
            if editing:
                e.need(not loras_by_panel[panel['id']], 'Qwen 编辑试验请关闭 LoRA，使用固定图像参考')
            if explicit_loras is None and not editing and panel["id"] not in frozen and (prefs or {}).get("reference_character_id"):
                from .characters import CharacterLibrary
                selected = anima_consistency.selected_character_reference(project, panel, model, prefs,
                    library=CharacterLibrary(repo.root.parent / "characters"))
                if selected:
                    cache_key = "character:" + selected["source_image_id"]
                    if cache_key not in cached:
                        cached[cache_key] = anima_consistency.freeze_character_reference(selected)
                    frozen[panel["id"]] = copy.deepcopy(cached[cache_key])
    return {"action": "draw_imported", "bundle": bundle["id"], "targets": targets, "candidate_count": count,
            "checkpoint": model, "steps": steps, "seed": seed, "run_key": uuid.uuid4().hex,
            "loras_by_panel": copy.deepcopy(loras_by_panel), "references_by_panel": frozen,
            "consistency_lora": False if editing or explicit_loras is not None else (prefs or {}).get("consistency_lora", False),
            **edit_snapshot, **size, **setup}


def draw_imported(app, pid, request, update, jid):
    if request.get("revision_note"):
        from .page_redraw import apply_revision
        apply_revision(app, pid, request, update, jid)
    from .comfy_recovery import recover_batch
    from .drawing_quantity import slots_for_panel
    repo = app.repo
    bundle = load_import(repo, pid, request["bundle"])
    rows = [r for r in bundle["panels"] if r["panel_id"] in request["targets"]]
    total = sum(len(slots_for_panel(request, row['panel_id'])) for row in rows)
    report = {"import": bundle["id"], "batches": [], "errors": [], "total": total}
    path = repo.folder(pid) / "runs" / jid / "imported-drawing.json"
    done, started = 0, active_time()

    def check():
        checkpoint()
        e.need(story_fingerprint(repo.get(pid)) == bundle["storyboard_hash"], "分镜或角色资料变化，已停止；已有图片保留")

    def emit(event):
        nonlocal total
        total = sum(len(slots_for_panel(request, row['panel_id'])) for row in rows)
        report['total'] = total
        detail = dict(event) if isinstance(event, dict) else {"message": event}
        detail.update(progress={"completed": done, "total": total, "unit": "张已处理"},
                      eta={"remaining_seconds": (active_time()-started)/done*(total-done) if done else None,
                           "scope": "导入提示词出图"})
        update(detail)

    try:
        check()
        if request.get('page_id') or request.get('problem_redraw'):
            app.model.prepare_redraw()
        else:
            app.model.stop()
        app.comfy.start()
        # A new import must not race any older ambiguous submission in this work.
        for pending in comfyui.drawing_state(repo, pid)["batches"]:
            if pending.get("status") in {"running", "uncertain"}:
                recovered = recover_batch(repo, pid, pending)
                e.need(recovered["status"] not in {"running", "uncertain"}, "之前的出图尚未确认结束，暂不提交新的图片")
        for row in rows:
            row = {**row, "negative": effective_negative(row, request)}
            suffix = request.get("character_suffixes", {}).get(row["panel_id"])
            if suffix:
                row = {**row, "positive": row["positive"] + "\n" + suffix}
            handled = set()
            while True:
                check()
                # Read after the pause boundary: the user may have changed this
                # schedule while the same worker was parked, without restarting.
                slots = slots_for_panel(request, row['panel_id'])
                pending_slots = [s for s in slots if s not in handled]
                if not pending_slots:
                    break
                slot = pending_slots[0]
                if slot in request.get('retired_candidate_slots_by_panel', {}).get(row['panel_id'], []):
                    handled.add(slot)
                    done += 1
                    emit('保留之前已结束的候选记录，不占本次新增数量')
                    continue
                previous = [b for b in comfyui.drawing_state(repo, pid)["batches"]
                            if b.get("render_options", {}).get("import_run") == request["run_key"]
                            and b["panel_id"] == row["panel_id"] and slot in b.get("candidate_indices", [])]
                for batch in previous:
                    if batch["status"] in {"running", "uncertain"}:
                        recovered = recover_batch(repo, pid, batch)
                        e.need(recovered["status"] not in {"running", "uncertain"}, "上次提交状态未确认，暂不重复绘制，请稍后继续")
                        batch.update(recovered)
                if any(b.get("images") for b in previous):
                    e.need(all((repo.folder(pid) / "images" / image["file"]).is_file()
                               for b in previous for image in b.get("images", [])), "已有候选文件缺失，请核对文件；未自动重复出图")
                    done += 1
                    handled.add(slot)
                    emit("复用此任务已保存的图片")
                    continue
                options = {"external_prompt": True, "direct_prompt": True, "reference": False, "control": False,
                           "resolution": request.get("resolution", "default"), "aspect_ratio": request.get("aspect_ratio", "storyboard"),
                           "hand_detail": False, "import_bundle": bundle["id"], "import_run": request["run_key"]}
                if "consistency_lora" in request:
                    options["consistency_lora"] = request["consistency_lora"]
                if row["panel_id"] in request.get("references_by_panel", {}):
                    from .qwen_edit import CHECKPOINT as EDIT_CHECKPOINT
                    if request['checkpoint'] == EDIT_CHECKPOINT:
                        options.update(consistency_lora=False, edit_references=copy.deepcopy(
                            request['references_by_panel'][row['panel_id']]))
                        options.update(edit_recipe=copy.deepcopy(request['edit_recipe']),
                                       edit_models=copy.deepcopy(request['edit_models']))
                    else:
                        options.update(consistency_lora=True, consistency_reference=copy.deepcopy(
                            request["references_by_panel"][row["panel_id"]]))
                if request.get("steps") is not None:
                    options["steps"] = request["steps"]
                if request.get("seed") is not None:
                    options["seed"] = (request["seed"] + slot) % 2**53
                emit(f"按导入原文绘制 {row['panel_id']} · {slots.index(slot) + 1}/{len(slots)}")
                try:
                    batch = comfyui.run_batch(repo, pid, row["panel_id"], emit, request["checkpoint"], 1,
                        "seeds", copy.deepcopy(request.get("loras_by_panel", {}).get(row["panel_id"], [])),
                        [slot], review_required=False, render_options=options, prompt_override=row)
                    report["batches"].append(batch["id"])
                except Exception as exc:
                    report["errors"].append({"panel_id": row["panel_id"], "candidate": slot+1, "error": str(exc)})
                    if any(b.get("status") in {"running", "uncertain"} for b in comfyui.drawing_state(repo, pid)["batches"]
                           if b.get("render_options", {}).get("import_run") == request["run_key"]):
                        raise
                done += 1
                handled.add(slot)
                e.save(path, report)
                emit("当前结果已保存")
        report["completion_message"] = f"按原文出图结束：已处理 {done} 张，失败 {len(report['errors'])} 张；由你选图，不自动审核或补画。"
        return report
    finally:
        e.save(path, report)
        app.comfy.stop()
