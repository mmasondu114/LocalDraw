"""Offline browser fixture for shared counts; capture submissions without inference."""
import sys
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from comic_workflow import engine as e, prompt_exchange, drawing_settings, manual_drawing
from comic_workflow.repository import Repository
from comic_workflow.server import Application, make_handler

root = Path(__file__).resolve().parents[1] / "data/test-results/imported-controls"
repo = Repository(root / "projects")
pid = repo.create(None, True)
document = prompt_exchange.export_storyboard(repo, pid)
for row in document["panels"]:
    row.update(positive="A person reading a book.", negative="text, blur")
preview = prompt_exchange.preview(repo, pid, document)
prompt_exchange.save_import(repo, pid, {"document":document,"confirm":True,"preview_hash":preview["preview_hash"]})
drawing_settings.save(repo, pid, {"mode":"none","checkpoint":"Anima-3.8B-v1.1.safetensors","items":[]})
e.save(repo.folder(pid) / "workspace.json", {"stage":"drawing","target":"P001-K01","updated":1,
    "views":{"drawing:P001-K01":{"fields":{"batch-candidate-count":"16"}}}})
with patch("comic_workflow.jobs.DATA", root):
    app = Application(repo)

def disabled(*args, **kwargs):
    raise ValueError("Offline fixture: inference disabled")

app.model.start = app.comfy.start = app.prepare_language = disabled
app.model.stop = app.comfy.stop = lambda: None
app.model.status = lambda: {"ready":False,"owned":False,"generating":False,"file_size_ok":True}
original = app.action
captured = []
def action(request):
    if request.get("action") in {"draw", "draw_all", "draw_imported"}:
        if request["action"] in {"draw", "draw_all"}:
            manual_drawing.prepare_request(repo, request["project"], request)
        captured.append(request)
        e.save(root / "captured.json", captured)
        return {"captured":True}
    return original(request)
app.action = action
print("http://127.0.0.1:8789", pid, flush=True)
ThreadingHTTPServer(("127.0.0.1",8789), make_handler(app)).serve_forever()
