import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from comic_workflow import engine as e, comfyui
from comic_workflow.repository import Repository
from comic_workflow.comfy_recovery import recover_batch


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Repository(Path(self.tmp.name) / "projects")
        self.pid = self.repo.create(None, True)
        self.batch = {
            "id": "recover",
            "panel_id": "P001-K01",
            "status": "uncertain",
            "active_prompt": "owned-prompt",
            "candidate_indices": [15],
            "images": [],
            "active_candidate": {
                "id": "recover-1",
                "file": "recover/1.png",
                "view_index": 16,
                "positive": "test",
                "seed": 7,
                "review": {"verdict": "pending"},
            },
        }
        (self.repo.folder(self.pid) / "images/recover").mkdir(parents=True)
        e.save(
            self.repo.folder(self.pid) / "images/index.json",
            {"batches": [self.batch], "selections": {}},
        )

    def test_download_completed_result_without_resubmitting(self):
        buf = io.BytesIO()
        Image.new("RGB", (10, 10), "red").save(buf, format="PNG")

        class Response:
            def __enter__(s):
                return s

            def __exit__(s, *args):
                pass

            def read(s, n):
                return buf.getvalue()

        calls = []

        def api(path, body=None):
            calls.append(path)
            if path == "/queue":
                return {"queue_running": [], "queue_pending": []}
            if path == "/history/owned-prompt":
                return {
                    "owned-prompt": {
                        "status": {"status_str": "success"},
                        "outputs": {
                            "7": {
                                "images": [{"filename": "image.png", "type": "output"}]
                            }
                        },
                    }
                }
            raise AssertionError(path)

        with (
            patch.object(comfyui, "api", side_effect=api),
            patch("urllib.request.OpenerDirector.open", return_value=Response()),
        ):
            result = recover_batch(self.repo, self.pid, self.batch)
            twice = recover_batch(self.repo, self.pid, result)
        self.assertEqual(result["status"], "done")
        self.assertEqual(len(twice["images"]), 1)
        self.assertEqual(result["images"][0]["view_index"], 16)
        self.assertNotIn("/prompt", calls)

    def test_interrupt_only_timed_out_owned_prompt_then_allow_retry(self):
        calls = []
        cancelled = False

        def api(path, body=None):
            nonlocal cancelled
            calls.append((path, body))
            if path == "/queue" and body is None:
                return {
                    "queue_running": [] if cancelled else [[1, "owned-prompt", {}, {}]],
                    "queue_pending": [[2, "someone-else", {}, {}]],
                }
            if path == "/history/owned-prompt":
                return {}
            if path == "/queue":
                self.assertEqual(body, {"delete": ["owned-prompt"]})
                return {}
            if path == "/interrupt":
                self.assertEqual(body, {"prompt_id": "owned-prompt"})
                cancelled = True
                return {}
            raise AssertionError(path)

        with patch.object(comfyui, "api", side_effect=api):
            result = recover_batch(self.repo, self.pid, self.batch, cancel_running=True)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(sum(path == "/interrupt" for path, _ in calls), 1)

    def test_unreachable_queue_does_not_authorize_a_duplicate(self):
        with (
            patch.object(comfyui, "api", side_effect=ConnectionError("not connected")),
            self.assertRaises(ConnectionError),
        ):
            recover_batch(self.repo, self.pid, self.batch, True)
        saved = comfyui.drawing_state(self.repo, self.pid)["batches"][0]
        self.assertEqual(saved["status"], "uncertain")

    def test_pending_hand_pass_is_checked_instead_of_completed_base_prompt(self):
        self.batch["active_hand_detail"] = {"status": "uncertain", "passes": [
            {"prompt_id": "owned-hand", "client_id": "hand-client"}]}
        def api(path, body=None):
            if path == "/queue":
                return {"queue_running": [[1, "owned-hand", {}, {"client_id": "hand-client"}]],
                        "queue_pending": []}
            if path == "/history/owned-hand":
                return {}
            raise AssertionError(path)
        with patch.object(comfyui, "api", side_effect=api):
            result = recover_batch(self.repo, self.pid, self.batch)
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(result["active_hand_detail"]["status"], "uncertain")

    def test_unknown_hand_submission_does_not_allow_duplicate_when_not_found(self):
        self.batch["active_hand_detail"] = {"status": "uncertain", "passes": [
            {"submission_pending": True, "client_id": "hand-client"}]}
        def api(path, body=None):
            if path == "/queue":
                return {"queue_running": [], "queue_pending": []}
            if path == "/history?max_items=200":
                return {}
            raise AssertionError(path)
        with patch.object(comfyui, "api", side_effect=api):
            result = recover_batch(self.repo, self.pid, self.batch)
        self.assertEqual(result["status"], "uncertain")
