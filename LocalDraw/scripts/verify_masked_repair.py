"""One isolated real inference; verify exact pixel preservation outside a mask."""

import sys
import time
import shutil
import urllib.parse
import urllib.request
from pathlib import Path
from PIL import Image, ImageDraw, ImageChops

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from comic_workflow import comfyui, engine as e
from comic_workflow.framing import apply_local_repair
from comic_workflow.paths import DATA


def main():
    e.need(
        not any(
            e.read(p).get("status") == "running" for p in (DATA / "jobs").glob("*.json")
        ),
        "已有任务正在运行",
    )
    e.need(not comfyui.discover()["ready"], "已有绘图服务，不能接管")
    source = DATA / "test-results/art-style/20260918-145804-389075/2026091802-B.png"
    output = DATA / "test-results/masked-repair" / e.stamp()
    folder = DATA / "comfy-input/repairs" / output.name
    folder.mkdir(parents=True)
    output.mkdir(parents=True)
    shutil.copyfile(source, folder / "original.png")
    original = Image.open(source).convert("RGB")
    mask = Image.new("RGB", original.size, "black")
    w, h = original.size
    ImageDraw.Draw(mask).rectangle(
        (int(w * 0.82), int(h * 0.02), w - 1, int(h * 0.15)), fill="white"
    )
    mask.save(folder / "mask.png")
    cfg = comfyui.config()
    cfg.update(width=w, height=h)
    graph = comfyui.graph(
        cfg["checkpoint"],
        "office background, soft window light, clean anime lineart",
        "text, watermark, low quality",
        2026091804,
        cfg,
    )
    apply_local_repair(
        graph,
        f"repairs/{output.name}/original.png",
        f"repairs/{output.name}/mask.png",
        "plain grey office wall",
    )
    e.save(output / "workflow.json", graph)
    manager = comfyui.ComfyManager()
    try:
        manager.start()
        job = comfyui.api("/prompt", {"prompt": graph})
        e.need(not job.get("node_errors"), str(job))
        e.save(output / "job.json", job)
        deadline = time.monotonic() + 600
        record = None
        while time.monotonic() < deadline:
            record = comfyui.api("/history/" + job["prompt_id"]).get(job["prompt_id"])
            if record:
                break
            time.sleep(1)
        e.need(
            record and record.get("status", {}).get("status_str") != "error",
            "修复出图失败",
        )
        e.save(output / "history.json", record)
        descriptor = record["outputs"]["7"]["images"][0]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(
            cfg["endpoint"] + "/view?" + urllib.parse.urlencode(descriptor), timeout=30
        ) as response:
            (output / "result.png").write_bytes(response.read())
        result = Image.open(output / "result.png").convert("RGB")
        diff = ImageChops.difference(original, result)
        outside = ImageChops.multiply(diff, ImageChops.invert(mask))
        e.need(outside.getbbox() is None, "框外像素发生变化")
        e.need(diff.getbbox() is not None, "修复未改变任何像素")
        e.save(
            output / "report.json",
            {
                "outside_mask_identical": True,
                "inside_changed": True,
                "source": str(source),
                "purpose": "仅验证局部修复技术链路，不代表美术验收；不改作品",
            },
        )
        print(output, flush=True)
    finally:
        manager.stop()


if __name__ == "__main__":
    main()
