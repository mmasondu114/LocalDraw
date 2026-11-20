"""Offline checks for analysis selection, engine parameters and private idle release."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from comic_workflow import analysis_models as profiles, paths
from comic_workflow.jobs import Jobs
from comic_workflow.model import ModelManager
from comic_workflow.server import Application
from comic_workflow.strata_model import StrataManager
from comic_workflow.task_control import checkpoint


class AnalysisModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        for module in (profiles, paths):
            handle = patch.object(module, "DATA", self.data)
            handle.start()
            self.addCleanup(handle.stop)
        paths.use_vision_model(False)
        self.addCleanup(paths.use_vision_model, False)

    def test_preference_without_loading(self):
        manager = ModelManager()
        cfg = paths.settings()
        self.assertEqual((cfg["model_backend"], cfg["context_size"], cfg["model_port"]),
                         ("strata", 65536, 18094))
        self.assertTrue(all(r["available"] for r in profiles.choices()["options"]))
        with patch.object(manager, "start", side_effect=AssertionError):
            profiles.select_model("huihui_27b", manager)
            with paths.prompt_model("page_redraw"):
                cfg = paths.settings()
                self.assertEqual((cfg["model_port"], cfg["context_size"]), (18091, 32768))
            self.assertEqual(profiles.choices()["selected"], "huihui_27b")
        profiles.select_model("flash_next_64k", manager)
        paths.use_vision_model(True)
        self.assertEqual(paths.settings()["model_port"], 18091)

    def test_missing_shard_and_occupied_selection(self):
        manager = ModelManager()
        profiles.select_model("huihui_27b", manager)
        cfg = paths.raw_settings()
        cfg["prompt_model"]["model_files"][0]["path"] = str(self.data / "missing-shard")
        with patch.object(profiles, "raw_settings", return_value=cfg):
            flash = profiles.choices()["options"][0]
            self.assertFalse(flash["available"])
            self.assertIn("missing-shard", flash["reason"])
            with self.assertRaisesRegex(ValueError, "missing-shard"):
                profiles.select_model("flash_next_64k", manager)
        manager.process = Mock()
        manager.process.poll.return_value = None
        with self.assertRaisesRegex(ValueError, "已加载"):
            profiles.select_model("flash_next_64k", manager)
        self.assertEqual(profiles.choices()["selected"], "huihui_27b")

    def test_real_profile_and_simulated_launch(self):
        cfg = {**paths.settings(), "min_free_ram_gib": 0}
        manager = StrataManager(cfg)
        mock_process = Mock()
        mock_process.poll.return_value = None
        states = [{"ready":False, "owned":False, "file_size_ok":True}, {"ready":True}]
        with patch("comic_workflow.strata_model.DATA", self.data), patch.object(manager, "status", side_effect=states), \
             patch("comic_workflow.model.request_json", side_effect=OSError("offline")), \
             patch("comic_workflow.strata_model.subprocess.Popen", return_value=mock_process) as start, \
             patch("comic_workflow.strata_model.attach_exit_job"):
            manager.start(lambda _:None)
        try:
            args = start.call_args.args[0]
            generated = json.loads(Path(args[args.index("--profile") + 1]).read_text(encoding="utf-8"))
            self.assertEqual(args[args.index("--port")+1], "18094")
            for k in ("args","args_written"):
                run = generated[k]
                self.assertEqual(run[run.index("--max-context")+1], "65536")
            self.assertIn("STRATA_API_KEY", start.call_args.kwargs["env"])
            self.assertTrue(profiles.worker_headers("http://127.0.0.1:18094/v1/chat/completions"))
            self.assertFalse(profiles.worker_headers("http://127.0.0.1:8094/v1/chat/completions"))
        finally:
            manager.log.close()
            profiles.release_key(18094)

    def test_busy_or_external_remains_untouched(self):
        manager = ModelManager()
        with patch("comic_workflow.model.request_json", side_effect=AssertionError):
            self.assertEqual(manager.unload_if_idle()["status"],"already_unloaded")
        manager.strata = StrataManager(paths.settings())
        manager.strata.process = Mock()
        manager.strata.process.poll.return_value = None
        with patch("comic_workflow.strata_model.terminate_tree") as stop, \
             patch("comic_workflow.model.request_json", side_effect=ValueError("409 busy")):
            self.assertEqual(manager.unload_if_idle()["status"],"deferred")
            stop.assert_not_called()
        worker = manager.strata.process
        with patch("comic_workflow.strata_model.terminate_tree") as stop, \
             patch("comic_workflow.model.request_json", return_value={"status":"unloaded"}) as post:
            self.assertEqual(manager.unload_if_idle()["status"],"unloaded")
            stop.assert_called_once_with(worker)
            post.assert_called_once_with("http://127.0.0.1:18094/unload", {}, timeout=30)
        manager.process = Mock()
        manager.process.poll.return_value = None
        manager.worker_port = 18091
        with patch.object(manager, "stop") as stop:
            for slots in ([{"is_processing":True}], [], [{}]):
                with patch("comic_workflow.model.request_json", return_value=slots):
                    self.assertEqual(manager.unload_if_idle()["status"],"deferred")
            stop.assert_not_called()
            with patch("comic_workflow.model.request_json", return_value=[{"is_processing":False}]):
                self.assertEqual(manager.unload_if_idle()["status"],"unloaded")
            stop.assert_called_once()

    def test_queue_active_and_all_terminal_paths(self):
        app = Application.__new__(Application)
        app.model = Mock()
        app.model.owns_live_worker.return_value = True
        app.model.unload_if_idle.return_value = {"status":"unloaded"}
        app.night_queue = SimpleNamespace(lock=threading.RLock(),state={"items":[]})
        with patch("comic_workflow.jobs.DATA", self.data):
            app.jobs = Jobs(on_finish=app.clear_finished_context)
        current = {"id":"current","label":"分析"}
        for other in [{"status":"waiting"}, {"status":"running","job":"another"}]:
            app.night_queue.state["items"] = [other]
            self.assertEqual(app.clear_finished_context(current)["status"],"deferred")
        app.model.unload_if_idle.assert_not_called()
        app.night_queue.state["items"] = []
        for expected in ("done","failed","cancelled"):
            started, proceed = threading.Event(), threading.Event()
            def work(update, jid):
                started.set()
                if expected == "failed":
                    raise ValueError("model failure")
                if expected == "cancelled":
                    proceed.wait(5)
                    checkpoint()
                return {}
            jid=app.jobs.submit("分析",work)
            self.assertTrue(started.wait(5))
            if expected == "cancelled":
                app.jobs.control(jid,"cancel",confirm=True)
                proceed.set()
            deadline=time.monotonic()+5
            while jid in app.jobs.controls and time.monotonic()<deadline:
                time.sleep(.02)
            self.assertEqual(app.jobs.get(jid)["status"],expected)
            self.assertEqual(app.jobs.get(jid)["context_cleanup"]["status"],"unloaded")
        self.assertEqual(app.model.unload_if_idle.call_count,3)

    def test_busy_release_retries_only_same_private_worker(self):
        app = Application.__new__(Application)
        app.model = Mock()
        owner = app.model.owned_worker.return_value
        app.model.owns_live_worker.return_value = True
        app.model.unload_if_idle.side_effect = [{"status":"deferred"}, {"status":"unloaded"}]
        app.night_queue = SimpleNamespace(lock=threading.RLock(),state={"items":[]})
        app.jobs = SimpleNamespace(lock=threading.RLock(), controls={})
        app.idle_release_timer = None
        with patch("comic_workflow.server.threading.Timer") as timer:
            app.clear_finished_context({"id":"last","label":"分析"})
            self.assertIs(app.pending_idle_worker, owner)
            delay, retry = timer.call_args.args
            self.assertGreater(delay, 0)
            # Neither a replacement nor a new connection can be torn down by the timer.
            app.model.owned_worker.return_value = Mock()
            retry()
            self.assertEqual(app.model.unload_if_idle.call_count, 1)
            app.model.owned_worker.return_value = owner
            retry()
            self.assertEqual(app.model.unload_if_idle.call_count, 2)
            self.assertIsNone(app.pending_idle_worker)

    def test_external_comfy_has_no_free_request(self):
        app = Application.__new__(Application)
        app.comfy = SimpleNamespace(process=None)
        with patch("comic_workflow.comfyui.discover", return_value={"ready": True}), \
             patch("comic_workflow.comfyui.api", return_value={"queue_running":[],"queue_pending":[]}) as api:
            app.release_drawing_cache()
            api.assert_called_once_with("/queue")


if __name__ == "__main__":
    unittest.main()

