"""Bounded automatic hand detection and local detail passes for future renders."""

import hashlib
import math
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter
from . import engine as e
from .paths import ROOT, DATA, local_path


def config():
    path = ROOT / "config/hand-detail.json"
    return e.read(path) if path.exists() else {}


def capability():
    settings = config()
    detector = local_path(settings.get("detector", "models/hand_detection/hand_yolov8n.pt"))
    comfy = e.read(ROOT / "config/comfyui.json")
    python = local_path(comfy["python_path"])
    installed = python.parent.parent / "Lib/site-packages/ultralytics/__init__.py"
    return {"enabled": comfy.get("hand_detail", False),
            "ready": detector.is_file() and python.is_file() and installed.is_file(),
            "detector": detector.name, "max_regions": settings.get("max_regions", 4)}


def detect(image, output, cfg, settings):
    detector = local_path(settings["detector"])
    e.need(detector.is_file(), "手部检测模型未安装")
    digest = hashlib.sha256(detector.read_bytes()).hexdigest()
    e.need(digest == settings["sha256"], "手部检测模型校验失败")
    result = subprocess.run(
        [str(local_path(cfg["python_path"])), "-X", "utf8", str(ROOT / "scripts/detect_hands.py"),
         "--model", str(detector), "--image", str(image), "--output", str(output),
         "--confidence", str(settings["confidence"]),
         "--imgsz", str(settings.get('image_size', 640))],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=90,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    (output.parent / "detector.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    e.need(result.returncode == 0, "手部检测失败；日志已保留")
    return e.read(output)


def regions(detections, size, limit):
    """Merge touching hands before expanding each region for wrist/pose context."""
    width, height = size
    boxes = []
    for detection in sorted(detections, key=lambda d: d.get("confidence", 0), reverse=True):
        raw = detection.get("box", [])
        if len(raw) != 4 or not all(isinstance(v, (float, int)) and math.isfinite(v) for v in raw):
            continue
        x1, y1, x2, y2 = raw
        x1, y1, x2, y2 = max(0, x1), max(0, y1), min(width, x2), min(height, y2)
        if x2 - x1 < 8 or y2 - y1 < 8:
            continue
        pad = max(4, min(x2 - x1, y2 - y1) * 0.12)
        boxes.append([max(0, int(x1 - pad)), max(0, int(y1 - pad)),
                      min(width, math.ceil(x2 + pad)), min(height, math.ceil(y2 + pad))])
    groups = []
    for box in boxes:
        members = [box]
        changed = True
        while changed:
            changed = False
            for group in groups[:]:
                if any(a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]
                       for a in members for b in group):
                    members += group
                    groups.remove(group)
                    changed = True
        groups.append(members)
    result = []
    # Four studio characters can expose eight disjoint hands.
    for members in groups[:max(1, min(8, limit))]:
        x1, y1 = min(b[0] for b in members), min(b[1] for b in members)
        x2, y2 = max(b[2] for b in members), max(b[3] for b in members)
        padding = max(32, (x2 - x1) * 0.65, (y2 - y1) * 0.65)
        crop = [max(0, int(x1 - padding)), max(0, int(y1 - padding)),
                min(width, math.ceil(x2 + padding)), min(height, math.ceil(y2 + padding))]
        result.append({"boxes": members, "crop": crop})
    return result


def prepare(image, region, folder, target):
    crop = region["crop"]
    original = image.crop(crop)
    mask = Image.new("L", original.size, 0)
    draw = ImageDraw.Draw(mask)
    for x1, y1, x2, y2 in region["boxes"]:
        draw.rounded_rectangle((x1 - crop[0], y1 - crop[1], x2 - crop[0] - 1, y2 - crop[1] - 1),
                               radius=6, fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(3))
    scale = min(1024, max(512, target)) / max(original.size)
    size = tuple(max(64, round(d * scale / 64) * 64) for d in original.size)
    original.resize(size, Image.Resampling.LANCZOS).save(folder / "crop.png")
    mask.resize(size, Image.Resampling.LANCZOS).convert("RGB").save(folder / "mask.png")
    return mask, size


def workflow(checkpoint, cfg, settings, seed, crop, mask, loras):
    from .comfyui import graph
    positive = "masterpiece, best quality, detailed hands, natural fingers, natural hand pose, sharp lineart"
    negative = "bad hands, malformed hands, extra fingers, missing fingers, fused fingers, extra arms, blurry, low quality"
    recipe = {**cfg, "steps": settings["steps"]}
    result = graph(checkpoint, positive, negative, seed, recipe, loras)
    result.pop("4")
    result.update({
        "210": {"class_type": "LoadImage", "inputs": {"image": crop}},
        "211": {"class_type": "LoadImage", "inputs": {"image": mask}},
        "212": {"class_type": "ImageToMask", "inputs": {"image": ["211", 0], "channel": "red"}},
        "213": {"class_type": "VAEEncode", "inputs": {"pixels": ["210", 0], "vae": ["1", 2]}},
        "214": {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["213", 0], "mask": ["212", 0]}},
    })
    result["5"]["inputs"].update(latent_image=["214", 0], denoise=settings["denoise"])
    if cfg.get("transient"):
        result["7"] = {"class_type": "PreviewImage", "inputs": {"images": ["6", 0]}}
    else:
        result["7"]["inputs"]["filename_prefix"] = "comic-studio/hands"
    return result


class PendingDetail(Exception):
    pass


def render(graph, folder, cfg, settings, state, persist):
    from .comfyui import api
    e.save(folder / "workflow.json", graph)
    state["client_id"] = uuid.uuid4().hex
    state["submission_pending"] = True
    persist()
    try:
        submitted = api("/prompt", {"prompt": graph, "client_id": state["client_id"]})
    except Exception as exc:
        cause = exc.__cause__
        if isinstance(cause, urllib.error.HTTPError) and 400 <= cause.code < 500:
            state["submission_pending"] = False
            raise
        raise PendingDetail("手部细化提交状态未确认；保留原图，不重复提交") from exc
    state["submission_pending"] = False
    e.save(folder / "job.json", submitted)
    e.need(not submitted.get("node_errors"), "手部细化节点校验失败")
    prompt_id = submitted.get("prompt_id")
    if not prompt_id:
        state["submission_pending"] = True
        persist()
        raise PendingDetail("手部细化未返回任务编号；请检查绘图队列")
    state["prompt_id"] = prompt_id
    persist()
    deadline = time.monotonic() + settings["timeout_seconds"]
    try:
        while time.monotonic() < deadline:
            history = api("/history/" + prompt_id).get(prompt_id)
            if history:
                break
            time.sleep(0.5)
        else:
            raise TimeoutError()
    except Exception as exc:
        raise PendingDetail("手部细化任务尚未确认完成；编号已保留，请检查绘图队列") from exc
    e.save(folder / "history.json", history)
    if cfg.get("transient") and history.get("status", {}).get("status_str") == "error":
        from .comfyui import cleanup_prompt_output
        cleanup_prompt_output(prompt_id, history)
    e.need(history.get("status", {}).get("status_str") != "error", "手部细化执行失败")
    images = history.get("outputs", {}).get("7", {}).get("images", [])
    e.need(images, "手部细化未返回图片")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(cfg["endpoint"] + "/view?" + urllib.parse.urlencode(images[0]), timeout=30) as response:
        content = response.read(40_000_001)
    e.need(len(content) <= 40_000_000, "手部细化返回图片过大")
    output = folder / "result.png"
    output.write_bytes(content)
    if cfg.get("transient"):
        from .comfyui import cleanup_prompt_output
        cleanup_prompt_output(prompt_id, history)
    with Image.open(output) as image:
        image.verify()
    return output


def enhance(image_path, checkpoint, cfg, seed, loras, update, on_state=None):
    """Keep the original and commit a result only after all bounded passes finish."""
    settings = config()
    image_path = Path(image_path)
    base = image_path.with_name("base-" + image_path.name)
    shutil.copy2(image_path, base)
    folder = image_path.parent / ("hand-detail-" + image_path.stem)
    folder.mkdir()
    report = {"status": "running", "base_file": base.name, "detector": settings.get("detector"),
              "settings": settings, "regions": [], "passes": []}

    def persist():
        e.save(folder / "status.json", report)
        if on_state:
            on_state(report)

    persist()
    try:
        update({"message": "检测手部，准备局部细化"})
        found = detect(image_path.resolve(), folder / "detection.json", cfg, settings)
        with Image.open(image_path) as source:
            image = source.convert("RGB")
        e.need(found["size"] == list(image.size), "手部检测图片尺寸不一致")
        report["regions"] = regions(found["boxes"], image.size, settings["max_regions"])
        if not report["regions"]:
            report["status"] = "no_hands"
            persist()
            return report
        input_root = DATA / "comfy-input" / "comic-hands" / uuid.uuid4().hex
        input_root.mkdir(parents=True)
        for n, region in enumerate(report["regions"], 1):
            update({"message": f"局部放大细化手部 {n}/{len(report['regions'])}"})
            work = folder / str(n)
            work.mkdir()
            mask, size = prepare(image, region, work, settings["crop_size"])
            for filename in ("crop.png", "mask.png"):
                shutil.copy2(work / filename, input_root / (str(n) + "-" + filename))
            crop_name = (input_root / f"{n}-crop.png").relative_to(input_root.parents[1]).as_posix()
            mask_name = (input_root / f"{n}-mask.png").relative_to(input_root.parents[1]).as_posix()
            detail_cfg = {**cfg, "width": size[0], "height": size[1]}
            graph = workflow(checkpoint, detail_cfg, settings, (seed + n) % 2**53,
                             crop_name, mask_name, loras)
            state = {"region": n, "size": list(size)}
            report["passes"].append(state)
            result = render(graph, work, cfg, settings, state, persist)
            with Image.open(result) as output:
                e.need(output.size == size, "手部细化图片尺寸不一致")
                patch = output.convert("RGB").resize(mask.size, Image.Resampling.LANCZOS)
            image.paste(patch, (region["crop"][0], region["crop"][1]), mask)
            state["completed"] = True
            persist()
        pending = image_path.with_name(image_path.name + ".enhanced.tmp")
        image.save(pending, format="PNG")
        pending.replace(image_path)
        report["status"] = "done"
    except PendingDetail as exc:
        report.update(status="uncertain", error=str(exc))
    except Exception as exc:
        report.update(status="failed", error=str(exc))
        update({"message": "手部增强未完成，已保留原图：" + str(exc)})
    persist()
    return report
