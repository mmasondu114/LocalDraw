import concurrent.futures
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from comic_workflow import model, paths
from comic_workflow.strata_model import StrataManager


class StrataPromptTests(unittest.TestCase):
    def test_page_redraw_64k_profile_matches_engine_limit(self):
        import json
        original = paths.settings()
        with paths.prompt_model("page_redraw"):
            cfg = paths.settings()
            self.assertEqual(cfg["context_size"], 65536)
            profile = json.loads(paths.local_path(cfg["strata_profile"]).read_text(encoding="utf-8"))
            for key in ("args", "args_written"):
                self.assertEqual(profile[key][profile[key].index("--max-context") + 1], "65536")
        self.assertEqual(paths.settings(), original)
        with paths.prompt_model():
            self.assertEqual(paths.settings()["context_size"], 65536)

    def test_prompt_override_is_scoped_and_resets_on_failure(self):
        original = paths.settings()
        with self.assertRaises(RuntimeError):
            with paths.prompt_model():
                self.assertEqual(paths.settings()["model_backend"], "strata")
                self.assertEqual(paths.settings()["context_size"], 65536)
                with concurrent.futures.ThreadPoolExecutor(1) as pool:
                    self.assertEqual(pool.submit(paths.settings).result(), original)
                raise RuntimeError("generation failed")
        self.assertEqual(paths.settings(), original)

    def test_native_token_count_keeps_system_separate(self):
        messages = [{"role": "system", "content": "Keep identities."},
                    {"role": "user", "content": "A rainy station."}]
        with paths.prompt_model(), patch.object(model, "request_json", return_value={"input_tokens": 42}) as call:
            self.assertEqual(model.count_messages(messages), 42)
        self.assertTrue(call.call_args.args[0].endswith("/v1/messages/count_tokens"))
        self.assertEqual(call.call_args.args[1]["system"], "Keep identities.")
        self.assertEqual(call.call_args.args[1]["messages"], messages[1:])

    def manager(self, path):
        return StrataManager({"model_port": 8094, "model_name": "qwen3.8-flash-next-iq3_s",
            "context_size": 65536, "model_files": [{"path": str(path), "size": 1}]})

    def test_http_health_without_loaded_model_is_not_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shard"
            path.write_bytes(b"x")
            manager = self.manager(path)
            health = {"service": "strata", "status": "ok", "loaded": False,
                      "model": manager.cfg["model_name"], "max_context": 65536}
            with patch.object(model, "request_json", return_value=health):
                self.assertFalse(manager.status()["ready"])
                health["loaded"] = True
                self.assertTrue(manager.status()["ready"])
                health["max_context"] = 32768
                self.assertFalse(manager.status()["ready"])

    def test_external_service_is_not_adopted_or_unloaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shard"
            path.write_bytes(b"x")
            manager = self.manager(path)
            with patch.object(model, "request_json", return_value={"service": "strata"}) as call:
                with self.assertRaisesRegex(ValueError, "独立服务"):
                    manager.start(Mock())
                manager.stop()
            self.assertTrue(all('/unload' not in c.args[0] for c in call.call_args_list))

    def test_owned_model_unloads_before_process_termination(self):
        manager = StrataManager({"model_port": 8094})
        manager.process = Mock()
        manager.process.poll.return_value = None
        events = []
        with patch.object(model, "request_json", side_effect=lambda *a, **k: events.append("unload") or {"status": "unloaded"}), \
             patch("comic_workflow.strata_model.terminate_tree", side_effect=lambda p: events.append("terminate")):
            manager.stop()
        self.assertEqual(events, ["unload", "terminate"])
        self.assertIsNone(manager.process)
