"""CPU-only isolation checks: localhost, models, and process starts are mocked."""
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, Mock, patch

from comic_workflow import comfyui
from comic_workflow.installed_assets import models
from comic_workflow.paths import ROOT


class CopyIsolationTests(TestCase):
    def test_native_app_has_no_web_or_http_server(self):
        self.assertFalse((ROOT / "web").exists())
        self.assertFalse((ROOT / "src/comic_workflow/server.py").exists())
        self.assertFalse((ROOT / "src/comic_workflow/startup.py").exists())

    def test_foreign_comfy_never_receives_free(self):
        cfg = {"endpoint": "http://127.0.0.1:18188", "private_service": True}
        process = Mock()
        process.poll.return_value = None
        with patch.object(comfyui, "config", return_value=cfg), patch.object(comfyui, "request_json") as request:
            with patch.object(comfyui, "_private_process", None):
                with self.assertRaises(ValueError):
                    comfyui.api("/free", {"unload_models": True})
                request.assert_not_called()
            with patch.object(comfyui, "_private_process", process):
                request.return_value = {"system": {"argv": []}}
                with self.assertRaises(ValueError):
                    comfyui.api("/free", {"unload_models": True})
                self.assertEqual([c.args[0].rsplit("/", 1)[-1] for c in request.call_args_list], ["system_stats"])

    def test_private_comfy_command_and_asset_paths(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "runtime/ComfyUI-KreaMoss"
            source.mkdir(parents=True)
            (source / "main.py").touch()
            py = root / "app-python.exe"
            py.touch()
            (root / "config").mkdir()
            cfg = {"endpoint": "http://127.0.0.1:18188", "private_service": True,
                   "source_directory": str(source), "python_path": str(py)}
            assets = root / "existing-weights"
            (root / "config/installed-runtime.json").write_text(json.dumps({"models": str(assets)}))
            self.assertEqual(models(root), assets)
            manager = comfyui.ComfyManager()
            with patch.object(comfyui, "ROOT", root), patch.object(comfyui, "DATA", root / "data"), \
                 patch.object(comfyui, "config", return_value=cfg), \
                 patch.object(comfyui.socket, "socket", MagicMock()), \
                 patch.object(comfyui, "discover", side_effect=[{"ready": False}, {"ready": True}]), \
                 patch.object(comfyui.subprocess, "Popen") as popen, patch.object(comfyui, "attach_exit_job"), \
                 patch.object(comfyui, "_private_process", None):
                popen.return_value.poll.return_value = None
                try:
                    manager.start()
                    args = popen.call_args.args[0]
                    self.assertEqual(args[args.index("--port") + 1], "18188")
                    self.assertEqual(Path(args[args.index("--user-directory") + 1]), root / "data/comfy-user")
                    self.assertEqual(popen.call_args.kwargs["cwd"], source)
                    paths = (root / "config/comfy-model-paths.yaml").read_text(encoding="utf-8")
                    self.assertIn(json.dumps(str(assets)), paths)
                finally:
                    if manager.log:
                        manager.log.close()
