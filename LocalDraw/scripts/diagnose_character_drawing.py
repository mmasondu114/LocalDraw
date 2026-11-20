"""Controlled local comparison; leaves story, selections and model weights untouched."""

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from comic_workflow import comfyui, engine as e


def main():
    out = ROOT / "data/test-results/character-diagnosis" / e.stamp()
    out.mkdir(parents=True)
    source = ROOT / "data/projects/story-0eacbbbeb6fd/images/index.json"
    original = e.read(source)["batches"][0]
    seed = original["images"][0]["seed"]
    cfg = original["settings"]
    checkpoint = original["checkpoint"]
    positive = (
        r"1other, solo, doctor \(arknights\), arknights, safe, "
        "(from behind:1.3), rear three-quarter view, medium shot, seated, "
        "black hooded coat, hood up, black gloves, face out of frame, "
        "typing on computer keyboard, computer monitor in front of character, "
        "desk, stack of papers, steaming mug, office, window on the left, daylight, "
        "anime coloring, masterpiece, high score, great score, absurdres"
    )
    negative = (
        "lowres, bad anatomy, bad hands, text, error, extra digits, fewer digits, "
        "worst quality, low quality, low score, bad score, average score, "
        "watermark, signature, blurry, looking at viewer, front view, "
        "visible eyes, visible mouth, bare hands, multiple characters"
    )
    original_graph = e.read(source.parent / original["id"] / "workflow-1.json")
    cases = [("Original prompt / no LoRA", original["images"][0]["positive"],
              original_graph["3"]["inputs"]["text"], [])]
    cases.append(("Revised tags / no LoRA", positive, negative, []))
    for strength in (0.35, 0.6):
        cases.append((f"Revised tags / style {strength}", positive, negative, [{
            "file": "明日方舟/彩色线条_nai3animation_v2.6.1-000100.safetensors", "weight": strength, "clip_weight": strength,
        }]))
    manager = comfyui.ComfyManager()
    report = []
    try:
        manager.start()
        queue = comfyui.api("/queue")
        e.need(not queue.get("queue_running") and not queue.get("queue_pending"),
               "ComfyUI 有正在执行的任务，请等它结束后再诊断")
        for i, (label, pos, neg, loras) in enumerate(cases, 1):
            graph = comfyui.graph(checkpoint, pos, neg, seed, cfg, loras)
            e.save(out / f"workflow-{i}.json", graph)
            response = comfyui.api("/prompt", {"prompt": graph})
            e.need(not response.get("node_errors"), str(response))
            job = response["prompt_id"]
            record = None
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                record = comfyui.api("/history/" + job).get(job)
                if record:
                    break
                time.sleep(1)
            e.need(record and record.get("status", {}).get("status_str") != "error",
                   "诊断生成失败或超时；请检查 ComfyUI 队列")
            e.save(out / f"history-{i}.json", record)
            descriptor = record["outputs"]["7"]["images"][0]
            url = cfg["endpoint"] + "/view?" + urllib.parse.urlencode(descriptor)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(url, timeout=30) as response:
                (out / f"{i}.png").write_bytes(response.read())
            report.append({"label": label, "file": f"{i}.png", "seed": seed,
                           "positive": pos, "negative": neg, "loras": loras})
            e.save(out / "comparison.json", report)
            print(label, flush=True)
        sheet = Image.new("RGB", (1280, 980), "white")
        draw = ImageDraw.Draw(sheet)
        for i, entry in enumerate(report):
            x, y = (i % 2) * 640, (i // 2) * 490
            with Image.open(out / entry["file"]) as picture:
                picture.thumbnail((620, 455))
                sheet.paste(picture, (x + (640 - picture.width) // 2, y + 30))
            draw.text((x + 12, y + 8), entry["label"], fill="black")
        sheet.save(out / "comparison.jpg")
        print(str(out), flush=True)
    finally:
        manager.stop()


if __name__ == "__main__":
    main()
