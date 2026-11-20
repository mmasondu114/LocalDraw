import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from comic_workflow import comfyui, engine as e, manual_drawing
from comic_workflow.jobs import Jobs
from comic_workflow.repository import Repository
from comic_workflow.server import Application


class DrawingResetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.repo = Repository(root / "projects")
        self.addCleanup(self.repo._temporary.cleanup)
        self.pid = self.repo.create(None, True)
        self.folder = self.repo.folder(self.pid)
        self.project = self.repo.get(self.pid)
        self.panel = self.project["pages"]["P001"]["panels"][0]
        self.app = Application.__new__(Application)
        self.app.repo = self.repo
        with patch("comic_workflow.jobs.DATA", root):
            self.app.jobs = Jobs()
        self.index = {"batches": [{"id": "batch", "status": "done", "images": [
            {"id": "old", "file": "batch/old.png"}]}], "selections": {
                self.panel["id"]: {"panel_hash": e.digest(self.panel), "image": {"id": "old"}}}}
        e.save(self.folder / "images/index.json", self.index)
        (self.folder / "images/batch").mkdir()
        (self.folder / "images/batch/old.png").write_bytes(b"artwork")
        (self.folder / "layouts/page").mkdir(parents=True)
        (self.folder / "layouts/page/page.png").write_bytes(b"composed")
        e.save(self.folder / "manual-prompts.json", {"panels": [{"positive": "Keep this prompt"}]})
        e.save(self.folder / "drawing-settings.json", {"checkpoint": "keep.safetensors", "mode": "none", "loras": []})
        self.request = {"action": "clear_generated_images", "project": self.pid,
                        "base_hash": e.digest(self.project)}

    def test_clear_all_artwork_preserves_story_inputs_and_enables_redraw(self):
        other = self.repo.create(None, True)
        e.save(self.repo.folder(other) / "images/index.json", self.index)
        preserved = {f: (self.folder / f).read_bytes() for f in
                     ("project.json", "manual-prompts.json", "drawing-settings.json")}
        args = {"base_hash": self.request["base_hash"]}
        self.assertIn(self.panel["id"], manual_drawing.preview(self.repo, self.pid, args)["skipped"])
        result = self.app.action(self.request)
        backup = Path(result["backup"])
        self.assertEqual(result["cleared"], 1)
        self.assertEqual((backup / "images/batch/old.png").read_bytes(), b"artwork")
        self.assertEqual((backup / "layouts/page/page.png").read_bytes(), b"composed")
        self.assertFalse((self.folder / "layouts").exists())
        self.assertEqual(comfyui.drawing_state(self.repo, self.pid), {"batches": [], "selections": {}})
        self.assertEqual(comfyui.drawing_state(self.repo, other), self.index)
        for filename, raw in preserved.items():
            self.assertEqual((self.folder / filename).read_bytes(), raw)
        preview = manual_drawing.preview(self.repo, self.pid, args)
        self.assertEqual(preview["skipped"], [])
        self.assertIn(self.panel["id"], [p["panel_id"] for p in preview["panels"]])
        self.assertEqual(self.app.action(self.request), {"cleared": 0})
        e.save(self.folder / "images/index.json", {"batches": [{"images": [{"id": "new"}]}], "selections": {}})
        self.assertEqual(comfyui.drawing_state(self.repo, self.pid)["batches"][0]["images"], [{"id": "new"}])

    def test_rejects_active_or_finishing_jobs_and_stale_work(self):
        for status in ("running", "pausing", "paused", "cancelling"):
            self.app.jobs.memory["job"] = {"project": self.pid, "status": status, "created": "1"}
            with self.subTest(status=status), self.assertRaises(ValueError):
                self.app.action(self.request)
        self.app.jobs.memory.clear()
        self.app.jobs.controls["finishing"] = {"job": {"project": self.pid, "status": "done"}}
        with self.assertRaises(ValueError):
            self.app.action(self.request)
        self.app.jobs.controls.clear()
        with self.assertRaises(ValueError):
            self.app.action({**self.request, "base_hash": "stale"})
        self.assertEqual(comfyui.drawing_state(self.repo, self.pid), self.index)

    def test_rejects_unconfirmed_submissions(self):
        for status in ("running", "queued", "submitted", "uncertain"):
            self.index["batches"][0]["status"] = status
            e.save(self.folder / "images/index.json", self.index)
            with self.subTest(status=status), self.assertRaises(ValueError):
                self.app.action(self.request)
            self.assertEqual(comfyui.drawing_state(self.repo, self.pid), self.index)

    def test_failed_archive_rolls_back_artwork(self):
        rename = Path.rename
        def fail_layout(path, target):
            if path == self.folder / "layouts":
                raise OSError("layout locked")
            return rename(path, target)
        with patch.object(Path, "rename", fail_layout), self.assertRaises(OSError):
            self.app.action(self.request)
        self.assertEqual(comfyui.drawing_state(self.repo, self.pid), self.index)
        self.assertTrue((self.folder / "images/batch/old.png").exists())
        self.assertTrue((self.folder / "layouts/page/page.png").exists())


if __name__ == "__main__":
    unittest.main()
