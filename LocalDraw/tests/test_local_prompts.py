import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from comic_workflow import engine as e, local_prompts as lp, model
from comic_workflow.repository import Repository
from comic_workflow.prompt_exchange import current_import, preview, save_import
from comic_workflow.app_core import StudioApp as Application
from comic_workflow.task_control import TaskCancelled


class LocalPromptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = Repository(self.root / "projects")
        self.pid = self.repo.create(None, True)
        self.before = self.repo.get(self.pid)
        self.doc = lp.export_storyboard(self.repo, self.pid)
        self.request = {"action": "fill_local_prompts", "draft_id": "draft-test",
                        "storyboard_hash": self.doc["storyboard_hash"]}
        self.app = SimpleNamespace(repo=self.repo, model=Mock(), prepare_language=Mock(), comfy=Mock())

    def response(self, prompt, context, schema, *args, **kwargs):
        row = context["current_panel"]
        return {"panel_id": row["panel_id"], "positive": "Medium shot. One person holds a book.",
                "negative": "text, watermark"}, {}

    def run_draft(self):
        return lp.generate_draft(self.app, self.pid, self.request, Mock(), "job-test")

    def test_generation_schema_rejects_runaway_negatives_and_non_english(self):
        def check(prompt, context, schema, *args, **kwargs):
            result, metrics = self.response(prompt, context, schema)
            e.jsonschema.validate(result, schema)
            e.jsonschema.validate({**result, "negative": "animal tail, watermark"}, schema)
            for changed in ({'negative': 'night, ' * 40}, {'negative': '中文'},
                            {'positive':'A lantern with Chinese text 中文'}):
                with self.assertRaises(e.jsonschema.ValidationError):
                    e.jsonschema.validate({**result, **changed}, schema)
            return result, metrics
        with patch.object(lp.creative, 'structured', side_effect=check):
            self.run_draft()

    def test_text_only_boards_supply_earlier_actions_without_future_events(self):
        rows = [{"panel_id": str(i), "storyboard": {"characters": [], "description": "", "shot": "",
                 "action": action, "state_before": {}}} for i, action in enumerate(
                     ["hangs coat on chair", "looks up", "opens door", "puts coat back on"])]
        context = lp.panel_context({"title": "Test", "panels": rows}, 2)
        self.assertEqual(context["earlier_actions"], [{"panel_id": "0", "action": "hangs coat on chair"}])
        self.assertEqual(context["previous_panel"]["action"], "looks up")
        rows[2]["storyboard"]["state_before"] = {"clothing": "shirt"}
        self.assertNotIn("earlier_actions", lp.panel_context({"title": "Test", "panels": rows}, 2))

    def test_confirmed_state_and_exclusions_reach_independent_panel_requests(self):
        project = self.repo.get(self.pid)
        project["brief"]["avoid"] = ["动物尾巴"]
        page = project["pages"]["P001"]
        wearing = {"C001.clothing": "white shirt and jacket"}
        removed = {"C001.clothing": "white shirt", "jacket.location": "chair"}
        page["state_in"] = wearing
        for panel in page["panels"]:
            panel["state_after"] = removed
        e.save(self.repo.folder(self.pid) / "project.json", project)
        document = lp.export_storyboard(self.repo, self.pid)
        context = lp.panel_context(document, len(page["panels"]) - 1)
        board = context["current_panel"]["storyboard"]
        self.assertEqual(context["avoid"], ["动物尾巴"])
        self.assertEqual(document["panels"][0]["storyboard"]["state_before"], wearing)
        self.assertEqual((board["state_before"], board["state_after"]), (removed, removed))
        self.assertEqual(board["props"], page["panels"][-1]["props"])
        drawing = model.build_context(project, "drawing_prompt", page["panels"][-1]["id"], "")
        self.assertEqual(drawing["state_before"], removed)

    def test_gpu_draft_preserves_story_and_confirmed_import_until_review(self):
        previous = copy.deepcopy(self.doc)
        for row in previous["panels"]:
            row.update(positive="Original confirmed prompt", negative="")
        check = preview(self.repo, self.pid, previous)
        imported = save_import(self.repo, self.pid, {"document": previous, "confirm": True,
                                                   "preview_hash": check["preview_hash"]})
        with patch.object(lp.creative, "structured", side_effect=self.response) as generate:
            result = self.run_draft()
        self.app.prepare_language.assert_called_once()
        self.assertEqual(set(self.app.prepare_language.call_args.kwargs), {"update"})
        self.app.model.start.assert_not_called()
        self.app.model.stop.assert_not_called()  # Jobs owns final idle release
        self.assertEqual(self.app.comfy.mock_calls, [])
        self.assertEqual(result["completed"], len(self.doc["panels"]))
        self.assertEqual(generate.call_count, len(self.doc["panels"]))
        self.assertEqual(self.repo.get(self.pid), self.before)
        self.assertEqual(current_import(self.repo, self.pid)["bundle"]["id"], imported["id"])
        draft = lp.current_draft(self.repo, self.pid)["draft"]
        self.assertEqual(draft["status"], "complete")
        self.assertEqual(draft["device"], "gpu")
        for original, generated in zip(self.doc["panels"], draft["document"]["panels"]):
            self.assertEqual(original["storyboard"], generated["storyboard"])
            self.assertEqual(original["panel_id"], generated["panel_id"])
        self.assertTrue(preview(self.repo, self.pid, draft["document"])["valid"])

    def test_partial_failure_keeps_completed_panels_and_resume_skips_them(self):
        count = 0
        def fail_second(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                raise ValueError("Disconnected")
            return self.response(*args, **kwargs)
        with patch.object(lp.creative, "structured", side_effect=fail_second), self.assertRaises(ValueError):
            self.run_draft()
        draft = lp.current_draft(self.repo, self.pid)["draft"]
        self.assertEqual(draft["completed"], 1)
        self.assertEqual(draft["status"], "failed")
        # Resume a draft written before panel state was included in exports.
        for row in draft["document"]["panels"]:
            row["storyboard"].pop("state_before")
        saved_prompt = draft["document"]["panels"][0]["positive"]
        e.save(self.repo.folder(self.pid) / "local-prompts" / draft["id"] / "draft.json", draft)
        self.app.model.stop.assert_not_called()  # Jobs owns final idle release
        with patch.object(lp.creative, "structured", side_effect=self.response) as generate:
            self.run_draft()
        self.assertEqual(generate.call_count, len(self.doc["panels"]) - 1)
        for call in generate.call_args_list:
            self.assertIn("state_before", call.args[1]["current_panel"]["storyboard"])
        resumed = lp.current_draft(self.repo, self.pid)["draft"]
        self.assertEqual(resumed["document"]["panels"][0]["positive"], saved_prompt)
        self.assertIsNone(current_import(self.repo, self.pid)["bundle"])

    def test_load_failure_stale_story_and_invalid_output_never_apply(self):
        self.app.prepare_language.side_effect = RuntimeError("load failed")
        with self.assertRaises(RuntimeError):
            self.run_draft()
        self.app.model.stop.assert_not_called()  # Jobs owns final idle release
        self.app.prepare_language.side_effect = None
        for value in ["", "中文提示词"]:
            bad = {"panel_id": self.doc["panels"][0]["panel_id"], "positive": value, "negative": ""}
            with patch.object(lp.creative, "structured", return_value=(bad, {})), self.assertRaises(Exception):
                self.run_draft()
            self.assertEqual(lp.current_draft(self.repo, self.pid)["draft"]["completed"], 0)
        changed = self.repo.get(self.pid)
        changed["pages"]["P001"]["panels"][0]["main_instant"] = "A changed action."
        e.save(self.repo.folder(self.pid) / "project.json", changed)
        self.assertTrue(lp.current_draft(self.repo, self.pid)["stale"])
        self.app.model.reset_mock()
        self.app.prepare_language.reset_mock()
        with self.assertRaises(ValueError):
            self.run_draft()
        self.app.model.start.assert_not_called()
        self.app.prepare_language.assert_not_called()
        self.assertIsNone(current_import(self.repo, self.pid)["bundle"])

    def test_cancellation_saves_partial_draft_and_unloads(self):
        with patch.object(lp.creative, "structured", side_effect=TaskCancelled), self.assertRaises(TaskCancelled):
            self.run_draft()
        self.assertEqual(lp.current_draft(self.repo, self.pid)["draft"]["status"], "cancelled")
        self.app.model.stop.assert_not_called()  # Jobs owns final idle release

    def test_action_queues_work_with_resumable_snapshot_without_loading_model(self):
        app = Application.__new__(Application)
        app.repo, app.jobs = self.repo, Mock()
        app.jobs.submit.return_value = "queued"
        self.assertEqual(app.action({"action": "fill_local_prompts", "project": self.pid}), {"job": "queued"})
        request = app.jobs.submit.call_args.kwargs["request"]
        self.assertEqual(request["storyboard_hash"], self.doc["storyboard_hash"])
        self.assertEqual(request["action"], "fill_local_prompts")
        self.assertIsNone(lp.current_draft(self.repo, self.pid)["draft"])


class GpuLifecycleTests(unittest.TestCase):
    def test_default_launch_uses_gpu_with_subprocess_mocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            weights = root / "model.gguf"
            weights.write_bytes(b"test")
            cfg = {**model.raw_settings(), "model_path": str(weights), "model_size": 4, "model_sha256": "hash"}
            manager = model.ModelManager()
            gpu = Mock()
            gpu.poll.return_value = None
            manager.status = lambda: {"owned": manager.process is not None, "ready": manager.process is not None}
            with patch.object(model, "settings", return_value=cfg), patch.object(model, "DATA", root), \
                 patch("comic_workflow.strata_model.DATA", root), patch.object(model, "filehash", return_value="hash"), \
                 patch.object(model, "terminate_tree"), patch.object(model, "close_job"), \
                 patch.object(model, "attach_exit_job", return_value=None), \
                 patch.object(model.subprocess, "Popen", return_value=gpu) as launch:
                try:
                    manager.start()
                    args = launch.call_args.args[0]
                    self.assertEqual(args[args.index("--gpu-layers") + 1], "all")
                    self.assertEqual(args[args.index("--device") + 1], "CUDA0")
                    self.assertEqual(args[args.index("--flash-attn") + 1], "on")
                    self.assertNotIn("--no-op-offload", args)
                    self.assertNotIn("--no-kv-offload", args)
                    self.assertIn("LLAMA_API_KEY", launch.call_args.kwargs["env"])
                    manager.start()
                    self.assertEqual(launch.call_count, 1)
                finally:
                    manager.stop()

    def test_preparation_frees_idle_drawing_weights_before_gpu_load(self):
        app = Application.__new__(Application)
        app.model = Mock()
        app.comfy = Mock()
        app.comfy.process.poll.return_value = None
        events = Mock()
        events.attach_mock(app.model.start, "start")
        with patch("comic_workflow.app_core.comfyui.discover", return_value={"ready": True}), \
             patch("comic_workflow.app_core.comfyui.api", return_value={"queue_running": [], "queue_pending": []}) as api:
            events.attach_mock(api, "api")
            update = Mock()
            app.prepare_language(update=update)
            self.assertEqual([call[0] for call in events.mock_calls], ["api", "api", "start"])
            api.assert_any_call("/free", {"unload_models": True, "free_memory": True})
            app.model.start.assert_called_once_with(vision=False, update=update)

    def test_preparation_does_not_load_qwen_while_drawing_is_busy(self):
        app = Application.__new__(Application)
        app.model = Mock()
        with patch("comic_workflow.app_core.comfyui.discover", return_value={"ready": True}), \
             patch("comic_workflow.app_core.comfyui.api", return_value={"queue_running": ["active"], "queue_pending": []}) as api:
            with self.assertRaises(ValueError):
                app.prepare_language()
            api.assert_called_once_with("/queue")
        app.model.start.assert_not_called()
