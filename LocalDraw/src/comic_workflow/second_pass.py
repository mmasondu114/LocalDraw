"""Second-pass (Hires / cross-model refine) pipeline with full & tiled modes."""

import copy
import hashlib
import io
import math
import secrets
import shutil
import time
import urllib.parse
import urllib.request
import uuid
from PIL import Image

from . import comfyui, engine as e, prompt_models
from .installed_assets import models as model_directory
from .lora_folders import matches as lora_matches_checkpoint
from .paths import DATA, ROOT
from .quick_sample import selected_loras
from .repository import LOCK
from .telemetry import ComfyProgress, emit

UPSCALE_MODEL = "RealESRGAN_x4plus_anime_6B.pth"
ALLOWED_SCALES = (1.0, 1.25, 1.4, 1.5, 2.0)
ALLOWED_MODES = ("auto", "full", "tiled")
ALLOWED_METHODS = ("latent", "upscale")
ALLOWED_POST_UPSCALES = (1.0, 1.5, 2.0)
VAE_TILED_THRESHOLD = 5_000_000


def default_params(checkpoint, method="latent"):
    """Per-model second-pass defaults, full-pass comfort limit, and tile size."""
    family = prompt_models.MODELS.get(checkpoint, (None, None))[1]
    latent = method == "latent"
    if family == "krea2":
        return {"denoise": 0.5 if latent else 0.3, "steps": 8, "comfort_pixels": 4_800_000, "tile_size": 1280}
    if family == "moss_image":
        return {"denoise": 0.45 if latent else 0.3, "steps": 20 if latent else 16, "comfort_pixels": 2_600_000, "tile_size": 1280}
    if family in ("anima", "anima38", "delta"):
        return {"denoise": 0.45 if latent else 0.35, "steps": 24, "comfort_pixels": 2_400_000, "tile_size": 1024}
    return {"denoise": 0.45 if latent else 0.35, "steps": 20, "comfort_pixels": 2_000_000, "tile_size": 1024}


def find_image(index, image_id):
    for batch in index.get("batches", []):
        for image in batch.get("images", []):
            if image.get("id") == image_id:
                return batch, image
    return None, None


def resolve_base_id(index, image_id):
    batch, image = find_image(index, image_id)
    e.need(image is not None, "原图不存在，请刷新后重试")
    if batch.get("kind") == "second_pass" and batch.get("source_image_id"):
        return batch["source_image_id"]
    return image_id


def alignment_for(checkpoint):
    family = prompt_models.MODELS.get(checkpoint, (None, None))[1]
    return 16 if family == "moss_image" else 8


def target_dimensions(width, height, scale, checkpoint):
    alignment = alignment_for(checkpoint)
    tw = max(alignment, int(math.floor(width * scale / alignment + 0.5)) * alignment)
    th = max(alignment, int(math.floor(height * scale / alignment + 0.5)) * alignment)
    e.need(tw <= 4096 and th <= 4096, "缩放后的分辨率超过 4096 像素上限，请降低缩放倍率")
    return tw, th


def resolve_mode(requested_mode, tw, th, checkpoint):
    defaults = default_params(checkpoint)
    tile = defaults["tile_size"]
    if requested_mode == "full":
        return "full"
    if requested_mode == "tiled":
        return "tiled" if (tw > tile or th > tile) else "full"
    if tw * th > defaults["comfort_pixels"] and (tw > tile or th > tile):
        return "tiled"
    return "full"


def _axis_positions(length, tile, overlap, alignment):
    if length <= tile:
        return [(0, length)]
    step = max(alignment, tile - overlap)
    count = math.ceil((length - overlap) / step)
    if count <= 1:
        return [(0, length)]
    span = length - tile
    positions = []
    for i in range(count):
        if i == 0:
            start = 0
        elif i == count - 1:
            start = span
        else:
            start = int(round(span * i / (count - 1) / alignment)) * alignment
            start = max(0, min(span, start))
        positions.append((start, start + tile))
    return positions


def compute_tiles(tw, th, tile_size, overlap=192, alignment=8):
    """Return a 2D grid of overlapping (x0, y0, x1, y1) tiles covering (tw, th)."""
    tx = min(tw, (tile_size // alignment) * alignment)
    ty = min(th, (tile_size // alignment) * alignment)
    ov_x = min(overlap, max(alignment, (tx // 4 // alignment) * alignment))
    ov_y = min(overlap, max(alignment, (ty // 4 // alignment) * alignment))
    xs = _axis_positions(tw, tx, ov_x, alignment)
    ys = _axis_positions(th, ty, ov_y, alignment)
    return [[(x0, y0, x1, y1) for (x0, x1) in xs] for (y0, y1) in ys]


def _cosine_ramp(length, total):
    """1D cosine ramp of length `total` ramping 0..255 over the first `length` pixels."""
    ramp = bytearray(total)
    fade = max(1, min(length, total))
    for i in range(fade):
        ramp[i] = int(round(255.0 * 0.5 * (1.0 - math.cos(math.pi * (i + 0.5) / fade))))
    for i in range(fade, total):
        ramp[i] = 255
    return bytes(ramp)


def blend_tiles(canvas_size, tile_rows):
    """Seamlessly blend a 2D list of ((x0, y0, x1, y1), PIL.Image) using separable cosine feathering."""
    tw, th = canvas_size
    out = Image.new("RGB", (tw, th))
    prev_y1 = 0
    for row_idx, row in enumerate(tile_rows):
        _, y0, _, y1 = row[0][0]
        row_h = y1 - y0
        strip = Image.new("RGB", (tw, row_h))
        prev_x1 = 0
        for col_idx, ((x0, _, x1, _), tile_im) in enumerate(row):
            tile_w = x1 - x0
            im = tile_im.convert("RGB")
            if im.size != (tile_w, row_h):
                im = im.resize((tile_w, row_h), Image.Resampling.LANCZOS)
            if col_idx == 0 or prev_x1 <= x0:
                strip.paste(im, (x0, 0))
            else:
                ov_x = prev_x1 - x0
                mask_1d = Image.frombytes("L", (tile_w, 1), _cosine_ramp(ov_x, tile_w))
                mask = mask_1d.resize((tile_w, row_h), Image.Resampling.NEAREST)
                strip.paste(im, (x0, 0), mask)
            prev_x1 = x1
        if row_idx == 0 or prev_y1 <= y0:
            out.paste(strip, (0, y0))
        else:
            ov_y = prev_y1 - y0
            mask_1d = Image.frombytes("L", (1, row_h), _cosine_ramp(ov_y, row_h))
            mask = mask_1d.resize((tw, row_h), Image.Resampling.NEAREST)
            out.paste(strip, (0, y0), mask)
        prev_y1 = y1
    return out


def build_upscale_graph(source_rel_path, tw, th, transient=False):
    """Build a lightweight upscale-only ComfyUI graph using RealESRGAN + lanczos."""
    save_node = (
        {"class_type": "PreviewImage", "inputs": {"images": ["53", 0]}}
        if transient
        else {"class_type": "SaveImage", "inputs": {"images": ["53", 0], "filename_prefix": "comic-studio/sp-up"}}
    )
    return {
        "50": {"class_type": "LoadImage", "inputs": {"image": source_rel_path}},
        "51": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": UPSCALE_MODEL}},
        "52": {"class_type": "ImageUpscaleWithModel",
               "inputs": {"upscale_model": ["51", 0], "image": ["50", 0]}},
        "53": {"class_type": "ImageScale",
               "inputs": {"image": ["52", 0], "upscale_method": "lanczos",
                          "width": tw, "height": th, "crop": "disabled"}},
        "55": save_node,
    }


def post_upscale_dimensions(width, height, post_upscale):
    if post_upscale <= 1.0:
        return width, height
    ow = max(2, int(round(width * post_upscale / 2)) * 2)
    oh = max(2, int(round(height * post_upscale / 2)) * 2)
    e.need(ow <= 6144 and oh <= 6144, "二采后超分尺寸超过 6144 像素上限，请降低倍率")
    return ow, oh


def build_graph(checkpoint, positive, negative, seed, cfg, loras, source_rel_path,
                orig_size, scale, denoise, *, has_upscaler=False, method="latent", post_upscale=1.0):
    """Build a second-pass ComfyUI workflow (latent bicubic detail-regen or pixel upscale + optional post-upscale)."""
    family = prompt_models.MODELS.get(checkpoint, (None, None))[1]
    e.need(family != "qwen_edit", "Qwen 编辑模型暂不支持作为二采底模")
    tw, th = target_dimensions(orig_size[0], orig_size[1], scale, checkpoint)
    run_cfg = {**cfg, "width": tw, "height": th}
    workflow = comfyui.graph(checkpoint, positive, negative, seed, run_cfg, loras)
    workflow.pop("4", None)
    vae_ref = workflow["6"]["inputs"]["vae"]
    workflow["50"] = {"class_type": "LoadImage", "inputs": {"image": source_rel_path}}
    if method == "latent":
        workflow["54"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["50", 0], "vae": vae_ref}}
        if scale > 1.0:
            workflow["55"] = {
                "class_type": "LatentUpscaleBy",
                "inputs": {"samples": ["54", 0], "upscale_method": "bicubic", "scale_by": float(scale)},
            }
            latent_ref = ["55", 0]
        else:
            latent_ref = ["54", 0]
    else:
        pixels_ref = ["50", 0]
        if scale >= 1.5 and has_upscaler:
            workflow["51"] = {"class_type": "UpscaleModelLoader", "inputs": {"model_name": UPSCALE_MODEL}}
            workflow["52"] = {"class_type": "ImageUpscaleWithModel",
                              "inputs": {"upscale_model": ["51", 0], "image": ["50", 0]}}
            workflow["53"] = {"class_type": "ImageScale",
                              "inputs": {"image": ["52", 0], "upscale_method": "lanczos",
                                         "width": tw, "height": th, "crop": "disabled"}}
            pixels_ref = ["53", 0]
        elif (tw, th) != tuple(orig_size):
            workflow["53"] = {"class_type": "ImageScale",
                              "inputs": {"image": ["50", 0], "upscale_method": "lanczos",
                                         "width": tw, "height": th, "crop": "disabled"}}
            pixels_ref = ["53", 0]
        workflow["54"] = {"class_type": "VAEEncode", "inputs": {"pixels": pixels_ref, "vae": vae_ref}}
        latent_ref = ["54", 0]
    workflow["5"]["inputs"]["latent_image"] = latent_ref
    workflow["5"]["inputs"]["denoise"] = denoise
    workflow["5"]["inputs"]["steps"] = run_cfg["steps"]
    # Keep native VAEDecode up to 5.0MP so qwen_image_vae avoids small-tile grid artifacts.
    if tw * th >= VAE_TILED_THRESHOLD:
        workflow["6"] = {
            "class_type": "VAEDecodeTiled",
            "inputs": {
                "samples": ["5", 0],
                "vae": vae_ref,
                "tile_size": 1024,
                "overlap": 256,
                "temporal_size": 64,
                "temporal_overlap": 8,
            },
        }
    out_w, out_h = post_upscale_dimensions(tw, th, post_upscale)
    if post_upscale > 1.0:
        if has_upscaler:
            workflow["61"] = {"class_type": "UpscaleModelLoader", "inputs": {"model_name": UPSCALE_MODEL}}
            workflow["62"] = {"class_type": "ImageUpscaleWithModel",
                              "inputs": {"upscale_model": ["61", 0], "image": ["6", 0]}}
            post_src = ["62", 0]
        else:
            post_src = ["6", 0]
        workflow["63"] = {
            "class_type": "ImageScale",
            "inputs": {"image": post_src, "upscale_method": "lanczos",
                       "width": out_w, "height": out_h, "crop": "disabled"},
        }
        workflow["7"]["inputs"]["images"] = ["63", 0]
    return workflow, (out_w, out_h)


def validate_request(repo, pid, request):
    p = repo.get(pid)
    index = comfyui.drawing_state(repo, pid)
    source_id = request.get("source_image_id")
    e.need(isinstance(source_id, str) and source_id, "请选择要二采的图片")
    base_id = resolve_base_id(index, source_id)
    base_batch, base_image = find_image(index, base_id)
    e.need(base_image is not None, "找不到一采原图，请刷新后重试")
    source_path = (repo.folder(pid) / "images" / base_image["file"]).resolve()
    image_root = (repo.folder(pid) / "images").resolve()
    e.need(source_path.is_relative_to(image_root) and source_path.is_file(), "原图文件已缺失")

    discovery = comfyui.discover(probe=False)
    checkpoint = request.get("checkpoint") or base_batch.get("checkpoint") or comfyui.config()["checkpoint"]
    e.need(checkpoint in discovery["checkpoints"], "选定的二采绘图模型未安装")
    family = prompt_models.MODELS.get(checkpoint, (None, None))[1]
    e.need(family != "qwen_edit", "Qwen 编辑模型暂不支持作为二采底模")

    scale = request.get("scale", 1.5)
    e.need(type(scale) in (int, float) and float(scale) in ALLOWED_SCALES, "缩放倍率仅支持 1.0× / 1.25× / 1.4× / 1.5× / 2.0×")
    scale = float(scale)

    mode = request.get("mode", "auto")
    e.need(mode in ALLOWED_MODES, "二采模式无效（仅支持 auto / full / tiled）")

    method = request.get("method", "latent")
    e.need(method in ALLOWED_METHODS, "二采方式无效（仅支持 latent / upscale）")

    post_upscale = request.get("post_upscale", 1.0)
    e.need(type(post_upscale) in (int, float) and float(post_upscale) in ALLOWED_POST_UPSCALES,
           "二采后超分仅支持 关闭 / 1.5× / 2.0×")
    post_upscale = float(post_upscale)

    defaults = default_params(checkpoint, method)
    denoise = request.get("denoise", defaults["denoise"])
    e.need(type(denoise) in (int, float) and 0.05 <= float(denoise) <= 1.0, "重绘幅度须为 0.05–1.0")
    denoise = round(float(denoise), 3)

    steps = request.get("steps")
    if steps is None or steps == "":
        steps = defaults["steps"]
    else:
        e.need(type(steps) is int and 1 <= steps <= 100, "采样步数须为 1–100 的整数")

    count = request.get("count", 1)
    e.need(type(count) is int and 1 <= count <= 8, "二采张数须为 1–8 张")

    seed = request.get("seed")
    if seed is not None:
        e.need(type(seed) is int and 0 <= seed < 2**53, "种子须为 0 至 9007199254740991 的整数")

    positive = request.get("positive")
    if positive is None:
        positive = base_image.get("positive", "")
    e.need(isinstance(positive, str) and 1 <= len(positive.strip()) <= 24000, "正向提示词无效")

    negative = request.get("negative")
    if negative is None:
        negative = base_image.get("negative", "")
    e.need(isinstance(negative, str) and len(negative) <= 12000, "负面提示词过长")

    raw_loras = request.get("loras")
    if raw_loras is None:
        raw_loras = [
            {"file": l["file"], "weight": l.get("weight", 0.7)}
            for l in base_batch.get("loras", [])
            if lora_matches_checkpoint(comfyui.lora_file_key(l.get("file", "")), checkpoint)
        ]
    loras = selected_loras(raw_loras, checkpoint, discovery)

    with Image.open(source_path) as im:
        orig_size = im.size
    tw, th = target_dimensions(orig_size[0], orig_size[1], scale, checkpoint)
    out_w, out_h = post_upscale_dimensions(tw, th, post_upscale)
    effective_mode = resolve_mode(mode, tw, th, checkpoint)

    return {
        "project": p,
        "base_id": base_id,
        "base_batch": base_batch,
        "base_image": base_image,
        "source_path": source_path,
        "orig_size": orig_size,
        "target_size": (tw, th),
        "output_size": (out_w, out_h),
        "checkpoint": checkpoint,
        "scale": scale,
        "mode": mode,
        "method": method,
        "post_upscale": post_upscale,
        "effective_mode": effective_mode,
        "denoise": denoise,
        "steps": steps,
        "count": count,
        "seed": seed,
        "positive": positive,
        "negative": negative,
        "loras": loras,
        "delta_matte_skin": bool(request.get("delta_matte_skin", False)),
        "panel_id": base_batch.get("panel_id", "P001-K01"),
        "panel_hash": base_batch.get("panel_hash", ""),
    }


def _sync_board_selection(index, panel_id, old_image_id, new_image_id, new_batch_id, new_image):
    sel = index.get("selections", {}).get(panel_id)
    if sel and sel.get("image", {}).get("id") == old_image_id:
        sel["image"] = copy.deepcopy(new_image)
        sel["batch"] = new_batch_id


def apply_result(repo, pid, base_image_id, candidate_image_id):
    with LOCK:
        repo.get(pid)
        index = comfyui.drawing_state(repo, pid)
        base_batch, base_image = find_image(index, base_image_id)
        e.need(base_image is not None, "一采原图不存在")
        cand_batch, cand_image = find_image(index, candidate_image_id)
        e.need(
            cand_image is not None
            and cand_batch.get("kind") == "second_pass"
            and cand_batch.get("source_image_id") == base_image_id,
            "二采候选图不存在或与当前原图不匹配",
        )
        applied = index.setdefault("second_pass_applied", {})
        previous_id = applied.get(base_image_id, base_image_id)
        applied[base_image_id] = candidate_image_id
        _sync_board_selection(index, base_batch.get("panel_id"), previous_id,
                              candidate_image_id, cand_batch["id"], cand_image)
        e.save(repo.folder(pid) / "images/index.json", index)
        return index


def unapply_result(repo, pid, base_image_id):
    with LOCK:
        repo.get(pid)
        index = comfyui.drawing_state(repo, pid)
        base_batch, base_image = find_image(index, base_image_id)
        e.need(base_image is not None, "一采原图不存在")
        applied = index.setdefault("second_pass_applied", {})
        previous_id = applied.pop(base_image_id, None)
        if previous_id:
            _sync_board_selection(index, base_batch.get("panel_id"), previous_id,
                                  base_image_id, base_batch["id"], base_image)
        e.save(repo.folder(pid) / "images/index.json", index)
        return index


def _rewrite_workflow_for_run(workflow, lora_files, transient):
    for node in workflow.values():
        if node["class_type"] in ("LoraLoader", "DeltaMixQ35LoraLoader"):
            inputs = node["inputs"]
            inputs["lora_name"] = lora_files[comfyui.lora_file_key(inputs["lora_name"])]
    if transient:
        for node in workflow.values():
            if node["class_type"] == "SaveImage":
                node["class_type"] = "PreviewImage"
                node["inputs"].pop("filename_prefix", None)
    return workflow


def _submit_and_fetch(workflow, cfg, bid, update, batch, persist, detail, transient):
    watcher = ComfyProgress(cfg["endpoint"], bid)
    job = None
    record = None
    try:
        emit(update, "提交二采任务", "ComfyUI", detail=detail)
        batch["submission_pending"] = True
        persist()
        response = comfyui.api("/prompt", {"prompt": workflow, "client_id": bid})
        batch["submission_pending"] = False
        e.need(not response.get("node_errors"), "ComfyUI 节点校验失败：" + e.encoded(response.get("node_errors")))
        job = response["prompt_id"]
        batch["active_prompt"] = job
        persist()

        deadline = time.monotonic() + cfg["timeout_seconds"]
        last_history = 0
        while time.monotonic() < deadline:
            event = watcher.poll(job, workflow)
            if event:
                update({"runtime": event})
            if time.monotonic() - last_history >= 1:
                last_history = time.monotonic()
                record = comfyui.api("/history/" + job).get(job)
                if record:
                    break
        e.need(record, "ComfyUI 二采超时；请检查队列")
        e.need(record.get("status", {}).get("status_str") != "error", "ComfyUI 二采执行失败")
        images = [im for out in record.get("outputs", {}).values() for im in out.get("images", [])]
        e.need(images, "ComfyUI 未返回二采图片")
        query = urllib.parse.urlencode(images[0])
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(cfg["endpoint"] + "/view?" + query, timeout=30) as resp:
            content = resp.read(80_000_001)
        e.need(len(content) <= 80_000_000, "返回图片过大")
        return job, content
    finally:
        if transient and job and record:
            comfyui.cleanup_prompt_output(job, record)
        watcher.close()


def run(app, pid, request, update, jid):
    from .task_control import checkpoint as task_checkpoint, TaskCancelled
    spec = validate_request(app.repo, pid, request)
    p = spec["project"]
    checkpoint = spec["checkpoint"]
    tw, th = spec["target_size"]
    out_w, out_h = spec["output_size"]
    post_upscale = spec["post_upscale"]
    effective_mode = spec["effective_mode"]
    defaults = default_params(checkpoint)
    cfg = comfyui.config()
    cfg.update(prompt_models.defaults(checkpoint))
    cfg["steps"] = spec["steps"]
    cfg["literal_prompt"] = True
    cfg["delta_matte_skin"] = spec["delta_matte_skin"]
    cfg["transient"] = bool(p.get("transient"))
    family = prompt_models.MODELS.get(checkpoint, (None, None))[1]
    if family == "krea2" and spec["negative"].strip():
        cfg["cfg"] = 1.2

    src_bytes = spec["source_path"].read_bytes()
    digest = hashlib.sha256(src_bytes).hexdigest()[:24]
    comfy_input = DATA / "comfy-input/second-pass" / f"{digest}.png"
    comfy_input.parent.mkdir(parents=True, exist_ok=True)
    if not comfy_input.exists():
        shutil.copyfile(spec["source_path"], comfy_input)
    source_rel = "second-pass/" + comfy_input.name
    has_upscaler = (model_directory(ROOT) / "upscale_models" / UPSCALE_MODEL).is_file()
    transient = bool(p.get("transient"))

    bid = uuid.uuid4().hex
    folder = app.repo.folder(pid) / "images" / bid
    folder.mkdir(parents=True, exist_ok=True)
    tile_grid = (
        compute_tiles(tw, th, defaults["tile_size"], overlap=192, alignment=alignment_for(checkpoint))
        if effective_mode == "tiled"
        else None
    )
    tile_total = sum(len(row) for row in tile_grid) if tile_grid else 0
    batch = {
        "id": bid,
        "kind": "second_pass",
        "source_image_id": spec["base_id"],
        "panel_id": spec["panel_id"],
        "panel_hash": spec["panel_hash"],
        "checkpoint": checkpoint,
        "created": e.stamp(),
        "status": "running",
        "images": [],
        "source": "ComfyUI 二次采样",
        "settings": cfg,
        "count": spec["count"],
        "loras": spec["loras"],
        "second_pass": {
            "source_image_id": spec["base_id"],
            "scale": spec["scale"],
            "mode": effective_mode,
            "method": spec["method"],
            "post_upscale": post_upscale,
            "tiles": tile_total,
            "denoise": spec["denoise"],
            "steps": spec["steps"],
            "width": out_w,
            "height": out_h,
            "upscaler": UPSCALE_MODEL if (((spec["method"] == "upscale" and spec["scale"] >= 1.5) or post_upscale > 1.0) and has_upscaler) else None,
        },
        "render_options": {"direct_prompt": True, "second_pass": True, "sample_job": jid},
    }

    def persist():
        with LOCK:
            index = comfyui.drawing_state(app.repo, pid)
            index["batches"] = [b for b in index["batches"] if b["id"] != bid] + [copy.deepcopy(batch)]
            e.save(app.repo.folder(pid) / "images/index.json", index)

    tile_input_dir = DATA / "comfy-input/second-pass" / f"tiles-{bid[:12]}"
    started = time.monotonic()
    try:
        update("准备二采绘图模型")
        app.model.stop()
        app.comfy.start()
        status = comfyui.discover()
        e.need(status["ready"], "ComfyUI 尚未启动")
        lora_files = status.get("lora_files") or {
            comfyui.lora_file_key(name): name for name in status.get("loras", [])
        }
        e.need(checkpoint in status["checkpoints"], "选定二采模型未在 ComfyUI 中就绪")
        for lora in spec["loras"]:
            e.need(lora["file"] in lora_files, "LoRA 文件不在 ComfyUI 可用列表中：" + lora["file"])
        persist()

        # Prepare upscaled base canvas once when running tiled mode.
        tiled_canvas = None
        if effective_mode == "tiled":
            task_checkpoint()
            if spec["method"] == "upscale" and spec["scale"] >= 1.5 and has_upscaler:
                update(f"正在超分准备分块画布（{tw}×{th}）")
                up_wf = build_upscale_graph(source_rel, tw, th, transient=transient)
                _, up_bytes = _submit_and_fetch(
                    up_wf, cfg, bid, update, batch, persist,
                    f"超分预处理 · {tw}×{th}", transient,
                )
                with Image.open(io.BytesIO(up_bytes)) as im:
                    tiled_canvas = im.convert("RGB")
            else:
                resample = Image.Resampling.BICUBIC if spec["method"] == "latent" else Image.Resampling.LANCZOS
                with Image.open(spec["source_path"]) as im:
                    tiled_canvas = im.convert("RGB").resize((tw, th), resample)
            tile_input_dir.mkdir(parents=True, exist_ok=True)

        for i in range(spec["count"]):
            task_checkpoint()
            seed = (spec["seed"] + i) % 2**53 if spec["seed"] is not None else secrets.randbelow(2**53)
            name = f"{i + 1}.png"
            out_path = folder / name
            if effective_mode == "tiled":
                rendered_rows = []
                done_tiles = 0
                last_job = None
                for r_idx, row in enumerate(tile_grid):
                    rendered_row = []
                    for c_idx, box in enumerate(row):
                        task_checkpoint()
                        x0, y0, x1, y1 = box
                        crop = tiled_canvas.crop(box)
                        tile_file = tile_input_dir / f"r{r_idx}c{c_idx}.png"
                        if not tile_file.exists():
                            crop.save(tile_file, format="PNG")
                        tile_rel = f"second-pass/{tile_input_dir.name}/{tile_file.name}"
                        update({
                            "message": f"分块二采第 {i + 1}/{spec['count']} 张（区块 {done_tiles + 1}/{tile_total}）",
                            "progress": {
                                "completed": i * tile_total + done_tiles,
                                "total": spec["count"] * tile_total,
                                "unit": "块（分块二采）",
                            },
                        })
                        tile_wf, _ = build_graph(
                            checkpoint, spec["positive"], spec["negative"], seed, cfg, spec["loras"],
                            tile_rel, (x1 - x0, y1 - y0), 1.0, spec["denoise"], has_upscaler=False,
                        )
                        _rewrite_workflow_for_run(tile_wf, lora_files, transient)
                        if r_idx == 0 and c_idx == 0:
                            e.save(folder / f"workflow-{i + 1}.json", tile_wf)
                        last_job, tile_bytes = _submit_and_fetch(
                            tile_wf, cfg, bid, update, batch, persist,
                            f"第 {i + 1}/{spec['count']} 张 · 区块 {done_tiles + 1}/{tile_total}（{x1 - x0}×{y1 - y0}）",
                            transient,
                        )
                        with Image.open(io.BytesIO(tile_bytes)) as t_im:
                            rendered_row.append((box, t_im.convert("RGB")))
                        done_tiles += 1
                    rendered_rows.append(rendered_row)
                merged = blend_tiles((tw, th), rendered_rows)
                if post_upscale > 1.0:
                    if has_upscaler:
                        post_in = tile_input_dir / f"merged-{i + 1}.png"
                        merged.save(post_in, format="PNG")
                        post_wf = build_upscale_graph(f"second-pass/{tile_input_dir.name}/{post_in.name}", out_w, out_h, transient=transient)
                        update(f"正在二采后超分第 {i + 1}/{spec['count']} 张（{out_w}×{out_h}）")
                        _, post_bytes = _submit_and_fetch(
                            post_wf, cfg, bid, update, batch, persist,
                            f"二采后超分 · {out_w}×{out_h}", transient,
                        )
                        out_path.write_bytes(post_bytes)
                    else:
                        merged.resize((out_w, out_h), Image.Resampling.LANCZOS).save(out_path, format="PNG")
                else:
                    merged.save(out_path, format="PNG")
                job = last_job
            else:
                remaining = (time.monotonic() - started) / i * (spec["count"] - i) if i else None
                update({
                    "message": f"二次采样第 {i + 1}/{spec['count']} 张",
                    "progress": {"completed": i, "total": spec["count"], "unit": "张（二采）"},
                    "eta": {"remaining_seconds": remaining, "scope": "二次采样", "basis": "本轮实测耗时"},
                })
                workflow, (out_w, out_h) = build_graph(
                    checkpoint, spec["positive"], spec["negative"], seed, cfg, spec["loras"],
                    source_rel, spec["orig_size"], spec["scale"], spec["denoise"],
                    has_upscaler=has_upscaler, method=spec["method"], post_upscale=post_upscale,
                )
                _rewrite_workflow_for_run(workflow, lora_files, transient)
                e.save(folder / f"workflow-{i + 1}.json", workflow)
                job, content = _submit_and_fetch(
                    workflow, cfg, bid, update, batch, persist,
                    f"第 {i + 1}/{spec['count']} 张 · {out_w}×{out_h}", transient,
                )
                out_path.write_bytes(content)
                with Image.open(out_path) as img:
                    img.verify()
                if transient:
                    with Image.open(out_path) as img:
                        clean = img.copy()
                        clean.info.clear()
                    clean.save(out_path)

            batch["images"].append({
                "id": f"{bid}-{i + 1}",
                "file": f"{bid}/{name}",
                "seed": seed,
                "prompt_id": job,
                "positive": spec["positive"],
                "negative": spec["negative"],
                "width": out_w,
                "height": out_h,
                "review": {"verdict": "unreviewed"},
            })
            persist()
            update({
                "message": f"已完成二采 {i + 1}/{spec['count']} 张",
                "progress": {"completed": i + 1, "total": spec["count"], "unit": "张（二采）"},
            })

        batch["status"] = "done"
        persist()
        auto_applied = None
        if spec["count"] == 1 and len(batch["images"]) == 1:
            auto_applied = batch["images"][0]["id"]
            apply_result(app.repo, pid, spec["base_id"], auto_applied)
        return {
            "batch": bid,
            "source_image_id": spec["base_id"],
            "mode": effective_mode,
            "method": spec["method"],
            "tiles": tile_total,
            "images": [im["id"] for im in batch["images"]],
            "auto_applied": auto_applied,
        }
    except TaskCancelled:
        batch.update(status="cancelled", submission_pending=False)
        persist()
        raise
    except Exception as exc:
        batch.update(status="failed", error=str(exc), submission_pending=False)
        persist()
        raise
    finally:
        if tile_input_dir.exists():
            shutil.rmtree(tile_input_dir, ignore_errors=True)
        app.comfy.stop()
