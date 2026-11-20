import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from comic_workflow.jobs import Jobs
from comic_workflow.model import GENERATION_LOCK
from comic_workflow.night_queue import NightQueue
from comic_workflow.repository import Repository


class NightQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Repository(Path(self.tmp.name) / "projects")
        self.addCleanup(self.repo._temporary.cleanup)
        with patch("comic_workflow.jobs.DATA", Path(self.tmp.name)):
            jobs = Jobs()
        self.app = SimpleNamespace(repo=self.repo, jobs=jobs)
        self.queue = NightQueue(self.app)
        self.addCleanup(self.queue.close)

    def finish(self):
        deadline = time.monotonic() + 3
        while self.app.jobs.controls and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(self.app.jobs.controls)

    def add(self, name):
        return self.queue.add("work", name, {"action": "draw_imported", "targets": [name]})["queued"]

    def test_sequential_execution_and_immutable_settings(self):
        request = {"action": "draw_imported", "targets": ["first"], "loras": ["old"]}
        self.queue.add("work", "first", request)
        request["loras"].append("new")
        self.add("second")
        events = []
        self.queue.run_item = lambda item, *_: events.append(item["label"]) or {}
        self.queue.state["running"] = True
        self.queue.tick(); self.finish()
        self.queue.tick(); self.finish()
        self.queue.tick()
        self.assertEqual(events, ["first", "second"])
        self.assertEqual([i["status"] for i in self.queue.snapshot()["items"]], ["done", "done"])
        self.assertFalse(self.queue.state["running"])
        self.assertEqual(self.queue.state["items"][0]["request"]["loras"], ["old"])

    def test_failure_stops_or_continues_as_configured(self):
        self.add("broken"); self.add("next")
        def fail(*_):
            raise ValueError("broken model")
        self.queue.run_item = fail
        self.queue.state["running"] = True
        self.queue.tick(); self.finish(); self.queue.tick()
        self.assertEqual(self.queue.state["items"][0]["status"], "failed")
        self.assertEqual(self.queue.state["items"][1]["status"], "waiting")
        self.assertFalse(self.queue.state["running"])
        self.queue.state.update(running=True, stop_on_error=False)
        self.queue.run_item = lambda *_: {"errors": ["one image failed"]}
        self.queue.tick(); self.finish(); self.queue.tick()
        self.assertEqual(self.queue.state["items"][1]["status"], "partial")

    def test_restart_keeps_waiting_but_does_not_autorun(self):
        self.add("first"); self.add("second")
        self.queue.state["items"][0].update(status="running", job="missing")
        self.queue.state["running"] = True
        self.queue.save()
        restored = NightQueue(self.app)
        self.assertFalse(restored.state["running"])
        self.assertEqual([i["status"] for i in restored.state["items"]], ["interrupted", "waiting"])

    def test_reorder_remove_and_pause_pending(self):
        first, second = self.add("first"), self.add("second")
        self.queue.control({"action": "reorder_queue", "items": [second, first]})
        self.assertEqual(self.queue.state["items"][0]["id"], second)
        with self.assertRaises(ValueError):
            self.queue.control({"action": "reorder_queue", "items": [first, first]})
        self.queue.control({"action": "remove_queue_item", "item": first})
        self.queue.control({"action": "pause_queue"})
        self.queue.run_item = lambda *_: self.fail("must not run")
        self.queue.tick()
        self.assertEqual(self.queue.state["items"][0]["status"], "waiting")

    def test_clear_history_preserves_waiting_and_problem_tasks(self):
        for name in ["waiting", "done", "cancelled", "failed", "partial"]:
            self.add(name)
            self.queue.state["items"][-1]["status"] = name
        self.queue.control({"action":"clear_queue_history"})
        self.assertEqual([i["status"] for i in self.queue.state["items"]], ["waiting","failed","partial"])

    def test_rename_persists_without_mutating_saved_settings(self):
        item_id=self.add("original")
        original=self.queue.state["items"][0]["request"]
        self.queue.control({"action":"rename_queue_item","item":item_id,"task_name":"  第一页 · 夜景候选  "})
        restored=NightQueue(self.app);self.addCleanup(restored.close)
        self.assertEqual(restored.snapshot()["items"][0]["label"],"第一页 · 夜景候选")
        self.assertTrue(restored.snapshot()["can_rename"])
        self.assertIs(self.queue.state["items"][0]["request"],original)
        for value in ["", " "*3, "x"*81, "line\nbreak", 12]:
            with self.subTest(value=value),self.assertRaises(ValueError):
                self.queue.control({"action":"rename_queue_item","item":item_id,"task_name":value})
        with patch.object(self.queue,"save",side_effect=OSError("disk full")),self.assertRaises(OSError):
            self.queue.control({"action":"rename_queue_item","item":item_id,"task_name":"not saved"})
        self.assertEqual(self.queue.state["items"][0]["label"],"第一页 · 夜景候选")

    def test_save_failure_does_not_start_or_silently_keep_new_work(self):
        self.add("first")
        with patch.object(self.queue,"save",side_effect=OSError("disk full")), \
             patch("comic_workflow.night_queue.threading.Thread"):
            with self.assertRaises(OSError):
                self.queue.control({"action":"start_queue"})
            self.assertFalse(self.queue.state["running"])
            with self.assertRaises(OSError):
                self.add("unsaved")
            self.assertEqual(len(self.queue.state["items"]),1)
            self.queue.state["running"]=True
            with patch.object(self.app.jobs,"submit") as submit, self.assertRaises(OSError):
                self.queue.tick()
            submit.assert_not_called()
            self.assertEqual(self.queue.state["items"][0]["status"],"waiting")

    def test_waits_for_other_gpu_work(self):
        self.add("first")
        self.queue.state["running"] = True
        GENERATION_LOCK.acquire()
        try:
            self.queue.tick()
            self.assertEqual(self.queue.state["items"][0]["status"], "waiting")
        finally:
            GENERATION_LOCK.release()

    def test_task_started_during_submission_keeps_queued_task_waiting(self):
        self.add("first")
        self.queue.state["running"] = True
        with patch.object(self.app.jobs, "submit", side_effect=ValueError("已有模型任务进行中，请等待完成")):
            self.queue.tick()
        self.assertEqual(self.queue.state["items"][0]["status"], "waiting")
        self.assertTrue(self.queue.state["running"])

    def test_retry_preserves_frozen_request_and_completed_item_cannot_retry(self):
        item_id = self.add("first")
        item = self.queue.state["items"][0]
        item.update(status="partial", finished="yesterday")
        self.queue.control({"action": "retry_queue_item", "item": item_id})
        self.assertEqual(item["status"], "waiting")
        self.assertEqual(item["request"]["targets"], ["first"])
        self.assertNotIn("finished", item)
        item["status"] = "done"
        with self.assertRaises(ValueError):
            self.queue.control({"action": "retry_queue_item", "item": item_id})

    def test_scheduled_start_waits_for_time_and_pause_cancels_start(self):
        self.add("later")
        self.queue.run_item = lambda *_: {}
        with patch("comic_workflow.night_queue.threading.Thread"):
            self.queue.control({"action": "start_queue", "start_at": time.time() + 3600})
        self.queue.tick()
        self.assertEqual(self.queue.state["items"][0]["status"], "waiting")
        self.queue.control({"action": "pause_queue"})
        self.queue.state["start_at"] = time.time() - 1
        self.queue.tick()
        self.assertEqual(self.queue.state["items"][0]["status"], "waiting")
        self.queue.state["running"] = True
        self.queue.tick(); self.finish(); self.queue.tick()
        self.assertEqual(self.queue.state["items"][0]["status"], "done")
        with self.assertRaises(ValueError):
            self.queue.control({"action": "start_queue", "start_at": "tomorrow"})

    def test_interrupted_retry_with_missing_job_preserves_frozen_run(self):
        item_id=self.add("interrupted")
        item=self.queue.state["items"][0]
        item.update(status="interrupted",job="missing",finished="old")
        item["request"]["run_key"]="same-run"
        request=item["request"]
        result=self.queue.control({"action":"retry_queue_item","item":item_id})
        self.assertTrue(result["can_recover_interrupted"])
        self.assertEqual(item["status"],"waiting")
        self.assertIs(item["request"],request)
        self.assertEqual(item["request"]["run_key"],"same-run")
        self.assertFalse(result["running"])
        self.assertNotIn("finished",item)

    def test_retry_does_not_replace_live_paused_or_unreadable_job(self):
        item_id=self.add("interrupted")
        item=self.queue.state["items"][0]
        item.update(status="interrupted",job="original")
        for status in ["running","pausing","cancelling","paused","resumed"]:
            with self.subTest(status=status), patch.object(self.app.jobs,"get",return_value={"status":status}):
                with self.assertRaises(ValueError):
                    self.queue.control({"action":"retry_queue_item","item":item_id})
            self.assertEqual(item["status"],"interrupted")
        self.app.jobs.controls["original"]={}
        with patch.object(self.app.jobs,"get",side_effect=FileNotFoundError):
            with self.assertRaises(ValueError):
                self.queue.control({"action":"retry_queue_item","item":item_id})
        self.app.jobs.controls.clear()
        with patch.object(self.app.jobs,"get",side_effect=ValueError("corrupt job record")):
            with self.assertRaisesRegex(ValueError,"corrupt job record"):
                self.queue.control({"action":"retry_queue_item","item":item_id})
        self.assertEqual(item["status"],"interrupted")

    def test_snapshot_exposes_progress_but_not_large_reference_payloads(self):
        self.add("first")
        item = self.queue.state["items"][0]
        item.update(status="running", job="live")
        item["request"]["references_by_panel"] = {"image": "large-payload"}
        job = {"status": "paused", "message": "waiting", "progress": {"completed": 2, "total": 4}}
        with patch.object(self.app.jobs, "get", return_value=job):
            snapshot = self.queue.snapshot()["items"][0]
        self.assertEqual(snapshot["progress"]["completed"], 2)
        self.assertEqual(snapshot["job_status"], "paused")
        self.assertNotIn("references_by_panel", snapshot["request"])

    def test_tracks_resumed_job_before_starting_next(self):
        self.add("first"); self.add("next")
        item = self.queue.state["items"][0]
        item.update(status="running", job="original")
        self.queue.state["running"] = True
        records = {"original": {"status": "resumed", "resumed_as": "continued"},
                   "continued": {"status": "paused"}}
        with patch.object(self.app.jobs, "get", side_effect=lambda jid: records[jid]):
            self.queue.tick()
        self.assertEqual(item["job"], "continued")
        self.assertEqual(self.queue.state["items"][1]["status"], "waiting")

    def test_restart_observes_paused_job_and_reconciles_already_finished_job(self):
        self.add("first"); self.add("next")
        self.queue.state["items"][0].update(status="running", job="saved")
        self.queue.save()
        job={"id":"saved","status":"paused","label":"first","created":"1"}
        self.app.jobs.save("saved",job)
        restored=NightQueue(self.app);self.addCleanup(restored.close)
        self.assertEqual(restored.state["items"][0]["status"],"running")
        self.assertEqual(restored.snapshot()["items"][0]["job_status"],"paused")
        self.assertFalse(restored.state["running"])
        self.app.jobs.save("saved",{**job,"status":"done","result":{}})
        restored.tick()
        self.assertEqual(restored.state["items"][0]["status"],"done")
        self.assertEqual(restored.state["items"][1]["status"],"waiting")

    def test_prompt_queue_can_resume_through_normal_job_controls_after_restart(self):
        from comic_workflow.server import Application
        request={"action":"prompt_batch","base_hash":"unchanged","run_key":"frozen-run"}
        self.app.jobs.save("saved",{"id":"saved","status":"paused","label":"prompt", "created":"1",
                                     "project":"work","request":request})
        with patch("comic_workflow.quick_sample.run",return_value={}) as run:
            result=Application.action(self.app,{"action":"resume_job","job":"saved"})
            self.finish()
        self.assertEqual(self.app.jobs.get(result["job"])["status"],"done")
        self.assertEqual(run.call_args.args[1],"work")
        self.assertEqual(run.call_args.args[2],request)


if __name__ == "__main__":
    unittest.main()
