import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from comic_workflow import engine as e, image_clipboard
from comic_workflow.repository import Repository


class ImageClipboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Repository(Path(self.tmp.name) / "projects")
        self.addCleanup(self.repo._temporary.cleanup)
        self.pid = self.repo.create_transient()
        e.save(self.repo.folder(self.pid) / "project.json", {"quick_sample": True})
        self.root = self.repo.folder(self.pid) / "images"
        self.root.mkdir()
        for i, color in enumerate(("red", "blue"), 1):
            Image.new("RGB", (16, 16), color).save(self.root / f"{i}.png")
        e.save(self.root / "index.json", {"batches": [{"images": [
            {"id": "first", "file": "1.png"}, {"id": "second", "file": "2.png"}]}]})

    def test_original_order_and_clipboard_survives_session_release(self):
        captured = []
        with patch.object(image_clipboard, "start_clipboard", side_effect=captured.append):
            result = image_clipboard.copy_images(self.repo, self.pid, ["second", "first"])
        cache = captured[0]
        self.addCleanup(image_clipboard.cleanup_cache, cache)
        files = e.read(cache / "request.json")["files"]
        self.assertEqual(Path(files[0]).read_bytes(), (self.root / "2.png").read_bytes())
        self.assertEqual(Path(files[1]).read_bytes(), (self.root / "1.png").read_bytes())
        self.repo.release_transient(self.pid)
        self.assertTrue(all(Path(f).exists() for f in files))
        self.assertEqual(result, {"copied": 2})
        self.assertEqual(list(self.repo.root.rglob("*.png")), [])

    def test_rejects_missing_duplicate_and_outside_images(self):
        with patch.object(image_clipboard, "start_clipboard") as start:
            for ids in ([], ["missing"], ["first", "first"], "first"):
                with self.assertRaises(ValueError):
                    image_clipboard.copy_images(self.repo, self.pid, ids)
            outside = Path(self.tmp.name) / "outside.png"
            Image.new("RGB", (16, 16)).save(outside)
            e.save(self.root / "index.json", {"batches": [{"images": [{"id": "first", "file": str(outside)}]}]})
            with self.assertRaises(ValueError):
                image_clipboard.copy_images(self.repo, self.pid, ["first"])
            start.assert_not_called()

    def test_copy_failure_removes_only_its_cache(self):
        caches = []
        def fail(cache):
            caches.append(cache)
            raise ValueError("clipboard busy")
        with patch.object(image_clipboard, "start_clipboard", side_effect=fail):
            with self.assertRaisesRegex(ValueError, "clipboard busy"):
                image_clipboard.copy_images(self.repo, self.pid, ["first"])
        self.assertFalse(caches[0].exists())
        self.assertTrue((self.root / "1.png").exists())
        with self.assertRaises(ValueError):
            image_clipboard.cleanup_cache(self.root)


if __name__ == "__main__":
    unittest.main()
