"""Isolated browser acceptance fixture; contains no user artwork or model calls."""

import copy
import argparse
import sys
import time
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from comic_workflow import engine as e
from comic_workflow.repository import Repository
from comic_workflow.server import Application, make_handler

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=8786)
parser.add_argument("--data-root", type=Path)
parser.add_argument("--simulate-queue", type=int, default=0,
                    help="Add two offline progress-only tasks, with this many steps each")
args = parser.parse_args()
if not 0 <= args.simulate_queue <= 1800:
    parser.error("--simulate-queue must be between 0 and 1800")
root = args.data_root or Path(__file__).resolve().parents[1] / "data/test-results/ui-upgrade-fixture"
repo = Repository(root / "projects")
ids = list(repo.root.glob("*/project.json"))
if ids:
    pid = ids[0].parent.name
else:
    pid = repo.create(None, True)
    p = repo.get(pid)
    payload = copy.deepcopy(p["pages"]["P001"])
    payload["panels"][0]["drawing_description"] = "测试修改：人物在窗边捧起纸船。"
    payload["panels"][1]["drawing_description"] = "测试修改：拉近镜头，保留原对白。"
    repo.propose(pid, "page", "P001", payload, e.digest(p))
    batches = []
    for panel in (panel for page in p["pages"].values() for panel in page["panels"]):
        bid = panel["id"].replace("-", "")
        images = []
        for n in range(1, 17):
            path = repo.folder(pid) / f"images/{bid}/{n}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (160, 100), (30 + n * 9, 80 + n * 5, 130)).save(path)
            images.append(
                {
                    "id": f"{bid}-{n}",
                    "file": f"{bid}/{n}.png",
                    "view_index": n,
                    "review": {"verdict": "pass", "reason": "人工测试图片"},
                }
            )
        batches.append(
            {
                "id": bid,
                "panel_id": panel["id"],
                "panel_hash": e.digest(panel),
                "images": images,
            }
        )
    e.save(
        repo.folder(pid) / "images/index.json", {"batches": batches, "selections": {}}
    )
    e.save(
        repo.folder(pid) / "workspace.json",
        {"stage": "page", "target": "P001", "updated": 1, "views": {}},
    )
with patch("comic_workflow.jobs.DATA", root):
    app = Application(repo)


# No GPU processes are used by this fixture.
def no_inference(*args, **kwargs):
    raise ValueError("这是离线界面验收窗口，模型推理已禁用。")


app.prepare_language = no_inference
app.model.start = no_inference
app.comfy.start = no_inference
app.model.stop = lambda: None
app.comfy.stop = lambda: None
app.jobs.on_pause = lambda: None
app.model.status = lambda: {
    "ready": False,
    "owned": False,
    "generating": False,
    "file_size_ok": True,
}
if args.simulate_queue:
    from comic_workflow.task_control import checkpoint

    original_run_item = app.night_queue.run_item

    def simulate_item(item, update, jid):
        if not item["request"].get("ui_simulation"):
            return original_run_item(item, update, jid)
        total = item["request"]["candidate_count"]
        for completed in range(total + 1):
            checkpoint()
            update({"message": "离线验收：正在模拟任务进度，不调用模型",
                    "progress": {"completed": completed, "total": total, "unit": "步"}})
            if completed < total:
                time.sleep(2)
        return {"completion_message": "离线进度演示完成，未生成图片"}

    app.night_queue.run_item = simulate_item
    if not any(item["request"].get("ui_simulation") for item in app.night_queue.state["items"]):
        for label in ("离线演示 · 第一项", "离线演示 · 接着执行"):
            app.night_queue.add(pid, label, {"action": "draw_imported", "checkpoint": "sdxl",
                "targets": ["P001-K01"], "candidate_count": args.simulate_queue, "ui_simulation": True})
print(pid, flush=True)
ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app)).serve_forever()
