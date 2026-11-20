"""Small, fully clothed storyboard experiment; never reads the user's story prompts."""
import argparse
import json
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from comic_workflow import comfyui, engine as e, prompt_models
from comic_workflow.model import request_json

OUT = ROOT / "data/test-results/panel-identity-20261017"
MODEL = "animaDeltaMix38BQwen0_deltaMixV60.safetensors"
CAST = {
    "exusiai": "Adult Exusiai, short red hair, orange eyes, normal human ears, a halo, fully clothed in a red jacket over a white shirt, black trousers and boots. She has no wolf ears.",
    "texas": "Adult Texas the Omertosa, long black hair, yellow eyes, one pair of black wolf ears on her head, fully clothed in a navy coat, black trousers and boots.",
}
PANELS = [
    {"id": "01", "visible": ["exusiai"], "shot": "wide full-body shot, eye level", "action": "Exusiai stands alone in a cafe doorway, holding a blue envelope in her right hand. Daylight after rain."},
    {"id": "02", "visible": ["texas"], "shot": "close-up face portrait, three-quarter angle", "action": "Texas alone looks down toward a blue envelope at the lower edge of the picture. Cafe interior. Both wolf ears visible."},
    {"id": "03", "visible": ["exusiai", "texas"], "shot": "medium two-person shot, side view", "action": "Exusiai stands on the LEFT and hands a blue envelope across a small cafe table to Texas standing on the RIGHT. There is space between their bodies. Exusiai has human ears and a halo; Texas alone has wolf ears."},
    {"id": "04", "visible": ["exusiai", "texas"], "shot": "distant wide establishing shot", "action": "Exusiai walks away on the LEFT of a sunlit town square. Texas remains on the RIGHT beside the cafe entrance, holding the blue envelope. Their full bodies are small in the scene and far apart."},
]


def idle():
    status = request_json("http://127.0.0.1:8765/api/status")
    assert not any(j.get("status") in {"running", "queued", "paused", "pausing", "cancelling"} for j in status.get("jobs", [])), "Studio has unfinished work"
    assert not status.get("model", {}).get("generating"), "Language model is busy"


def prepare():
    idle()
    props = request_json("http://127.0.0.1:8091/props")
    assert "Qwen3.8-27B-UD-Q5_K_M.gguf" in props["model_path"]
    system = (
        "Write four safe, fully clothed anime storyboard image prompts. Output JSON only with a panels array. "
        "Each item has id, positive and negative strings. Write ALL image prompts in English. "
        "Each prompt must stand alone. Start with the shot, exact visible-person count and main action, "
        "then describe each visible character separately with their position and owned traits. "
        "Never mention absent characters, even in a negative comparison. Do not copy a two-person prefix into solo panels. "
        "Do not invent intimacy or physical contact. Do not put wolf ears in the global negative for a panel showing Texas. "
        "Keep every positive under 180 words. No nudity, sexuality or violence."
    )
    request = {"model": "comic-local", "messages": [{"role": "system", "content": system},
        {"role": "user", "content": json.dumps({"title": "Returning a lost envelope", "cast": CAST, "panels": PANELS})}],
        "temperature": 0.2, "max_tokens": 2200, "response_format": {"type": "json_object"}}
    e.save(OUT / "qwen-request.json", request)
    result = request_json("http://127.0.0.1:8091/v1/chat/completions", request, timeout=180)
    e.save(OUT / "qwen-response.json", result)
    rows = json.loads(result["choices"][0]["message"]["content"])["panels"]
    assert [r["id"] for r in rows] == [p["id"] for p in PANELS]
    e.save(OUT / "prompts.json", rows)
    e.save(OUT / "gpu-verification.json", {"model_path": props["model_path"], "context": props["default_generation_settings"]["n_ctx"],
        "timings": result.get("timings"), "gpu": subprocess.check_output(["nvidia-smi", "--query-gpu=name,memory.used,utilization.gpu", "--format=csv"], text=True)})
    print("Safe prompt drafts saved for inspection", flush=True)


def render():
    idle()
    # Release the app-owned language model before loading the drawing model.
    boot = request_json("http://127.0.0.1:8765/api/bootstrap")
    import urllib.request
    req = urllib.request.Request("http://127.0.0.1:8765/api/action", data=json.dumps({"action": "stop_model"}).encode(),
        headers={"Content-Type": "application/json", "X-Studio-Token": boot["token"]})
    with urllib.request.urlopen(req, timeout=30) as response:
        response.read()
    rows = e.read(OUT / "prompts.json")
    manager = comfyui.ComfyManager()
    report = e.read(OUT / "render-report.json") if (OUT / "render-report.json").exists() else []
    try:
        manager.start({"anima_regional_trial": True})
        queue = comfyui.api("/queue")
        assert not queue.get("queue_running") and not queue.get("queue_pending"), "Drawing queue is busy"
        files = comfyui.discover()["lora_files"]
        lora_key = next(k for k in files if "3049635" in k and k.startswith("anime/"))
        lora = {"file": files[lora_key], "weight": 0.7, "clip_weight": 0}
        common = "Two adult women, Exusiai with short red hair, orange eyes, a halo, red jacket, white shirt, black trousers, and Texas with long black hair, yellow eyes, wolf ears, navy coat, black trousers, in a cafe after the rain. Fully clothed, daylight. "
        cases = [
            {"id": "baseline-01", "positive": common + "全景，平视。能天使独自站在咖啡馆门口，右手拿着蓝色信封。", "negative": "text, watermark, blurry", "loras": [lora]},
            {"id": "baseline-02", "positive": common + "面部特写，四分之三侧面。德克萨斯独自低头看着蓝色信封。", "negative": "text, watermark, blurry", "loras": [lora]},
        ] + [{**r, "id": "fixed-" + r["id"], "loras": []} for r in rows]
        cases += [{**r, "id": "lora-" + r["id"], "loras": [lora]} for r in rows if r["id"] in {"01", "03"}]
        if (OUT / "refined-prompts.json").exists():
            cases += [{**r, "loras": [lora] if r.get("use_lora") else []} for r in e.read(OUT / "refined-prompts.json")]
        for case in cases:
            if any(r["id"] == case["id"] for r in report):
                continue
            idle()
            cfg = {**prompt_models.defaults(MODEL), "width": case.get("width", 1024), "height": 768, "literal_prompt": True}
            graph = comfyui.graph(MODEL, case["positive"], case["negative"], case.get("seed", 2026101701), cfg, case["loras"])
            # Experimental explicit spatial conditions; not enabled in production.
            default_background = case.get("background_mode") == "default"
            pos, neg = (None, None) if default_background else (["2", 0], ["3", 0])
            for index, region in enumerate(case.get("regions", [])):
                base = 700 + index * 10
                x1, y1, x2, y2 = region["bounds"]
                x, y = round(cfg["width"] * x1), round(cfg["height"] * y1)
                width, height = round(cfg["width"] * (x2-x1)), round(cfg["height"] * (y2-y1))
                def node(offset, kind, **inputs):
                    key = str(base + offset)
                    graph[key] = {"class_type": kind, "inputs": inputs}
                    return [key, 0]
                blank = node(0, "SolidMask", value=0., width=cfg["width"], height=cfg["height"])
                area = node(1, "SolidMask", value=1., width=width, height=height)
                mask = node(2, "MaskComposite", destination=blank, source=area, x=x, y=y, operation="add")
                for offset, name in [(3, "positive"), (6, "negative")]:
                    encoded = node(offset, "CLIPTextEncode", clip=graph["2"]["inputs"]["clip"], text=region[name])
                    if case.get("region_crop"):
                        masked = node(offset+1, "ConditioningSetArea", conditioning=encoded,
                                      width=max(64, width//8*8), height=max(64, height//8*8),
                                      x=x//8*8, y=y//8*8, strength=1.)
                    else:
                        masked = node(offset+1, "ConditioningSetMask", conditioning=encoded, mask=mask, strength=1., set_cond_area=case.get("mask_area", "mask bounds"))
                    previous = pos if name == "positive" else neg
                    combined = node(offset+2, "ConditioningCombine", conditioning_1=previous, conditioning_2=masked) if previous else masked
                    if name == "positive":
                        pos = combined
                    else:
                        neg = combined
            if default_background:
                assert pos and neg, "Default background requires explicit character regions"
                graph["900"] = {"class_type": "PairConditioningSetDefaultCombine", "inputs": {
                    "positive": pos, "negative": neg,
                    "positive_DEFAULT": ["2", 0], "negative_DEFAULT": ["3", 0]}}
                pos, neg = ["900", 0], ["900", 1]
            graph["5"]["inputs"].update(positive=pos, negative=neg)
            if case.get("attention_routing"):
                chain = None
                for index, region in enumerate(case["regions"]):
                    base = 700 + index * 10
                    inputs = {"mask": [str(base+2), 0], "conditioning": [str(base+3), 0], "weight": 1.}
                    if chain:
                        inputs["regions"] = chain
                    key = str(910+index)
                    graph[key] = {"class_type": "AnimaConditioningRegion", "inputs": inputs}
                    chain = [key, 0]
                graph["919"] = {"class_type": "CLIPTextEncode", "inputs": {
                    "clip": graph["2"]["inputs"]["clip"], "text": case["background"]}}
                graph["920"] = {"class_type": "ApplyAnimaRegionalConditioningPatch", "inputs": {
                    "model": graph["5"]["inputs"]["model"], "regions": chain,
                    "base_mode": "uncovered_only", "base_strength": 1.,
                    "start_percent": 0., "end_percent": case.get("routing_end", .35),
                    "cross_mask_strength": 1., "self_mask_strength": case.get("routing_self", .2), "base_ratio": .1,
                    "cross_inject_every_n_blocks": 1, "self_inject_every_n_blocks": 1,
                    "background_conditioning": ["919", 0]}}
                graph["5"]["inputs"].update(model=["920", 0], positive=["2", 0], negative=["3", 0])
            graph["7"]["inputs"]["filename_prefix"] = "panel-identity-test/" + case["id"]
            workflow_path = OUT / (case["id"] + "-workflow.json")
            if workflow_path.exists():
                assert e.read(workflow_path) == graph, "Changed experiment must use a new id: " + case["id"]
            else:
                e.save(workflow_path, graph)
            submitted_path = OUT / (case["id"] + "-submitted.json")
            if submitted_path.exists():
                submitted = e.read(submitted_path)
            else:
                submitted = comfyui.api("/prompt", {"prompt": graph})
                e.save(submitted_path, submitted)
            jid = submitted["prompt_id"]
            print("Rendering " + case["id"], flush=True)
            start = time.monotonic()
            history_path = OUT / (case["id"] + "-history.json")
            history = e.read(history_path) if history_path.exists() else None
            while time.monotonic() - start < 900:
                if history:
                    break
                history = comfyui.api("/history/" + jid).get(jid)
                if history:
                    break
                time.sleep(1)
            else:
                raise TimeoutError("Uncertain submission; inspect before retrying " + jid)
            e.save(history_path, history)
            if history["status"]["status_str"] != "success":
                error = next((v.get("exception_message") for k, v in history["status"].get("messages", []) if k == "execution_error"), "Render failed")
                report.append({"id": case["id"], "status": "error", "prompt_id": jid, "error": error})
                e.save(OUT / "render-report.json", report)
                print("Failed " + case["id"] + ": " + error.strip(), flush=True)
                continue
            entry = history["outputs"]["7"]["images"][0]
            with urllib.request.urlopen(comfyui.config()["endpoint"] + "/view?" + urllib.parse.urlencode(entry), timeout=30) as response:
                (OUT / (case["id"] + ".png")).write_bytes(response.read())
            report.append({"id": case["id"], "status": "success", "seconds": round(time.monotonic()-start, 1), "prompt_id": jid})
            e.save(OUT / "render-report.json", report)
            print("Saved " + case["id"], flush=True)
    finally:
        manager.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["prepare", "render"])
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    prepare() if args.stage == "prepare" else render()
