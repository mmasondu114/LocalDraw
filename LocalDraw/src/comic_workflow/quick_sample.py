"""Create a one-frame sample from user text without loading any model."""

import uuid
import time
import re
from . import engine as e, comfyui
from .repository import LOCK
from .schemas import CHARACTER
from .characters import CharacterLibrary
from .paths import ROOT, prompt_model


def clear_images(app, pid):
    """Clear one prompt session's outputs while preserving its input/settings."""
    import shutil

    with app.jobs.lock, LOCK:
        project = app.repo.get(pid)
        e.need(project.get("quick_sample"), "只能清除提示词出图的图片")
        e.need(not any(control["job"].get("project") == pid
                       for control in app.jobs.controls.values()),
               "请等待当前出图任务结束，或取消任务后再清除图片")
        e.need(not any(job.get("project") == pid and job["status"] in
                       {"running", "pausing", "paused", "cancelling"}
                       for job in app.jobs.listing(limit=None)),
               "请先结束当前出图任务再清除图片")
        folder = app.repo.folder(pid).resolve()
        images = folder / "images"
        e.need(images.resolve().is_relative_to(folder) and not images.is_symlink(),
               "无效图片目录")
        state = comfyui.drawing_state(app.repo, pid)
        e.need(not any(b.get("status") in {"running", "queued", "submitted", "uncertain"}
                       for b in state["batches"]), "仍有图片状态未确认，请先核对任务状态")
        count = sum(len(batch.get("images", [])) for batch in state["batches"])
        if images.exists():
            if project.get("transient", True):
                shutil.rmtree(images)
            else:
                backup = folder / "cleared-images" / uuid.uuid4().hex
                e.need(backup.resolve().is_relative_to(folder), "无效备份目录")
                backup.mkdir(parents=True)
                e.save(backup / "record.json", {"created": e.stamp(), "cleared": count})
                images.rename(backup / "images")
                return {"cleared": count, "backup": str(backup)}
        return {"cleared": count}


def character_prompt_problem(prompt):
    """Catch chat-card sections in the editable drawing field, without rewriting it."""
    return bool(re.search(
        r"(?im)^\s*(?:#{1,6}\s*)?(?:\*\*)?"
        r"(?:personality|speech\s+style|interaction\s+guidelines|"
        r"background|relationships|system\s+prompt|"
        r"性格(?:与行为习惯)?|说话方式(?:与台词样例)?|互动(?:规则|指南)|"
        r"人物关系|背景(?:设定)?|系统提示词)\s*(?:\*\*)?\s*[:：]",
        prompt,
    ))


@prompt_model()
def translate(app, request, update, jid):
    """Explicit, reviewable English conversion; never submits drawing work."""
    import re
    from . import creative
    from .paths import DATA
    from .schemas import obj, arr, S

    text, negative = request.get("text", ""), request.get("negative", "")
    e.need(isinstance(text, str) and 1 <= len(text.strip()) <= 12000, "请先填写正向提示词")
    e.need(isinstance(negative, str) and len(negative) <= 12000, "负面提示词过长")
    refs, selections = selected_characters(app.repo, request.get("characters", []), False)
    from .clothing_policy import resolve_selections
    selections = resolve_selections(text, selections, refs)
    context = {"positive": text, "negative": negative, "characters": selections}
    e.need(len(e.encoded(context)) <= 18000, "内容过长，请先精简角色描述")
    import tempfile
    temporary = tempfile.TemporaryDirectory(prefix="local-draw-translation-")
    from pathlib import Path
    folder = Path(temporary.name)
    schema = obj({"positive": S, "negative": S, "characters": arr(obj({"name": S, "prompt": S}))})
    instruction = (
        "你是 SDXL 动漫绘图提示词翻译器。将用户输入转为简洁、具体、可画的英文标签和短语，"
        "整理已有英文或翻译中文，不编造剧情、服装或人物特征。保持用户要求的人数、景别、动作、场景和重要细节。"
        "输入角色框可能误粘贴完整聊天角色卡。只提取可见外观，删除性格、说话方式、称呼用户、背景、"
        "人物关系、互动规则和保持一致等对模型的指令，不将其翻译成绘图标签。"
        "角色姓名使用用户提供的英文名称与作品标签；没有英文名称时不要猜造型。"
        "正向场景明确指定的服装和外观优先于角色卡的默认服装；例如场景要求女仆装时，"
        "角色外观中不要再附加黑蓝战术制服或原服装。忽略‘Use uploaded image’等指令，参考图由程序单独加载。"
        "不要照抄‘忠实还原官方’等抽象指令，不假设模型认识角色名字；以用户的具体外观为准。"
        "positive 仅翻译场景与动作，不重复 characters 中的外观。角色引用 /名字 从画面标签中移除。"
        "characters 与输入数量和顺序完全一致，name 原样保留，每个 prompt 翻译对应外观，"
        "不得合并不同角色特征；不要把姓名、性格、台词、资料中的未知项或‘没有纹身’翻成正向标签。"
        "负面提示词按用户原意译为英文标签，不添加不相关禁止项。已有英文权重语法和 LoRA 触发词保留。"
        "除 name 字段外全部使用英文。positive 不超过 180 个英文词，negative 不超过 100 个英文词，"
        "每个角色 prompt 不超过 100 个英文词。不加入解释。"
    )
    try:
        app.prepare_language(update=update)
        result, metrics = creative.structured(instruction, context, schema, folder, update, max_tokens=2400)
        e.need([c["name"] for c in result["characters"]] == [c["name"] for c in selections],
               "转换后的角色未一一对应，未覆盖原输入，请重试")
        converted = [result["positive"], result["negative"]] + [c["prompt"] for c in result["characters"]]
        e.need(all(not re.search(r"[\u3400-\u9fff]", value) for value in converted),
               "转换结果仍含中文，未覆盖原输入，请重试")
        e.need(result["positive"].strip() and all(c["prompt"].strip() for c in result["characters"]),
               "转换结果不完整，未覆盖原输入，请重试")
        e.need(not any(character_prompt_problem(c["prompt"]) for c in result["characters"]),
               "整理结果仍含聊天角色设定，未覆盖原输入，请重试")
        e.save(folder / "draft.json", {"kind": "prompt_translate", "source": context, "result": result, "metrics": metrics})
        return result
    finally:
        temporary.cleanup()


def selected_characters(repo, items, use_reference):
    e.need(isinstance(items, list), "角色选择必须是列表")
    e.need(type(use_reference) is bool, "参考图选项无效")
    library = CharacterLibrary(repo.root.parent / "characters")
    refs, selections, seen = [], [], set()
    for item in items:
        e.need(isinstance(item, dict), "无效角色选择")
        card = library.get(item.get("name"))
        e.need(card["id"] not in seen, "不能重复选择同一个角色")
        e.need(item.get("revision") == card["revision"], "角色资料已更新，请刷新角色列表后重试")
        prompt = item.get("prompt", "")
        e.need(isinstance(prompt, str) and len(prompt) <= 6000, "角色提示词最多 6000 字符")
        character_reference = item.get("use_reference", True)
        e.need(type(character_reference) is bool, "角色参考图选项无效")
        e.need(prompt.strip() or (use_reference and character_reference and card.get("image_id")),
               "请填写角色外观提示词，或启用该角色的参考图")
        if use_reference and character_reference and card.get("image_id"):
            e.need(library.image_path(card["image_id"]).is_file(), card["name"] + " 的参考图不存在")
        seen.add(card["id"])
        refs.append(card)
        selection = {"name": card["name"], "revision": card["revision"], "prompt": prompt}
        if "use_reference" in item:
            selection["use_reference"] = item["use_reference"]
        if item.get("region") is not None:
            from .lora_binding import region_valid
            e.need(region_valid(item["region"]), "角色区域无效")
            selection["region"] = item["region"]
        selections.append(selection)
    if use_reference:
        e.need(any(c.get("image_id") and s.get("use_reference", True)
                   for c, s in zip(refs, selections)),
               "请至少勾选一个角色的参考图，或取消使用参考图")
        config = ROOT / "config/reference-models.json"
        e.need(config.is_file() and e.read(config).get("enabled"), "角色参考图功能未启用")
        for model in e.read(config)["models"]:
            path = ROOT / model["directory"] / model["name"]
            e.need(path.is_file() and path.stat().st_size == model["size"], "参考图模型未完整，请先运行 08-download-reference.bat")
    return refs, selections


def selected_loras(items, checkpoint, installed=None):
    """Resolve user selections locally; compatibility is structural, not quality approval."""
    from .lora_binding import MAX_LORAS
    e.need(isinstance(items, list) and len(items) <= MAX_LORAS, "最多同时选择 5 个 LoRA")
    if not items:
        return []
    from .lora_inspection import inspect_local
    inspections = inspect_local(checkpoint=checkpoint)
    available = {comfyui.lora_file_key(name)
                 for name in (installed or comfyui.discover()).get("loras", [])}
    labels = comfyui.lora_labels()
    selected, seen = [], set()
    for item in items:
        e.need(isinstance(item, dict), "无效 LoRA 选择")
        e.need(item.get("character_id") != "unresolved",
               "角色 LoRA 尚未关联，请先指定对应的角色；单人整图使用时可明确选择全图")
        file = item.get("file")
        e.need(isinstance(file, str), "LoRA 文件未找到，请刷新列表")
        file = comfyui.lora_file_key(file)
        from .lora_folders import matches
        e.need(matches(file, checkpoint), "LoRA 不在当前模型对应的文件夹中")
        e.need(file in available, "LoRA 文件未找到，请刷新列表")
        e.need(file not in seen, "不能重复选择同一个 LoRA")
        seen.add(file)
        e.need(inspections.get(file, {}).get("structurally_compatible") is True,
               file + " 不兼容或结构未能确认，不能用于当前底模出图")
        weight = item.get("weight", 0.7)
        e.need(type(weight) in (float, int) and 0 <= weight <= 2, "LoRA 强度须为 0–2")
        selected.append({"file": file, "label": labels.get(file, {}).get("name") or file,
                         "weight": weight, "clip_weight": weight, "trigger_words": "",
                         **({"character_id": labels[file]["character_id"], "character": labels[file].get("character", "")}
                            if "character_id" in labels.get(file, {}) else
                            {k: item[k] for k in ("character_id", "character", "scope", "characters", "target_character_ids") if k in item}),
                         "experimental_checkpoint": checkpoint, "confirmed_compatibility": False})
    return selected


def prompt_characters(refs):
    characters = []
    for n, card in enumerate(refs or [{"name": "未绑定角色（不要求出镜）", "profile": {}}], 1):
        character = {key: ([] if spec.get("type") == "array" else "")
                     for key, spec in CHARACTER["properties"].items()}
        profile = card["profile"]
        character.update(id=f"C{n:03}", name=card["name"], origin="original",
                         source_notes=["用户角色库快照；此处不推断原作事实"],
                         appearance=e.encoded(profile) if profile else "人物是否出现及外观仅按用户输入",
                         voice=profile.get("voice", ""), motivation=profile.get("motivation", ""))
        characters.append(character)
    return characters


def prompt_panel(text, ratio, cast, page="P001", number=1):
    return {"id": f"{page}-K{number:02}", "page_id": page, "number": number, "purpose": "按用户描述试画",
             "layout": "单幅画面，填满画布", "aspect_ratio": ratio, "shot": "遵循用户指定的景别",
             "angle": "遵循用户指定的视角", "composition": "按用户画面描述，不留无意的白边",
             "scene_id": "S001", "characters": cast, "main_instant": text,
             "background": "按用户描述，不添加无关情节", "props": [], "transition_in": "独立单幅画面",
             "transition_out": "独立单幅画面", "state_after": {}, "drawing_description": text}


def prepare_sample(repo, request):
    text = request.get("text", "")
    e.need(isinstance(text, str) and 1 <= len(text.strip()) <= 12000, "请输入 1–12000 字符的提示词")
    negative = request.get("negative", "")
    e.need(isinstance(negative, str) and len(negative) <= 12000, "负面提示词过长")
    count = request.get("count", 1)
    e.need(type(count) is int and 1 <= count <= 16, "一次请选择 1–16 张")
    checkpoint = request.get("checkpoint")
    from .comparison_models import available
    discovery = comfyui.discover()
    installed = discovery["checkpoints"]
    catalog = {m["name"]: m for m in available()}
    e.need(checkpoint in installed and (checkpoint not in catalog or catalog[checkpoint]["installed"]),
           "请先下载完整的绘图模型，再选择出图")
    ratio = request.get("aspect_ratio", "3:2")
    from .image_size import dimensions
    resolution = request.get("resolution", "default")
    dimensions(ratio, resolution)
    loras = selected_loras(request.get("loras", []), checkpoint, discovery)
    # Only explicit selections bind characters; slash text remains literal.
    items = request.get("characters", [])
    from .prompt_models import MODELS
    using_anima = checkpoint in MODELS
    # API callers also get image binding by default. An explicit false remains
    # a supported text-only choice, including frozen historical projects.
    use_reference = request.get("use_reference")
    if "use_reference" not in request and using_anima:
        use_reference = False
    elif "use_reference" not in request:
        e.need(isinstance(items, list), "角色选择必须是列表")
        library = CharacterLibrary(repo.root.parent / "characters")
        use_reference = False
        for item in items:
            e.need(isinstance(item, dict), "无效角色选择")
            card = library.get(item.get("name"))
            if card.get("image_id") and item.get("use_reference", True) is not False:
                use_reference = True
    reference_weight = request.get("reference_weight", 0.6)
    e.need(type(reference_weight) in (int, float) and 0 <= reference_weight <= 1,
           "参考图强度须为 0–1")
    refs, selections = selected_characters(repo, items, use_reference)
    e.need(not use_reference or reference_weight > 0,
           "参考图已启用，请设置大于 0 的强度，或取消角色的参考图勾选")
    for selection in selections:
        e.need(not character_prompt_problem(selection["prompt"]),
               selection["name"] + " 的外观提示词包含聊天设定，请先点击“整理 / 转换英文提示词”并核对")
    from .clothing_policy import resolve_selections
    resolved = resolve_selections(text, selections, refs)
    for source, selection in zip(selections, resolved):
        if source["prompt"] != selection["prompt"]:
            selection["source_prompt"] = source["prompt"]
    selections = resolved
    regional = request.get("regional_characters", False)
    if using_anima:
        e.need(not use_reference and not regional,
               "当前模型使用文字与同架构 LoRA；请关闭 SDXL 参考图和分区绘制")
    e.need(type(regional) is bool, "分区绘制选项无效")
    hand_detail = request.get("hand_detail", comfyui.config().get("hand_detail", False))
    e.need(type(hand_detail) is bool, "手部增强选项无效")
    delta_matte_skin = request.get("delta_matte_skin", comfyui.config().get("delta_matte_skin", True))
    e.need(type(delta_matte_skin) is bool, "Invalid matte skin setting")
    consistency_lora = request.get("consistency_lora", comfyui.config().get("consistency_lora", False))
    e.need(type(consistency_lora) is bool, "一致性 LoRA 选项无效")
    if regional:
        from .lora_binding import separate_regions
        regional = len(selections) >= 2 and separate_regions(c.get("region") for c in selections)
    if regional:
        from .regional_prompt import validate
        validate(selections)
    positive = "\n\n".join([text] + [c["prompt"] for c in selections if c["prompt"].strip()])
    e.need(len(positive) <= 24000, "合并后的提示词过长，请精简角色外观描述")
    characters = prompt_characters(refs)
    cast = [{"character_id": c["id"], "position": "按用户描述", "gaze": "按用户描述",
             "action": "按用户描述的单一瞬间", "expression": "按用户描述"}
            for c in characters] if refs else []
    brief = {"title": "试画 · " + text.splitlines()[0][:24], "genre": "单张画面试画",
             "direction": text, "page_count": 1, "panels_per_page": 1,
             "reading_direction": "LTR", "page_mode": "single_page", "first_story_page_side": "right",
             "must_include": [], "avoid": [], "character_material": text, "character_refs": refs}
    panel = prompt_panel(text, ratio, cast)
    p = {"version": 1, "workflow": "direct", "quick_sample": True, "script": text, "brief": brief,
         "prompt_input": {"text": text, "negative": negative, "count": count,
                          "checkpoint": checkpoint, "aspect_ratio": ratio, "resolution": resolution, "loras": loras,
                          "characters": selections, "use_reference": use_reference,
                          "regional_characters": regional,
                          "hand_detail": hand_detail,
                          "consistency_lora": consistency_lora,
                          "delta_matte_skin": delta_matte_skin,
                          "reference_weight": reference_weight},
         "provenance": "用户直接提示词出图；衣着按场景提示词优先，未指定时使用角色默认服装，不经过 Qwen",
         "canon": {"characters": characters, "scenes": [{"id": "S001", "name": "用户指定的场景",
            "layout": "按用户描述", "landmarks": [], "lighting": "按用户描述", "axis_rule": "独立画面"}],
            "props": [], "world_rules": [], "initial_state": {}},
         "options": None, "outline": {"selected_option": "direct", "premise": text, "beats": [text],
            "ending": "单幅试画，无新增剧情", "must_include_mapping": [], "unknowns": []},
         "plan": {"pages": [{"id": "P001", "number": 1, "scene_ids": ["S001"],
            "character_ids": [c["character_id"] for c in cast], "panel_count": 1,
            "purpose": "单幅试画", "beat": text, "hook": "", "reveal": "", "turn_after": True,
            "constraints": ["不扩写剧情，只画输入描述的一个瞬间"]}]},
         "pages": {"P001": {"id": "P001", "number": 1, "reading_direction": "LTR",
            "layout": "单幅画面", "page_turn_note": "独立单幅，无翻页", "state_in": {},
            "panels": [panel], "lettering": [], "state_out": {}}}}
    e.validate_project(p, complete=True)
    from .character_binding import identity_hash
    p["drawing_prompts"] = {"P001-K01": {"panel_hash": e.digest(panel),
        "identity_hash": identity_hash(p, panel), "payload": {"panel_id": "P001-K01",
        "positive": positive, "negative": negative, "character_anchors": [], "composition_variants": [],
        "adaptation_notes": "场景提示词原样发送；角色默认服装仅在提示词未指定衣着时补入"}}}
    from .lora_binding import route, plan
    loras = route(loras, p, panel, comfyui.lora_labels())
    if not using_anima:
        plan(loras, p, panel, p["drawing_prompts"]["P001-K01"]["payload"])
    p["prompt_input"]["loras"] = loras
    return p


def create(repo, request, *, persistent=False):
    p = prepare_sample(repo, request)
    pid = "prompt-" + uuid.uuid4().hex[:12] if persistent else repo.create_transient()
    p["transient"] = not persistent
    if persistent:
        p["brief"]["title"] = "排队出图 · " + p["script"].strip().splitlines()[0][:24]
    with LOCK:
        folder = repo.folder(pid)
        e.save(folder / "project.json", p)
        e.save(folder / "workspace.json", {"stage": "prompt", "target": "", "updated": 1, "views": {}})
    return {"project": pid}


def run(app, pid, request, update, jid):
    from .prompt_board import sample_input
    p = sample_input(app.repo, pid, request)
    e.need(p.get("quick_sample") and e.digest(p) == request["base_hash"], "试画输入已变化，请刷新")
    spec = p["prompt_input"]
    loras = selected_loras(spec.get("loras", []), spec["checkpoint"])
    if not p.get("transient", True):
        # Validate the files, but keep queued character bindings and weights frozen.
        import copy
        loras = copy.deepcopy(spec.get("loras", []))
    pending = [b for b in comfyui.drawing_state(app.repo, pid)["batches"]
               if b.get("status") in {"running", "uncertain"}]
    e.need(not any(not request.get("run_key") or b.get("render_options", {}).get("queue_run") != request["run_key"]
                   for b in pending), "有其他尚未完成或状态未确认的出图任务")
    report = {"batches": [], "errors": [], "count": spec["count"], "loras": loras, "status": "running"}
    output = app.repo.folder(pid) / "runs" / jid / "prompt-batch.json"
    started = time.monotonic()
    done = 0

    def emit(event):
        detail = dict(event) if isinstance(event, dict) else {"message": event}
        remaining = (time.monotonic() - started) / done * (spec["count"] - done) if done else None
        detail.update(progress={"completed": done, "total": spec["count"], "unit": "张已处理"},
                      eta={"remaining_seconds": remaining, "scope": "本次出图", "basis": "按本轮实际已处理耗时估算"})
        update(detail)

    try:
        emit("准备绘图模型，不调用 Qwen")
        app.model.stop()
        app.comfy.start()
        if pending:
            from .comfy_recovery import recover_batch
            emit("核对此任务上次提交的图片，避免重复生成")
            for batch in pending:
                recovered = recover_batch(app.repo, pid, batch)
                e.need(recovered["status"] not in {"running", "uncertain"},
                       "上次提交的图片仍未确认结束，已有结果保留，请稍后继续核对")
        for i in range(spec["count"]):
            if request.get("run_key"):
                previous = [b for b in comfyui.drawing_state(app.repo, pid)["batches"]
                            if b.get("render_options", {}).get("queue_run") == request["run_key"]
                            and i in b.get("candidate_indices", []) and b.get("images")]
                if previous:
                    e.need(all((app.repo.folder(pid) / "images" / image["file"]).is_file()
                               for b in previous for image in b["images"]), "已保存的图片文件缺失，请核对后继续")
                    report["batches"].extend(b["id"] for b in previous)
                    done += 1
                    emit(f"复用此任务第 {i + 1} 张已保存的图片")
                    continue
            try:
                emit(f"绘制第 {i + 1}/{spec['count']} 张")
                batch = comfyui.run_batch(app.repo, pid, p.get("board_target", "P001-K01"), emit, spec["checkpoint"], 1,
                    "seeds", loras, candidate_indices=[i], review_required=False,
                    **({"board_sample": p} if p.get("board_target") else {}),
                    render_options={"diagnostic": "small_batch", "direct_prompt": True,
                                    "aspect_ratio": spec["aspect_ratio"], "resolution": spec.get("resolution", "default"),
                                    "consistency_lora": spec.get("consistency_lora", comfyui.config().get("consistency_lora", False)),
                                    "delta_matte_skin": spec.get("delta_matte_skin", comfyui.config().get("delta_matte_skin", True)),
                                    "reference": spec.get("use_reference", False),
                                    "reference_weight": spec.get("reference_weight", 0.7),
                                    "hand_detail": spec.get("hand_detail", comfyui.config().get("hand_detail", False)),
                                    "control": False, "sample_job": jid, "queue_run": request.get("run_key")})
                report["batches"].append(batch["id"])
            except Exception as exc:
                report["errors"].append({"number": i + 1, "error": str(exc)})
                if any(b.get("status") == "uncertain" for b in comfyui.drawing_state(app.repo, pid)["batches"]):
                    raise
            done += 1
            e.save(output, report)
            emit(f"已处理 {done}/{spec['count']} 张")
        report["status"] = "done_with_errors" if report["errors"] else "done"
        report["completion_message"] = f"出图结束：成功 {len(report['batches'])} 张，失败 {len(report['errors'])} 张。"
        return report
    except Exception:
        report["status"] = "interrupted"
        raise
    finally:
        e.save(output, report)
        app.comfy.stop()
