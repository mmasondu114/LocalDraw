import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from comic_workflow import engine as e


class AtomicSaveTests(unittest.TestCase):
    def test_transient_windows_lock_preserves_old_json_then_retries(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "job.json"
            e.save(target, {"value": "old"})
            replace = Path.replace
            calls = []

            def busy_once(source, destination):
                calls.append(source)
                self.assertEqual(e.read(target), {"value": "old"})
                if len(calls) < 3:
                    raise PermissionError(13, "Windows sharing violation")
                return replace(source, destination)

            with (
                patch.object(Path, "replace", busy_once),
                patch("comic_workflow.engine.time.sleep"),
            ):
                e.save(target, {"value": "new"})
            self.assertEqual(len(calls), 3)
            self.assertEqual(e.read(target), {"value": "new"})
            self.assertEqual(list(Path(root).glob("*.tmp")), [])

    def test_persistent_denial_keeps_original_and_is_bounded(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "job.json"
            e.save(target, {"value": "old"})
            with (
                patch.object(
                    Path, "replace", side_effect=PermissionError("denied")
                ) as replacement,
                patch("comic_workflow.engine.time.sleep"),
                self.assertRaises(PermissionError),
            ):
                e.save(target, {"value": "new"})
            self.assertEqual(replacement.call_count, 8)
            self.assertEqual(e.read(target), {"value": "old"})
            self.assertEqual(list(Path(root).glob("*.tmp")), [])
