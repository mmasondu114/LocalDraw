import unittest
from unittest.mock import Mock, patch

from comic_workflow.app_core import StudioApp as Application


class PauseModelTests(unittest.TestCase):
    def setUp(self):
        self.app = Application.__new__(Application)
        self.app.model = Mock(vision=True)
        self.app.comfy = Mock()
        self.app.comfy.process.poll.return_value = None

    @patch("comic_workflow.app_core.comfyui.api")
    @patch("comic_workflow.app_core.comfyui.discover", return_value={"ready": True})
    def test_unload_and_restore_language_with_same_vision_mode(self, discover, api):
        api.return_value = {"queue_running": [], "queue_pending": []}
        self.app.model.status.return_value = {"owned": True}
        restore = self.app.release_paused_models()
        api.assert_any_call("/free", {"unload_models": True, "free_memory": True})
        self.app.comfy.stop.assert_not_called()
        self.app.model.stop.assert_called_once()
        self.app.model.start.assert_not_called()
        update = Mock()
        restore(update)
        self.app.model.start.assert_called_once_with(vision=True, update=update)

    @patch("comic_workflow.app_core.comfyui.api")
    @patch("comic_workflow.app_core.comfyui.discover", return_value={"ready": True})
    def test_active_queue_is_not_unloaded(self, discover, api):
        api.return_value = {"queue_running": ["active"], "queue_pending": []}
        with self.assertRaises(ValueError):
            self.app.release_paused_models()
        api.assert_called_once_with("/queue")
        self.app.model.stop.assert_not_called()

    @patch("comic_workflow.app_core.comfyui.discover", return_value={"ready": False})
    def test_no_model_does_not_load_one_on_resume(self, discover):
        self.app.model.status.return_value = {"owned": False}
        self.assertIsNone(self.app.release_paused_models())
        self.app.model.stop.assert_not_called()
        self.app.model.start.assert_not_called()
