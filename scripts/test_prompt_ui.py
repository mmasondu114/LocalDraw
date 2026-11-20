"""Isolated prompt/character UI fixture; never starts a GPU process."""

import sys
import argparse
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from comic_workflow import engine as e
from comic_workflow.characters import DETAILS
from comic_workflow.repository import Repository
from comic_workflow.server import Application, make_handler

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=8787)
parser.add_argument("--data-root", type=Path)
parser.add_argument("--lora-search", action="store_true")
args = parser.parse_args()
root = args.data_root or Path(__file__).resolve().parents[1] / "data/test-results/prompt-character-ui"
with patch("comic_workflow.jobs.DATA", root):
    app = Application(Repository(root / "projects"))

if not app.characters.listing():
    for name, image in [("测试甲", True), ("测试乙", False)]:
        profile = {key: "供界面验收使用的虚构角色资料，不代表用户角色。" for key in DETAILS}
        profile.update(hair="silver hair", clothing="white coat", observed=[], suggested=[], unknowns=[])
        image_id = "test-portrait" if image else None
        if image:
            path = app.characters.image_path(image_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (160, 200), "lightblue").save(path)
        app.characters.save(name, profile, {"mode": "text"}, image_id)

app.model.stop = lambda: None
app.comfy.start = lambda: None
app.comfy.stop = lambda: None
app.model.status = lambda: {"ready": False, "owned": False, "generating": False, "file_size_ok": True}

def no_inference(*args, **kwargs):
    raise ValueError("界面验收：不会启动真实模型")

app.prepare_language = lambda **kwargs: None
app.model.start = no_inference

def mock_batch(repo, pid, *args, **kwargs):
    e.save(root / "last-render-options.json", kwargs)
    return {"id": "ui-mock-batch"}

files=["原神/雷电将军_random_pony_artist123.safetensors", "明日方舟/玛恩纳_opaque.safetensors"] if args.lora_search else []
inspection={files[0]:{"structurally_compatible":True,"pinyin":"lei dian jiang jun","english_aliases":["Raiden Shogun","Raiden Ei","Baal"]},
            files[1]:{"structurally_compatible":True,"pinyin":"ma en na","english_aliases":["Młynar","Mlynar"]}} if files else {}
with (
    patch("comic_workflow.comfyui.discover", return_value={
        "ready": False, "checkpoints": ["animagine-xl-4.0-opt.safetensors"], "loras": files,
    }),
    patch("comic_workflow.server.inspect_local", return_value=inspection),
    patch("comic_workflow.comfyui.run_batch", side_effect=mock_batch),
    patch("comic_workflow.creative.structured", side_effect=lambda prompt, context, *args, **kwargs: ({
        "positive": "1girl, dancing on a frozen lake", "negative": "text, watermark",
        "characters": [{"name": c["name"], "prompt": "silver hair, white dress"} for c in context["characters"]],
    }, {})),
    patch("comic_workflow.paths.DATA", root),
):
    print(f"Prompt UI fixture: http://127.0.0.1:{args.port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app)).serve_forever()
