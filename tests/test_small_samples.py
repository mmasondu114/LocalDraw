import copy
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image
from comic_workflow import comfyui, drawing_tools, engine as e, art_presets
from comic_workflow.repository import Repository
from comic_workflow.character_binding import identity_hash


class SmallSampleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Repository(self.tmp.name)
        self.pid = self.repo.create(None, True)
        self.p = self.repo.get(self.pid)
        self.panel = self.p["pages"]["P001"]["panels"][0]
        self.p.setdefault("drawing_prompts", {})[self.panel["id"]] = {
            "panel_hash": e.digest(self.panel), "identity_hash": identity_hash(self.p, self.panel),
            "payload": {"positive": "anime woman at counter", "negative": "text",
                        "character_anchors": [], "visual_plan": {"version": 1}}}
        self.repo.get = lambda pid: copy.deepcopy(self.p)
        self.app = SimpleNamespace(repo=self.repo, model=Mock(), comfy=Mock(), prepare_language=Mock())
        self.request = {"base_hash": e.digest(self.p), "target": self.panel["id"],
                        "checkpoints": ["one.safetensors", "two.safetensors", "three.safetensors"], "count": 2}

    def test_same_seeds_all_models_only_one_load_and_no_auto_selection(self):
        before = copy.deepcopy(self.p)
        renders = []
        def render(*args, **kw):
            renders.append((args, kw))
            if len(renders) == 2:
                raise ValueError("fake failed image")
            return {"id": str(len(renders)), "images": []}
        events = []
        with patch.object(comfyui, "discover", return_value={"checkpoints": self.request["checkpoints"]}), \
             patch.object(comfyui, "run_batch", side_effect=render), \
             patch.object(drawing_tools, "prepare") as prepare:
            result = drawing_tools.small_batch(self.app, self.pid, self.request, events.append, "sample")
        self.assertEqual(len(renders), 6)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["status"], "done_with_errors")
        self.assertEqual([kw["render_options"]["seed"] for _, kw in renders], [2026091901, 2026091902] * 3)
        self.assertTrue(all(kw["review_required"] is False for _, kw in renders))
        self.app.comfy.start.assert_called_once()
        self.app.comfy.stop.assert_called_once()
        self.app.prepare_language.assert_not_called()
        prepare.assert_not_called()
        self.assertEqual(self.p, before)
        self.assertEqual(events[-1]["progress"]["completed"], 6)
        self.assertEqual(events[-1]["eta"]["remaining_seconds"], 0)
        self.assertEqual(comfyui.drawing_state(self.repo, self.pid)["selections"], {})

    def test_missing_model_and_stale_story_never_start_gpu(self):
        with patch.object(comfyui, "discover", return_value={"checkpoints": []}):
            with self.assertRaisesRegex(ValueError, "下载"):
                drawing_tools.small_batch(self.app, self.pid, self.request, Mock(), "sample")
        self.request["base_hash"] = "stale"
        with self.assertRaisesRegex(ValueError, "变化"):
            drawing_tools.small_batch(self.app, self.pid, self.request, Mock(), "sample")
        self.app.comfy.start.assert_not_called()
        self.app.prepare_language.assert_not_called()

    def test_old_prompt_is_used_without_automatic_upgrade(self):
        self.p["drawing_prompts"][self.panel["id"]]["payload"].pop("visual_plan")
        self.request["base_hash"] = e.digest(self.p)
        order = []
        self.app.comfy.start.side_effect = lambda: order.append("drawing")
        with patch.object(comfyui, "discover", return_value={"checkpoints": self.request["checkpoints"]}), \
             patch.object(comfyui, "run_batch", return_value={"id": "test", "images": []}), \
             patch.object(drawing_tools, "prepare", side_effect=lambda *a, **kw: order.append("adapt")) as prepare:
            drawing_tools.small_batch(self.app, self.pid, self.request, Mock(), "upgrade")
        prepare.assert_not_called()
        self.assertEqual(order, ["drawing"])

    def test_pending_submission_not_duplicated(self):
        with patch.object(comfyui, "discover", return_value={"checkpoints": self.request["checkpoints"]}), \
             patch.object(comfyui, "drawing_state", side_effect=[{"batches": []}, {"batches": [{"status": "uncertain"}]}]), \
             patch.object(comfyui, "run_batch", side_effect=ValueError("timeout")) as render:
            with self.assertRaisesRegex(ValueError, "timeout"):
                drawing_tools.small_batch(self.app, self.pid, self.request, Mock(), "sample")
        self.assertEqual(render.call_count, 1)
        self.app.comfy.stop.assert_called_once()

    def test_sample_model_overrides_preset_and_preserves_index(self):
        graphs = []
        buf = io.BytesIO()
        Image.new("RGB", (64, 64), "green").save(buf, format="PNG")
        def api(path, body=None):
            if path == "/prompt":
                graphs.append(body["prompt"])
                return {"prompt_id": "test", "node_errors": {}}
            return {"test": {"status": {"status_str": "success"}, "outputs": {"7": {"images": [{"filename": "test.png"}]}}}}
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=SimpleNamespace(read=lambda n: buf.getvalue()))
        opener.open.return_value.__exit__ = Mock(return_value=False)
        old = {"batches": [{"id": "old", "images": []}], "selections": {"old-panel": {"image": {"id": "chosen"}}}}
        e.save(self.repo.folder(self.pid) / "images/index.json", old)
        with patch.object(comfyui, "discover", return_value={"ready": True, "checkpoints": ["test.safetensors"]}), \
             patch.object(comfyui, "api", side_effect=api), \
             patch("comic_workflow.art_presets.get", return_value={"reference": True}), \
             patch("comic_workflow.drawing_settings.validate", side_effect=AssertionError("must not load preset")), \
             patch("comic_workflow.telemetry.websocket.create_connection", side_effect=ConnectionError("offline")), \
             patch("urllib.request.build_opener", return_value=opener):
            result = comfyui.run_batch(self.repo, self.pid, self.panel["id"], Mock(), "test.safetensors", 1,
                render_options={"diagnostic": "small_batch", "control": False, "reference": False, "seed": 42})
        self.assertEqual(result["checkpoint"], "test.safetensors")
        self.assertIsNone(result["art_preset_hash"])
        self.assertEqual(graphs[0]["1"]["inputs"]["ckpt_name"], "test.safetensors")
        after = comfyui.drawing_state(self.repo, self.pid)
        self.assertEqual(after["selections"], old["selections"])
        self.assertEqual(after["batches"][0], old["batches"][0])


class ComparisonDownloadTests(unittest.TestCase):
    def test_explicit_proxy_is_used_and_logs_are_saved_without_network(self):
        path = Path(__file__).resolve().parents[1] / "scripts/download_comparison_models.py"
        spec = importlib.util.spec_from_file_location("comparison_proxy", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            module.root = Path(tmp)
            fetch = Mock()
            self.assertEqual(module.main(["--proxy", "http://127.0.0.1:12345", "--model", "noobai-1.1"], fetch), 0)
            proxy_handler = next(h for h in fetch.call_args.kwargs["opener"].handlers if hasattr(h, "proxies"))
            self.assertEqual(proxy_handler.proxies["https"], "http://127.0.0.1:12345")
            logs = list((module.root / "data/logs").glob("download-models-*.log"))
            self.assertEqual(len(logs), 1)
            self.assertIn("显式使用本机 HTTP 代理", logs[0].read_text(encoding="utf-8"))

    def test_download_pins_hashes_continues_failure_and_list_is_offline(self):
        path = Path(__file__).resolve().parents[1] / "scripts/download_comparison_models.py"
        spec = importlib.util.spec_from_file_location("comparison_download", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        fetch = Mock(side_effect=[ValueError("offline fixture"), Path("two"), Path("three")])
        self.assertEqual(module.main([], fetch), 1)
        self.assertEqual(fetch.call_count, 3)
        for call in fetch.call_args_list:
            _, workers, url, size, sha, name = call.args
            self.assertEqual(workers, 12)
            self.assertTrue(call.kwargs["fallback_urls"][0].startswith("https://hf-mirror.com/"))
            self.assertNotIn("/main/", url)
            self.assertTrue(size > 6_000_000_000 and len(sha) == 64)
            self.assertTrue(url.endswith(name))
        offline = Mock(side_effect=AssertionError("must not download"))
        self.assertEqual(module.main(["--list"], offline), 0)
        offline.assert_not_called()


if __name__ == "__main__":
    unittest.main()
