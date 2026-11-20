"""Wait for verified components, then render base and character-LoRA smoke images."""
import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from comic_workflow import anima, comfyui, engine as e

OUT = ROOT / "data/anima-migration/gpu-verification"


def run():
    manifest = e.read(ROOT / "config/anima-model.json")
    deadline = time.monotonic() + 7200
    while not all((ROOT / f["directory"] / f["name"]).is_file() and
                  (ROOT / f["directory"] / f["name"]).stat().st_size == f["size"] for f in manifest["files"]):
        if time.monotonic() > deadline:
            raise TimeoutError("Components not ready")
        time.sleep(10)
    for f in manifest["files"]:
        with (ROOT / f["directory"] / f["name"]).open("rb") as stream:
            assert hashlib.file_digest(stream, "sha256").hexdigest() == f["sha256"], f["name"]
    print("All four component hashes verified", flush=True)
    cfg = dict(width=832, height=1216, steps=28, cfg=5, sampler_name="res_multistep", scheduler="beta")
    label = e.read(next((ROOT / "data/anima-migration/downloads").glob("原神-*.json")))
    for file in (ROOT / "data/anima-migration/downloads").glob("*.json"):
        candidate = e.read(file)
        if candidate["character"]["name"] == "甘雨" and candidate["status"] == "downloaded_verified":
            label = candidate
            break
    lora_name = comfyui.lora_file_key(label["relative_path"])
    discovered = comfyui.discover()
    lora_name = discovered["lora_files"][lora_name]
    cases = [
        ("base", "masterpiece, best quality, 1girl, original adult female character, blue hair, blue eyes, fully clothed in a long blue winter coat, standing in a peaceful garden, waist-up portrait, daylight, detailed background", []),
        ("ganyu-lora", "masterpiece, best quality, genshin ganyu, ganyu (genshin impact), blue hair, goat horns, purple eyes, 1girl, adult woman, fully clothed in a long blue winter coat and scarf, standing in a peaceful garden, waist-up portrait, daylight, detailed background",
         [{"file": lora_name, "weight": .7, "clip_weight": 0}]),
    ]
    report = {"checkpoint": anima.CHECKPOINT, "component_hashes_verified": True, "cases": []}
    for name, positive, loras in cases:
        graph = anima.graph(anima.CHECKPOINT, positive, "worst quality, low quality, blurry, text, watermark", 20261015, cfg, loras)
        graph["7"]["inputs"]["filename_prefix"] = "anima-verification/" + name
        e.save(OUT / (name + "-workflow.json"), graph)
        start = time.monotonic()
        submitted = comfyui.api("/prompt", {"prompt": graph})
        e.save(OUT / (name + "-submitted.json"), submitted)
        job = submitted["prompt_id"]
        while time.monotonic() - start < 1200:
            record = comfyui.api("/history/" + job).get(job)
            if record:
                break
            time.sleep(2)
        else:
            raise TimeoutError("GPU generation exceeded 20 minutes")
        e.save(OUT / (name + "-history.json"), record)
        if record["status"]["status_str"] != "success":
            raise RuntimeError("ComfyUI failed; inspect saved history")
        entry = record["outputs"]["7"]["images"][0]
        url = comfyui.config()["endpoint"] + "/view?" + urllib.parse.urlencode(entry)
        with urllib.request.urlopen(url, timeout=30) as response:
            (OUT / (name + ".png")).write_bytes(response.read())
        report["cases"].append({"name": name, "prompt_id": job, "seconds": round(time.monotonic() - start, 2),
                                 "status": "passed", "image": str(OUT / (name + ".png")), "loras": loras})
        e.save(OUT / "report.json", report)
        print("GPU render passed:", name, flush=True)
    e.save(OUT / "report.json", {**report, "status": "passed"})


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "run.log").open("a", encoding="utf-8", buffering=1) as log:
        sys.stdout = log
        sys.stderr = log
        try:
            run()
        except Exception as exc:
            e.save(OUT / "failure.json", {"error": str(exc), "type": type(exc).__name__})
            raise
