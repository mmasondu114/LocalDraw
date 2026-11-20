"""Neutral eight-page resume fixture. All model calls are mocked."""
import sys
from pathlib import Path
from unittest.mock import Mock, patch
from http.server import ThreadingHTTPServer

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root), str(root / "src")]
from tests.test_storyboard_resume import StoryboardResumeTests
from comic_workflow import engine as e
from comic_workflow.server import Application, make_handler

fixture = StoryboardResumeTests()
fixture.setUp()
fixture.save_partial()
e.save(fixture.root / "jobs/book.json", {
    "id": "book", "project": fixture.pid, "label": "生成整部分镜",
    "status": "running", "created": "1", "request": fixture.request,
})
e.save(fixture.repo.folder(fixture.pid) / "workspace.json", {
    "stage": "page", "target": "P001", "updated": 1, "views": {}})
with patch("comic_workflow.jobs.DATA", fixture.root):
    app = Application(fixture.repo)
app.prepare_language = Mock()
app.model.stop = Mock()
app.model.start = Mock(side_effect=RuntimeError("离线测试不加载模型"))
app.comfy.start = Mock(side_effect=RuntimeError("离线测试不加载模型"))
app.model.status = lambda: {"ready": False, "owned": False, "generating": False, "file_size_ok": True}
original_generate = fixture.generate
def generate(*args):
    result = original_generate(*args)
    print(args[1], args[2], flush=True)
    return result

print(fixture.pid, flush=True)
try:
    with patch("comic_workflow.storyboard_tools.generate", side_effect=generate):
        ThreadingHTTPServer(("127.0.0.1", 8787), make_handler(app)).serve_forever()
finally:
    fixture.tmp.cleanup()
