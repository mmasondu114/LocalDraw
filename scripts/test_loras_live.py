"""Opt-in smoke test of structurally compatible local LoRAs, no training."""

import hashlib
import sys
from pathlib import Path
from PIL import Image

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from comic_workflow import engine as e, comfyui, creative
from comic_workflow.characters import CharacterLibrary
from comic_workflow.repository import Repository
from comic_workflow.lora_inspection import inspect_local
from comic_workflow.model import ModelManager

out = root / "data/test-results/lora-live" / e.stamp()
repo = Repository(out / "projects")
pid = repo.create(None, True)
p = repo.get(pid)
panel = p["pages"]["P001"]["panels"][0]
target = panel["id"]
prompt = {
    "panel_id": target,
    "positive": "1girl, adult woman, brown hair, white shirt, blue apron, bookstore, standing, anime illustration, masterpiece, high score",
    "negative": "text, watermark, lowres",
    "composition_variants": ["front view", "side view", "three quarter view"],
    "character_anchors": [
        {"character_id": x["character_id"], "tags": "adult woman"}
        for x in panel["characters"]
    ],
    "adaptation_notes": "仅测试 LoRA 技术加载，不作为指定角色效果验收",
}
c = repo.propose(pid, "drawing_prompt", target, prompt, e.digest(p))
repo.accept(pid, c["id"])
manager = comfyui.ComfyManager()
report = {}
e.need(not ModelManager().status()["ready"], "请等待语言模型任务结束，避免显存冲突")
try:
    writer = ModelManager()
    try:
        writer.start()
        character = creative.character_draft(
            CharacterLibrary(out / "characters"),
            {
                "name": "文字验收角色",
                "mode": "text",
                "text": "原创成年女性，28岁，黑色齐肩短发、绿色眼睛、灰色长风衣和棕色皮靴。性格冷静、说话简短，是修钟表的工匠；害怕迟到，习惯用手指敲桌面计时，不携带武器。",
            },
            out / "text-character",
            lambda s: print(s, flush=True),
        )
        e.save(out / "text-character.json", character)
    finally:
        writer.stop()
    manager.start()
    for name, inspection in inspect_local().items():
        if inspection["structurally_compatible"] is not True:
            report[name] = {"status": "incompatible_skipped", **inspection}
            continue
        print("Testing " + name, flush=True)
        log = root / "data/logs/comfyui.log"
        offset = log.stat().st_size
        try:
            b = comfyui.run_batch(
                repo,
                pid,
                target,
                lambda s: print(s, flush=True),
                count=1,
                loras=[
                    {
                        "file": name,
                        "weight": 0.7,
                        "clip_weight": 0.7,
                        "trigger_words": "",
                        "compatible_checkpoint": comfyui.config()["checkpoint"],
                        "confirmed_compatibility": True,
                    }
                ],
            )
            with Image.open(
                repo.folder(pid) / "images" / b["images"][0]["file"]
            ) as image:
                size = image.size
            with log.open("rb") as f:
                f.seek(offset)
                tail = f.read().decode(errors="replace")
            warnings = [
                s
                for s in tail.splitlines()
                if "not loaded" in s.lower() or "size mismatch" in s.lower()
            ]
            path = root / "models/loras" / name
            report[name] = {
                "status": "passed" if not warnings else "partial",
                "checkpoint": comfyui.config()["checkpoint"],
                "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "file_size": path.stat().st_size,
                "mtime_ns": path.stat().st_mtime_ns,
                "image": str(repo.folder(pid) / "images" / b["images"][0]["file"]),
                "dimensions": size,
                "warnings": warnings,
                "note": "真实加载并产出图片；不保证特定画风或角色效果",
            }
        except Exception as exc:
            report[name] = {"status": "failed", "error": str(exc)}
        e.save(out / "report.json", report)
    e.save(root / "data/lora-load-tests.json", report)
    e.save(out / "report.json", report)
finally:
    manager.stop()
print(e.encoded(report), flush=True)
