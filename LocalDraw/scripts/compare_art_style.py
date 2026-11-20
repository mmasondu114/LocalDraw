"""Small same-seed comparison for visual judgment; no story or LoRA edits."""

import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from comic_workflow import comfyui, engine as e
from comic_workflow.reference_images import references_for_panel, apply_references
from comic_workflow.paths import ROOT


def main():
    output = ROOT / "data/test-results/art-style" / e.stamp()
    output.mkdir(parents=True)
    p = e.read(ROOT / "data/projects/story-0932d83a32c9/project.json")
    panel = next(x for x in p["pages"]["P005"]["panels"] if x["id"] == "P005-K02")
    cfg = comfyui.config()
    cfg.update(width=832, height=1216)
    positive = (
        r"1girl, exusiai \(arknights\), arknights, safe, solo, "
        "red short hair, orange eyes, halo, white light wings, "
        "black and white tactical jacket, black fingerless gloves, "
        "upper body, gentle smile, looking at viewer, standing in an office, "
        "window light, detailed eyes, clean lineart, delicate anime coloring, "
        "masterpiece, high score, great score, absurdres"
    )
    negative = (
        "lowres, bad anatomy, bad hands, text, error, extra digits, fewer digits, "
        "worst quality, low quality, low score, bad score, average score, "
        "watermark, signature, blurry, multiple people"
    )
    loras = [{"file": "明日方舟/明日方舟风格_AnimeOriginalArtStyleShadow2_SDXL-000100.safetensors", "weight": 0.7, "clip_weight": 0.7}]
    references = references_for_panel(p, panel)
    manager = comfyui.ComfyManager()
    report = {
        "purpose": "画风诊断，不是漫画成品；同一随机种子下逐项加入 LoRA 和参考图",
        "cases": [],
    }
    print(str(output), flush=True)
    try:
        e.need(
            not comfyui.discover()["ready"],
            "绘图服务已有其他拥有者，请通过网页卸载后重试",
        )
        manager.start()
        for seed in (2026091801, 2026091802):
            for key, label, use_lora, use_reference in [
                ("A", "底模原生", False, False),
                ("B", "底模＋你的 LoRA 0.7", True, False),
                ("C", "底模＋LoRA＋原图参考 0.7", True, True),
            ]:
                name = f"{seed}-{key}"
                graph = comfyui.graph(
                    cfg["checkpoint"],
                    positive,
                    negative,
                    seed,
                    cfg,
                    loras if use_lora else [],
                )
                if use_reference:
                    apply_references(graph, references, cfg)
                e.save(output / (name + "-workflow.json"), graph)
                result = comfyui.api("/prompt", {"prompt": graph})
                e.need(not result.get("node_errors"), str(result))
                e.save(output / (name + "-job.json"), result)
                until = time.monotonic() + 900
                record = None
                while time.monotonic() < until:
                    record = comfyui.api("/history/" + result["prompt_id"]).get(
                        result["prompt_id"]
                    )
                    if record:
                        break
                    time.sleep(1)
                e.need(
                    record and record.get("status", {}).get("status_str") != "error",
                    "诊断出图失败，请检查保存的任务记录",
                )
                e.save(output / (name + "-history.json"), record)
                descriptor = record["outputs"]["7"]["images"][0]
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(
                    cfg["endpoint"] + "/view?" + urllib.parse.urlencode(descriptor),
                    timeout=30,
                ) as response:
                    (output / (name + ".png")).write_bytes(response.read())
                report["cases"].append(
                    {
                        "label": label,
                        "file": name + ".png",
                        "seed": seed,
                        "lora": use_lora,
                        "reference": use_reference,
                    }
                )
                e.save(output / "report.json", report)
                print(label, seed, "saved", flush=True)
        cards = "".join(
            f'<figure><img src="{x["file"]}"><figcaption>{x["label"]} · 种子 {x["seed"]}</figcaption></figure>'
            for x in report["cases"]
        )
        (output / "index.html").write_text(
            '<!doctype html><meta charset="utf-8"><title>画风对照</title><style>body{font:18px sans-serif;background:#eee;margin:24px}main{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}figure{margin:0}img{width:100%}</style><h1>画风对照：同一角色、提示词和随机种子</h1><p>这些是诊断图，不是剧情成品。每一行只改变 LoRA 和参考图开关。</p><main>'
            + cards
            + "</main>",
            encoding="utf-8",
        )
    finally:
        manager.stop()
        report["inference_stopped"] = True
        e.save(output / "report.json", report)


if __name__ == "__main__":
    main()
