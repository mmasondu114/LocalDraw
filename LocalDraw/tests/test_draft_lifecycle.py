import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, Mock, patch

from comic_workflow import engine as e
from comic_workflow.model import GENERATION_LOCK
from comic_workflow.repository import Repository
from comic_workflow.app_core import StudioApp as Application


class DraftLifecycleTests(unittest.TestCase):
    def exercise_draft(self, action, mode, failure=None):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            events = []

            def stop():
                self.assertTrue(GENERATION_LOCK.locked())
                events.append("unload")

            model = Mock()
            model.stop.side_effect = stop
            result = {"profile": {"name": "测试角色"}, "metrics": {"seconds": 1}}
            with (
                patch("comic_workflow.jobs.DATA", root),
                patch("comic_workflow.app_core.DATA", root),
                patch("comic_workflow.app_core.ModelManager", return_value=model),
                patch.object(Application, "prepare_language") as prepare,
                patch(
                    f"comic_workflow.app_core.creative.{action}", return_value=result
                ) as generate,
                patch("comic_workflow.jobs.threading.Thread") as thread,
            ):
                app = Application(Repository(root / "projects"))
                if failure == "prepare":
                    prepare.side_effect = RuntimeError("加载失败")
                if failure == "generate":
                    generate.side_effect = RuntimeError("生成失败")
                original_save = e.save

                def save(path, value):
                    if Path(path).name == "draft.json":
                        if failure == "save":
                            raise OSError("保存失败")
                        events.append("save")
                    original_save(path, value)

                with patch("comic_workflow.app_core.e.save", side_effect=save):
                    jid = app.action({"action": action, "mode": mode})["job"]
                    # Run the real Jobs worker synchronously so cleanup and lock
                    # release are complete before inspecting the final job.
                    thread.call_args.kwargs["target"]()

                job = app.jobs.get(jid)
                model.stop.assert_called_once_with()
                self.assertFalse(GENERATION_LOCK.locked())
                prepare.assert_called_once_with(
                    vision=action == "character_draft" and mode == "image",
                    update=ANY,
                )
                if failure:
                    self.assertEqual(job["status"], "failed", job)
                    self.assertEqual(
                        job["message"],
                        {
                            "prepare": "加载失败",
                            "generate": "生成失败",
                            "save": "保存失败",
                        }[failure],
                    )
                    self.assertEqual(events, ["unload"])
                else:
                    self.assertEqual(job["status"], "done", job)
                    self.assertEqual(job["result"], result)
                    self.assertEqual(events, ["save", "unload"])
                    self.assertEqual(
                        e.read(root / "creative" / jid / "draft.json"),
                        {"kind": action, **result},
                    )

    def test_completed_drafts_unload_after_preserving_results(self):
        for action, mode in [
            ("character_draft", "image"),
            ("character_draft", "text"),
            ("story_draft", "text"),
        ]:
            with self.subTest(action=action, mode=mode):
                self.exercise_draft(action, mode)

    def test_failed_drafts_unload_and_preserve_error(self):
        for action, mode in [
            ("character_draft", "image"),
            ("character_draft", "text"),
            ("story_draft", "text"),
        ]:
            for failure in ["prepare", "generate", "save"]:
                with self.subTest(action=action, mode=mode, failure=failure):
                    self.exercise_draft(action, mode, failure)
