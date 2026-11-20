import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from comic_workflow import engine as e
from comic_workflow.jobs import Jobs
from comic_workflow.model import GENERATION_LOCK
from comic_workflow.task_control import checkpoint


class TaskControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        with patch("comic_workflow.jobs.DATA", self.data):
            self.jobs = Jobs()
        self.entered = threading.Event()
        self.finish_current = threading.Event()
        self.saved = []
        self.cleaned = threading.Event()

    def work(self, update, jid):
        try:
            self.entered.set()
            self.finish_current.wait(3)
            self.saved.append("first")
            update({"progress": {"completed": 1, "total": 2}})
            checkpoint()
            self.saved.append("second")
            return {"saved": list(self.saved)}
        finally:
            self.cleaned.set()

    def wait_status(self, jid, status):
        until = time.monotonic() + 3
        while time.monotonic() < until:
            if self.jobs.get(jid)["status"] == status:
                return
            time.sleep(.01)
        self.fail(self.jobs.get(jid))

    def finish_worker(self, jid):
        self.finish_current.set()
        if jid in self.jobs.controls:
            self.jobs.control(jid, "cancel", confirm=True)
        until = time.monotonic() + 3
        while jid in self.jobs.controls and time.monotonic() < until:
            time.sleep(.01)

    def start_work(self):
        jid = self.jobs.submit("test", self.work, "project")
        self.addCleanup(self.finish_worker, jid)
        self.assertTrue(self.entered.wait(2))
        return jid

    def test_pause_saves_current_unit_and_resume_does_not_repeat_it(self):
        jid = self.start_work()
        self.jobs.control(jid, "pause")
        self.assertEqual(self.jobs.get(jid)["status"], "pausing")
        self.finish_current.set()
        self.wait_status(jid, "paused")
        self.assertEqual(self.saved, ["first"])
        self.assertFalse(self.cleaned.is_set())
        with self.assertRaises(ValueError):
            self.jobs.submit("another", lambda *_: None)
        self.jobs.control(jid, "resume")
        self.wait_status(jid, "done")
        self.assertEqual(self.saved, ["first", "second"])
        self.assertTrue(self.cleaned.is_set())

    def test_cancel_requires_confirmation_and_never_retries(self):
        jid = self.start_work()
        with self.assertRaisesRegex(ValueError, "二次确认"):
            self.jobs.control(jid, "cancel")
        self.assertEqual(self.jobs.get(jid)["status"], "running")
        self.jobs.control(jid, "cancel", confirm=True)
        self.finish_current.set()
        self.wait_status(jid, "cancelled")
        self.assertEqual(self.saved, ["first"])
        self.assertTrue(self.cleaned.is_set())
        self.finish_worker(jid)
        self.assertFalse(GENERATION_LOCK.locked())
        with self.assertRaises(ValueError):
            self.jobs.control(jid, "resume")

    def test_cancel_while_paused_wakes_worker_and_runs_cleanup(self):
        jid = self.start_work()
        self.jobs.control(jid, "pause")
        self.finish_current.set()
        self.wait_status(jid, "paused")
        self.jobs.control(jid, "cancel", confirm=True)
        self.wait_status(jid, "cancelled")
        self.assertTrue(self.cleaned.is_set())
        self.assertEqual(self.saved, ["first"])

    def test_pause_unloads_after_save_and_restores_before_next_unit(self):
        events = []
        def release():
            events.append(("unload", list(self.saved)))
            return lambda update: events.append(("reload", list(self.saved)))
        self.jobs.on_pause = release
        jid = self.start_work()
        self.jobs.control(jid, "pause")
        self.assertEqual(events, [])
        self.finish_current.set()
        self.wait_status(jid, "paused")
        self.jobs.control(jid, "pause")
        self.assertEqual(events, [("unload", ["first"])])
        self.jobs.control(jid, "resume")
        self.wait_status(jid, "done")
        self.assertEqual(events, [("unload", ["first"]), ("reload", ["first"])])
        self.assertEqual(self.saved, ["first", "second"])

    def test_cancel_during_unload_remains_responsive_and_skips_reload(self):
        unloading, released = threading.Event(), threading.Event()
        restored = []
        def release():
            unloading.set()
            released.wait(3)
            return lambda update: restored.append(True)
        self.jobs.on_pause = release
        jid = self.start_work()
        self.jobs.control(jid, "pause")
        self.finish_current.set()
        self.assertTrue(unloading.wait(2))
        try:
            self.jobs.control(jid, "cancel", confirm=True)
            self.assertEqual(self.jobs.get(jid)["status"], "cancelling")
        finally:
            released.set()
        self.wait_status(jid, "cancelled")
        self.assertEqual(restored, [])
        self.assertEqual(self.saved, ["first"])

    def test_unload_failure_keeps_task_paused_and_resumable(self):
        def release():
            raise RuntimeError("queue busy")
        self.jobs.on_pause = release
        jid = self.start_work()
        self.jobs.control(jid, "pause")
        self.finish_current.set()
        self.wait_status(jid, "paused")
        self.assertEqual(self.jobs.get(jid)["pause_warning"], "queue busy")
        self.jobs.control(jid, "resume")
        self.wait_status(jid, "done")
        self.assertEqual(self.saved, ["first", "second"])

    def saved_job(self, status="running"):
        e.save(self.jobs.root / "saved.json", {
            "id": "saved", "project": "project", "label": "draw", "created": "1",
            "status": status, "request": {"candidate_count": 16},
            "progress": {"completed": 258, "total": 480},
        })
        with patch("comic_workflow.jobs.DATA", self.data):
            self.jobs = Jobs()

    def test_restart_stays_paused_until_explicit_resume(self):
        self.saved_job()
        self.assertEqual(self.jobs.get("saved")["status"], "paused")
        self.assertEqual(self.jobs.get("saved")["progress"]["completed"], 258)
        with self.assertRaises(ValueError):
            self.jobs.submit("new", lambda *_: None)
        result = self.jobs.control("saved", "resume", resume_fn=self.work)
        jid = result["job"]
        self.addCleanup(self.finish_worker, jid)
        self.assertTrue(self.entered.wait(2))
        self.assertEqual(self.jobs.get("saved")["status"], "resumed")
        self.finish_current.set()
        self.wait_status(jid, "done")

    def test_restart_marks_nonresumable_job_interrupted_and_preserves_progress(self):
        e.save(self.jobs.root / "interrupted.json", {
            "id": "interrupted", "project": "project", "label": "test storyboard",
            "created": "1", "status": "running",
            "progress": {"completed": 4, "total": 8, "unit": "页"},
        })
        with patch("comic_workflow.jobs.DATA", self.data):
            restarted = Jobs()
        job = restarted.get("interrupted")
        self.assertEqual(job["status"], "interrupted")
        self.assertEqual(job["progress"], {"completed": 4, "total": 8, "unit": "页"})
        self.assertEqual(restarted.controls, {})
        self.assertFalse(GENERATION_LOCK.locked())
        with self.assertRaisesRegex(ValueError, "二次确认"):
            restarted.control("interrupted", "cancel")
        self.assertEqual(restarted.get("interrupted")["status"], "interrupted")
        restarted.control("interrupted", "cancel", confirm=True)
        self.assertEqual(restarted.get("interrupted")["status"], "cancelled")
        self.assertEqual(restarted.get("interrupted")["progress"], job["progress"])

    def test_cancel_restored_task_keeps_saved_progress(self):
        self.saved_job("paused")
        with self.assertRaises(ValueError):
            self.jobs.control("saved", "cancel")
        self.jobs.control("saved", "cancel", confirm=True)
        self.assertEqual(self.jobs.get("saved")["status"], "cancelled")
        self.assertEqual(self.jobs.get("saved")["progress"]["completed"], 258)
