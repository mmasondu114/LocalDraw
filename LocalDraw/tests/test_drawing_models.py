import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from comic_workflow import comfyui, drawing_models


class PreloadTests(unittest.TestCase):
    def setUp(self):
        self.manager = comfyui.ComfyManager()
        self.app = SimpleNamespace(comfy=self.manager, model=Mock())
        self.process = SimpleNamespace(poll=lambda: None, preloaded_checkpoint=None)
        self.enterContext(patch.object(drawing_models, "installed", return_value=["base.safetensors"]))
        self.start = self.enterContext(patch.object(self.manager, "start", side_effect=self.start_process))
        self.stop_process = self.enterContext(patch.object(comfyui, "terminate_tree"))
        self.api = self.enterContext(patch.object(comfyui, "api", return_value={"prompt_id": "warm-job"}))
        self.wait = self.enterContext(patch.object(drawing_models, "wait_for_load"))

    def start_process(self, options):
        self.manager.process = self.process

    def load(self):
        drawing_models.preload(self.app, "base.safetensors", lambda msg: None, "job")

    def test_keep_after_draw_and_release_explicitly(self):
        self.load()
        self.manager.stop()
        self.load()
        self.start.assert_called_once()
        self.stop_process.assert_not_called()
        drawing_models.unload(self.app)
        self.stop_process.assert_called_once_with(self.process)
        self.assertIsNone(self.manager.preloaded)

    def test_failed_load_cleans_worker_and_allows_retry(self):
        self.wait.side_effect = [ValueError("load failed"), None]
        with self.assertRaisesRegex(ValueError, "load failed"):
            self.load()
        self.assertIsNone(self.manager.preloaded)
        self.stop_process.assert_called_once_with(self.process)
        self.load()
        self.assertEqual(self.manager.preloaded, "base.safetensors")

    def test_invalid_choice_and_busy_queue_keep_current_model(self):
        self.load()
        with self.assertRaisesRegex(ValueError, "已安装"):
            drawing_models.preload(self.app, "../unknown", Mock(), "job")
        self.api.return_value = {"queue_running": ["painting"]}
        with self.assertRaisesRegex(ValueError, "队列"):
            drawing_models.unload(self.app)
        self.stop_process.assert_not_called()

    def test_pause_reloads_freed_weights(self):
        def release_once():
            if self.wait.call_count == 1:
                self.process.preloaded_checkpoint = None
        with patch.object(drawing_models, "check_task", side_effect=release_once):
            self.load()
        self.assertEqual(self.wait.call_count, 2)
        self.assertEqual(self.manager.preloaded, "base.safetensors")


class OpenModelFolderTests(unittest.TestCase):
    def test_opens_diffusion_or_checkpoint_folder_by_model_type(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("os.startfile", create=True) as startfile:
                res_diff = drawing_models.open_folder("krea2_turbo_bf16.safetensors", root=root)
                self.assertEqual(res_diff["opened"], str((root / "models/diffusion_models").resolve()))
                self.assertTrue((root / "models/diffusion_models").is_dir())
                res_empty = drawing_models.open_folder("", root=root)
                self.assertEqual(res_empty["opened"], str((root / "models/diffusion_models").resolve()))
                res_ckpt = drawing_models.open_folder("NoobAI-XL-v1.1.safetensors", root=root)
                self.assertEqual(res_ckpt["opened"], str((root / "models/checkpoints").resolve()))
                self.assertTrue((root / "models/checkpoints").is_dir())
                self.assertEqual(startfile.call_count, 3)
            with self.assertRaises(ValueError):
                drawing_models.open_folder(123, root=root)
