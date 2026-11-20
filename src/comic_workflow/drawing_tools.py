"""Small bounded comparisons and explicit masked edits, with history preserved."""

import shutil
import copy
import time
from PIL import Image
from . import engine as e, comfyui, creative, drawing_settings
from .character_binding import panel_characters
from .framing import source_text
from .paths import DATA
from .reviewed_drawing import record_review
from .model import generate
from .batch_drawing import story_fingerprint


def small_batch(app, pid, request, update, jid):
    """Bounded manual comparison: adapt once, render together, no automatic retries."""
    from .character_binding import identity_hash
    from .comparison_models import available

    p = app.repo.get(pid)
    e.need(e.digest(p) == request["base_hash"], "作品已变化，请刷新")
    target = request.get("target")
    panel = next((x for page in p["pages"].values() for x in page["panels"] if x["id"] == target), None)
    e.need(panel, "请先确认要试画的文字分镜")
    checkpoints = request.get("checkpoints")
    e.need(isinstance(checkpoints, list) and 1 <= len(checkpoints) <= 3
           and all(isinstance(c, str) for c in checkpoints)
           and len(set(checkpoints)) == len(checkpoints), "一次请选择 1–3 个不同模型")
    count = request.get("count", 2)
    e.need(type(count) is int and 1 <= count <= 4, "每个模型试画 1–4 张")
    seed = request.get("seed", 2026091901)
    e.need(type(seed) is int and 0 <= seed < 2**53 - 4, "种子须为有效非负整数")
    installed = comfyui.discover()["checkpoints"]
    known = {m["name"]: m for m in available()}
    for checkpoint in checkpoints:
        e.need(checkpoint in installed and (checkpoint not in known or known[checkpoint]["installed"]),
               checkpoint + " 尚未下载完整，请运行下载 BAT 后刷新")
    e.need(not any(b.get("status") in ("running", "uncertain") for b in comfyui.drawing_state(app.repo, pid)["batches"]),
           "还有绘图任务未结束或状态待核实，请先检查队列")
    prefs = drawing_settings.load(app.repo, pid)
    loras = drawing_settings.loras_for_panel(prefs, p, panel) if request.get("use_loras") is True and prefs else []
    fingerprint = story_fingerprint(p)
    total = len(checkpoints) * count
    done = 0
    started = time.monotonic()
    report = {"purpose": "单张 / 少量同种子试画，未自动审核，不自动补画或选图", "target": target,
              "count_per_model": count, "seed": seed, "batches": [], "errors": [], "status": "running"}
    output = app.repo.folder(pid) / "runs" / jid / "small-batch.json"

    def progress(event):
        event = dict(event) if isinstance(event, dict) else {"message": event}
        event["progress"] = {"completed": done, "total": total, "unit": "张试画（含已报告失败）"}
        # Whole-job ETA is estimated from finished attempts; sampler telemetry remains intact.
        if done:
            event["progress"]["eta_seconds"] = max(0, (time.monotonic() - started) / done * (total - done))
        event["eta"] = {"remaining_seconds": event["progress"].get("eta_seconds"),
                        "scope": "少量试画预计", "basis": "按本轮实际耗时估算；切换底模的加载耗时可能不同"}
        update(event)

    try:
        prompt = p.get("drawing_prompts", {}).get(target)
        e.need(prompt and prompt.get("panel_hash") == e.digest(panel)
               and prompt.get("identity_hash") == identity_hash(p, panel)
               and prompt.get("payload", {}).get("positive", "").strip(),
               "缺少当前画格的有效提示词，请先核对并保存；不会自动改写")
        literal = {"panel_id":target, "panel_hash":e.digest(panel),
                   "positive":prompt["payload"]["positive"], "negative":prompt["payload"].get("negative", "")}
        app.model.stop()
        progress("启动绘图服务，接下来连续试画所有选定模型")
        app.comfy.start()
        started = time.monotonic()
        for checkpoint in checkpoints:
            experimental = copy.deepcopy(loras)
            for lora in experimental:
                if lora.get("compatible_checkpoint") != checkpoint:
                    lora["experimental_checkpoint"] = checkpoint
                    lora["confirmed_compatibility"] = False
            for i in range(count):
                e.need(story_fingerprint(app.repo.get(pid)) == fingerprint, "原文或分镜已变化，停止试画")
                progress(f"试画 {checkpoint} · {i + 1}/{count}")
                try:
                    batch = comfyui.run_batch(app.repo, pid, target, progress, checkpoint, 1,
                        "seeds", experimental, candidate_indices=[i], review_required=False,
                        render_options={"diagnostic": "small_batch", "sample_job": jid,
                                        "seed": seed + i, "control": False, "reference": False,
                                        "external_prompt":True, "direct_prompt":True}, prompt_override=literal)
                    report["batches"].append({"batch": batch["id"], "checkpoint": checkpoint, "seed": seed + i})
                except Exception as exc:
                    report["errors"].append({"checkpoint": checkpoint, "seed": seed + i, "error": str(exc)})
                    e.save(output, report)
                    # Lost submission acknowledgement must not duplicate an in-flight render.
                    if any(b.get("status") == "uncertain" for b in comfyui.drawing_state(app.repo, pid)["batches"]):
                        raise
                done += 1
                e.save(output, report)
                progress(f"已处理 {done}/{total} 张；成功 {len(report['batches'])}，失败 {len(report['errors'])}")
        report["status"] = "done_with_errors" if report["errors"] else "done"
        report["completion_message"] = f"试画结束：成功 {len(report['batches'])} 张，失败 {len(report['errors'])} 张。"
        if report["errors"]:
            report["completion_message"] += "失败原因：" + report["errors"][0]["error"][:240]
        progress("试画结束，请在对应画格的「少量试画结果」中比较和选择")
        return report
    except Exception:
        report["status"] = "interrupted"
        raise
    finally:
        e.save(output, report)
        try:
            app.model.stop()
        finally:
            app.comfy.stop()


def prepare(app, pid, target, jid, update, drawing_checkpoint=None):
    p = app.repo.get(pid)
    if not drawing_checkpoint:
        prefs = drawing_settings.load(app.repo, pid)
        drawing_checkpoint = (prefs or {}).get("checkpoint") or comfyui.config()["checkpoint"]
    app.prepare_language(update=update)
    payload, metrics = generate(
        p,
        "drawing_prompt",
        target,
        "逐项对照原文与绑定角色资料，先写详细画面说明及必须出现的事实，再转成可画的英文标签和接触关系短语。只描述这一瞬间与镜头内可见内容，不添加剧情；保留细腻的彩色日漫线条。",
        app.repo.folder(pid) / "runs" / jid / target / "adaptation",
        update,
        drawing_checkpoint=drawing_checkpoint,
    )
    c = app.repo.propose(
        pid,
        "drawing_prompt",
        target,
        payload,
        e.digest(p),
        "用户授权修正镜头适配，原文与分镜不变",
        metrics,
    )
    return app.repo.accept(pid, c["id"])


def review_batch(app, pid, panel, batch, jid, update):
    p = app.repo.get(pid)
    app.prepare_language(vision=True, update=update)
    for image in batch["images"]:
        repair=batch.get('render_options',{}).get('local_repair',{})
        hand=repair.get('hand_guidance')
        repair_target=None
        if hand:
            owner=next(c for c in p['canon']['characters'] if c['id']==hand['owner'])
            repair_target={**hand,'character_name':owner['name'],'rect':repair['rect']}
        elif repair.get('owner'):
            owner=next(c for c in p['canon']['characters'] if c['id']==repair['owner'])
            repair_target={'kind':'local','owner':owner['id'],'character_name':owner['name'],
                          'rect':repair['rect'],
                          'instruction':repair.get('requested_instruction',repair.get('instruction',''))}
        result = creative.review_image(
            panel,
            image["positive"],
            app.repo.folder(pid) / "images" / image["file"],
            source_text(p, panel),
            panel_characters(p, panel),
            app.repo.folder(pid) / "runs" / jid / "reviews" / image["id"],
            update,
            **({'repair_target':repair_target} if repair_target else {}),
        )
        record_review(app.repo, pid, batch["id"], image["id"], result)


def pilot(app, pid, request, update, jid):
    p = app.repo.get(pid)
    e.need(e.digest(p) == request["base_hash"], "作品已变化，请刷新")
    targets = request["targets"]
    e.need(
        isinstance(targets, list)
        and 1 <= len(targets) <= 3
        and len(set(targets)) == len(targets),
        "小样一次只验证 1–3 格",
    )
    fingerprint = story_fingerprint(p)
    prefs = drawing_settings.load(app.repo, pid)
    report = {"purpose": "小批量同种子对照，不自动补画或选图", "batches": []}
    folder = app.repo.folder(pid) / "runs" / jid
    completed = 0
    total = 5 * len(targets)  # one adaptation, two images, two reviews per panel

    def update_stage(event):
        event = dict(event) if isinstance(event, dict) else {"message": event}
        event["progress"] = {
            "completed": completed,
            "total": total,
            "unit": "小样步骤（适配 / 绘图 / 审核）",
        }
        update(event)

    try:
        for target in targets:
            prepare(app, pid, target, jid, update_stage)
            completed += 1
        for target in targets:
            p = app.repo.get(pid)
            e.need(
                story_fingerprint(p) == fingerprint, "原文或已确认分镜变化，停止小样"
            )
            panel = next(
                x
                for x in p["pages"][target.split("-")[0]]["panels"]
                if x["id"] == target
            )
            app.model.stop()
            app.comfy.start()
            loras = drawing_settings.loras_for_panel(prefs, p, panel) if prefs else []
            pairs = []
            for control in (False, True):
                update_stage(
                    {
                        "message": f"小样 {target} · {'增加构图草图' if control else '仅镜头修正与低权重参考'}",
                    }
                )
                batch = comfyui.run_batch(
                    app.repo,
                    pid,
                    target,
                    update_stage,
                    prefs["checkpoint"] if prefs else None,
                    1,
                    "seeds",
                    loras,
                    review_required=True,
                    render_options={
                        "seed": 2026091803,
                        "control": control,
                        "diagnostic": "构图对照",
                    },
                )
                pairs.append(batch)
                completed += 1
                report["batches"].append(
                    {"panel": target, "batch": batch["id"], "control": control}
                )
                e.save(folder / "pilot.json", report)
            for batch in pairs:
                review_batch(app, pid, panel, batch, jid, update_stage)
                completed += 1
        update_stage(
            {
                "message": "对照图已保存，待你判断画风与剧情",
            }
        )
        return report
    finally:
        app.model.stop()
        app.comfy.stop()


def local_repair(app, pid, request, update, jid):
    p = app.repo.get(pid)
    e.need(e.digest(p) == request["base_hash"], "作品已变化，请刷新")
    found = [
        (b, i)
        for b in comfyui.drawing_state(app.repo, pid)["batches"]
        for i in b["images"]
        if i["id"] == request["image"]
    ]
    e.need(len(found) == 1, "原图不存在")
    b, image = found[0]
    source_loras = copy.deepcopy(b.get('render_options', {}).get('local_repair', {}).get('source_loras', b.get('loras', [])))
    target = b["panel_id"]
    panel = next(
        x for x in p["pages"][target.split("-")[0]]["panels"] if x["id"] == target
    )
    e.need(b["panel_hash"] == e.digest(panel), "旧版分镜图片不能用于当前局部修复")
    rect = request["rect"]
    e.need(
        isinstance(rect, list)
        and len(rect) == 4
        and all(isinstance(x, (int, float)) and 0 <= x <= 1 for x in rect),
        "请选择图片内的修复区域",
    )
    e.need(rect[0] < rect[2] and rect[1] < rect[3], "修复区域不能为空")
    instruction = str(request.get("instruction", "")).strip()
    e.need(2 <= len(instruction) <= 2000, "请简述要修正的内容")
    owner = None
    if 'owner' in request:
        from . import lora_binding
        owner = lora_binding.repair_owner(p, panel, request['owner'])
    from . import repair_masks
    outline = repair_masks.validate_outline(request.get('outline'), rect)
    hand_guidance = None
    if request.get('hand_guidance') is not None:
        from . import guided_hand
        hand_guidance = guided_hand.validate_request(request['hand_guidance'],rect,
            {c['character_id'] for c in panel['characters']})
        e.need(owner is None or owner['id'] == hand_guidance['owner'], '修复角色与手部角色不一致')
    folder = DATA / "comfy-input/repairs" / jid
    folder.mkdir(parents=True)
    shutil.copyfile(
        app.repo.folder(pid) / "images" / image["file"], folder / "original.png"
    )
    with Image.open(folder / "original.png") as original:
        w, h = original.size
    if hand_guidance:
        mask = guided_hand.edit_mask((w,h),rect,hand_guidance)
    else:
        mask = repair_masks.edit_mask((w,h), rect, outline)
    mask.save(folder / "mask.png")
    if not hand_guidance and (owner or outline):
        repair_masks.blend_mask(mask).save(folder / 'blend-mask.png')
    placement = guided_hand.prepare(folder,hand_guidance,rect) if hand_guidance else None
    try:
        if hand_guidance:
            translation={'positive':guided_hand.prompt(hand_guidance['glove'],hand_guidance['side'])}
        else:
            translation = {"positive": instruction}
        app.model.stop()
        if hand_guidance:
            app.comfy.start({'guided_hand_backend':guided_hand.settings().get('backend','impact')})
        else:
            app.comfy.start()
        options = {
            "control": False,
            "local_repair": {
                "original": f"repairs/{jid}/original.png",
                "mask": f"repairs/{jid}/mask.png",
                "instruction": translation["positive"],
                "requested_instruction": instruction,
                "source_image": image["id"],
                "rect": rect,
                "source_loras": source_loras,
            },
        }
        if hand_guidance:
            options['reference']=False
            options['local_repair'].update(hand_guidance=hand_guidance,placement=placement,
                depth=f'repairs/{jid}/hand-depth.png',noise_mask=f'repairs/{jid}/hand-noise-mask.png',
                source_art_preset_hash=b.get('art_preset_hash'))
        elif owner:
            options['reference'] = False
            options['local_repair'].update(owner=owner['id'],
                source_art_preset_hash=b.get('art_preset_hash'))
        if not hand_guidance and (owner or outline):
            options['local_repair'].update(blend_mask=f'repairs/{jid}/blend-mask.png', outline=outline)
        batch = comfyui.run_batch(
            app.repo,
            pid,
            target,
            update,
            b["checkpoint"],
            1,
            "seeds",
            [] if hand_guidance else source_loras,
            review_required=False,
            render_options=options,
        )
        return {
            "batch": batch["id"],
            "source_image": image["id"],
            "note": "框外保留原始像素；新图由你核对，原图保留",
        }
    finally:
        app.model.stop()
        app.comfy.stop()
