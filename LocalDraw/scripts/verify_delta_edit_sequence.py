"""Opt-in Delta v6 / Anima Edit v2 comparison, isolated from studio projects."""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from comic_workflow import prompt_models

OUT = ROOT / "data/test-results/delta-v6-anima-edit-v2-20261018"
SERVER_ROOT = OUT
ENDPOINT = "http://127.0.0.1:8192"
MODEL = "animaDeltaMix38BQwen0_deltaMixV60.safetensors"
LORA = "anime/edit/AnimeEditV2.safetensors"
IDENTITY = (
    "One adult woman, age 25, short straight teal bob haircut with blunt bangs, amber eyes, "
    "a small white triangular hair clip on her left temple. She wears a mustard-yellow hip-length "
    "jacket with dark brown buttons over a plain cream shirt, dark brown straight trousers, "
    "brown ankle boots, and a small red rectangular crossbody satchel."
)
SCENE = (
    "A quiet rural train platform in warm afternoon light, a green wooden bench, "
    "a cream station wall and a blue station clock. Anime illustration, clean line art, "
    "soft cel shading, consistent warm colors, masterpiece, best quality."
)
PANELS = [
    {"id": "01", "title": "站在长椅旁", "action": "Full-body establishing shot, eye-level view. The woman stands beside the green bench, facing the viewer, both hands relaxed at her sides. Her whole outfit and satchel are clearly visible."},
    {"id": "02", "title": "转身向右行走", "action": "Full-body side view. The woman walks briskly toward the right along the platform, shown in profile, one leg forward, the other behind, arms swinging naturally. The green bench is behind her."},
    {"id": "03", "title": "蹲下拾起蓝色车票", "action": "Medium full-body shot from a slightly high angle. The woman crouches low beside the green bench with both knees bent, reaching her right hand down to pick up a single blue train ticket lying on the stone platform. Her left hand rests on her left knee."},
    {"id": "04", "title": "近景举票微笑", "action": "Close-up chest-up portrait, three-quarter angle. The woman is standing upright, smiling warmly and holding a single blue train ticket between the fingers of her right hand beside her face. Her face fills the frame; the station wall is softly blurred behind her."},
]
NEGATIVE = "worst quality, low quality, blurry, extra people, duplicate person, extra limbs, malformed hands, extra fingers, text, watermark, comic panels, split screen"
CFG = {"width": 768, "height": 1024, "steps": 30, "cfg": 4.5, "sampler_name": "dpmpp_2m", "scheduler": "sgm_uniform"}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def api(path, body=None):
    req = urllib.request.Request(ENDPOINT + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=40) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(exc.read().decode("utf-8", "replace")) from None


def prompt(panel):
    return panel["action"] + " " + IDENTITY + " " + SCENE


def build(panel, case, ref=None, weight=1.0):
    graph = prompt_models.graph(MODEL, prompt(panel), NEGATIVE, 18102600 + int(panel["id"]), CFG)
    graph["7"]["inputs"]["filename_prefix"] = "sequence/" + case
    if ref:
        ref_name = ref.name
        target = SERVER_ROOT / "comfy-input" / ref_name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(ref, target)
        elif hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(ref.read_bytes()).digest():
            raise RuntimeError("Reference filename collision")
        graph["20"] = {"class_type": "DeltaMixQ35LoraLoader", "inputs": {
            "model": ["1", 0], "clip": ["10", 0], "lora_name": str(Path(LORA)),
            "strength_model": weight, "strength_clip": 0.0, "skip_gen2": True, "skip_watch": True}}
        graph["21"] = {"class_type": "LoadImage", "inputs": {"image": ref_name}}
        graph["24"] = {"class_type": "ImageScale", "inputs": {"image": ["21", 0], "upscale_method": "lanczos",
            "width": CFG["width"], "height": CFG["height"], "crop": "center"}}
        graph["22"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["24", 0], "vae": ["12", 0]}}
        graph["23"] = {"class_type": "ApplyCosmosReferenceLatent", "inputs": {
            "model": ["20", 0], "ref_latents.ref_latent_1": ["22", 0]}}
        graph["5"]["inputs"]["model"] = ["23", 0]
    return graph


def gpu_monitor(stop, samples):
    while not stop.is_set():
        try:
            raw = subprocess.check_output(["nvidia-smi", "--query-gpu=memory.used,utilization.gpu", "--format=csv,noheader,nounits"], text=True, creationflags=subprocess.CREATE_NO_WINDOW)
            mem, util = (int(v.strip()) for v in raw.strip().split(","))
            samples.append({"time": time.time(), "used_mib": mem, "util_percent": util})
        except Exception:
            pass
        stop.wait(1)


def render(panel, case, ref=None, weight=1.0):
    dest = OUT / case
    dest.mkdir(parents=True, exist_ok=True)
    result_path = dest / "result.json"
    image_path = dest / (case + ".png")
    graph = build(panel, case, ref, weight)
    graph_path = dest / "workflow-api.json"
    if graph_path.exists():
        assert json.loads(graph_path.read_text(encoding="utf-8")) == graph, "Use a new case name for changed settings"
    else:
        save(graph_path, graph)
    if result_path.exists() and image_path.exists():
        print("Already complete: " + case, flush=True)
        return image_path
    queue = api("/queue")
    if queue["queue_running"] or queue["queue_pending"]:
        raise RuntimeError("Test server has an active queue; inspect before resubmission")
    submitted_path = dest / "submitted.json"
    start = time.monotonic()
    samples = []
    stop = threading.Event()
    monitor = threading.Thread(target=gpu_monitor, args=(stop, samples), daemon=True)
    monitor.start()
    try:
        if submitted_path.exists():
            submitted = json.loads(submitted_path.read_text())
        else:
            submitted = api("/prompt", {"prompt": graph, "client_id": "delta-edit-sequence-test"})
            save(submitted_path, submitted)
        job = submitted["prompt_id"]
        print("Rendering " + case + " job=" + job, flush=True)
        deadline = time.monotonic() + 1200
        while time.monotonic() < deadline:
            history = api("/history/" + job).get(job)
            if history:
                break
            time.sleep(2)
        else:
            raise TimeoutError("Rendering timed out; job retained, do not resubmit")
        save(dest / "history.json", history)
        if history["status"]["status_str"] != "success":
            raise RuntimeError("Generation failed: " + json.dumps(history["status"], ensure_ascii=False))
        entry = history["outputs"]["7"]["images"][0]
        with urllib.request.urlopen(ENDPOINT + "/view?" + urllib.parse.urlencode(entry), timeout=40) as response:
            image_path.write_bytes(response.read())
        elapsed = round(time.monotonic() - start, 2)
        result = {"case": case, "panel": panel, "positive": prompt(panel), "negative": NEGATIVE,
            "config": CFG, "seed": graph["5"]["inputs"]["seed"], "seconds": elapsed,
            "reference": str(ref) if ref else None, "lora_strength": weight if ref else None,
            "image": str(image_path), "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
            "peak_device_used_mib": max((s["used_mib"] for s in samples), default=None), "prompt_id": job}
        save(result_path, result)
        print("Completed " + case + " in " + str(elapsed) + "s, peak device VRAM " + str(result["peak_device_used_mib"]) + " MiB", flush=True)
        return image_path
    finally:
        stop.set()
        monitor.join(timeout=3)
        save(dest / "gpu-samples.json", samples)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["anchor", "baseline", "chain", "all"])
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    save(OUT / "experiment.json", {"model": MODEL, "lora": LORA, "panels": PANELS,
        "identity": IDENTITY, "scene": SCENE, "negative": NEGATIVE, "config": CFG,
        "design": "Four panels. Shared unconditioned anchor; three independent controls and three previous-image-conditioned generations. Matched per-panel seeds and text. No character LoRA."})
    anchor = render(PANELS[0], "anchor-01")
    if args.mode in {"baseline", "all"}:
        for panel in PANELS[1:]:
            render(panel, "baseline-" + panel["id"])
    if args.mode in {"chain", "all"}:
        assert (ROOT / "models/loras" / LORA).exists(), "Edit LoRA is not ready"
        previous = anchor
        for panel in PANELS[1:]:
            previous = render(panel, "chain-" + panel["id"], previous)


if __name__ == "__main__":
    main()
