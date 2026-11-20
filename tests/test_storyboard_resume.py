import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from comic_workflow import engine as e
from comic_workflow.jobs import Jobs
from comic_workflow.repository import Repository
from comic_workflow.server import Application
from comic_workflow.storyboard_tools import generate_all, restore_jobs, PAGE_INSTRUCTION
from comic_workflow.task_control import TaskCancelled


class StoryboardResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = Repository(self.root / "projects")
        self.pid = self.repo.create(None, True)
        self.base = self.repo.get(self.pid)
        page = self.base["pages"]["P001"]
        planned = self.base["plan"]["pages"][0]
        self.base["brief"]["page_count"] = 8
        self.base["plan"]["pages"] = []
        self.pages = {}
        state = self.base["canon"]["initial_state"]
        for n in range(1, 9):
            key = f"P{n:03}"
            plan = copy.deepcopy(planned)
            plan.update(id=key, number=n, turn_after=e.turn_after(self.base["brief"], n))
            self.base["plan"]["pages"].append(plan)
            payload = json.loads(json.dumps(page).replace("P001", key))
            payload.update(number=n, state_in=copy.deepcopy(state))
            self.pages[key] = payload
            state = payload["state_out"]
        self.base["pages"] = {}
        e.save(self.repo.folder(self.pid) / "project.json", self.base)
        self.folder = self.repo.folder(self.pid) / "storyboard-drafts/book"
        self.request = {"action": "storyboard_all", "bundle_id": "book",
                        "base_hash": e.digest(self.base), "instruction": "保持雨夜氛围"}
        self.app = SimpleNamespace(repo=self.repo, prepare_language=Mock(), model=Mock())
        self.calls = []

    def save_partial(self, count=4):
        bundle = {"id": "book", "base_hash": self.request["base_hash"],
                  "created": "1", "status": "running", "plan": self.base["plan"],
                  "pages": {key: {"payload": copy.deepcopy(value), "metadata": {}}
                            for key, value in list(self.pages.items())[:count]}}
        e.save(self.folder / "bundle.json", bundle)
        return copy.deepcopy(bundle)

    def generate(self, p, stage, target, instruction, folder, update):
        self.calls.append((stage, target))
        if stage == "page":
            n = int(target[1:])
            self.assertEqual(list(p["pages"]), [f"P{i:03}" for i in range(1, n)])
            self.assertEqual(instruction, PAGE_INSTRUCTION + self.request["instruction"])
            return copy.deepcopy(self.pages[target]), {}
        self.assertEqual(stage, "audit")
        return {"verdict": "pass", "issues": []}, {}

    def run_resume(self, jid="resume", generate=None):
        with patch("comic_workflow.storyboard_tools.generate", side_effect=generate or self.generate):
            return generate_all(self.app, self.pid, self.request, lambda _: None, jid)

    def test_four_of_eight_only_generates_five_to_eight(self):
        before = self.save_partial()
        result = self.run_resume()
        self.assertEqual(result["bundle"], "book")
        self.assertEqual([target for stage, target in self.calls if stage == "page"],
                         ["P005", "P006", "P007", "P008"])
        saved = e.read(self.folder / "bundle.json")
        for key, entry in before["pages"].items():
            self.assertEqual(saved["pages"][key]["payload"], entry["payload"])
        self.assertEqual(saved["status"], "complete")
        self.assertEqual(self.repo.get(self.pid), self.base)

    def test_audit_progress_counts_only_persisted_reviews(self):
        self.save_partial(8)
        events = []

        def record(event):
            if isinstance(event, dict) and event.get("detail_progress"):
                entries = e.read(self.folder / "bundle.json")["pages"]
                reviews = [item["metadata"].get("continuity_review") for item in entries.values()]
                completed = sum(bool(r) and r.get("verdict") != "unavailable" for r in reviews)
                self.assertEqual(event["detail_progress"]["completed"], completed)
                events.append(completed)

        with patch("comic_workflow.storyboard_tools.generate", side_effect=self.generate):
            generate_all(self.app, self.pid, self.request, record, "audit")
        self.assertEqual(sorted(set(events)), list(range(9)))
        self.assertEqual(events[-1], 8)

    def test_unavailable_audit_does_not_increment_checked_pages(self):
        self.save_partial(8)

        def unavailable(project, stage, key, instruction, folder, update):
            if stage == "audit" and key == "P004":
                raise ValueError("model unavailable")
            return self.generate(project, stage, key, instruction, folder, update)

        result = self.run_resume(generate=unavailable)
        self.assertIn("1 页缺少核对结果", result["completion_message"])
        saved = e.read(self.folder / "bundle.json")
        self.assertEqual(saved["status"], "complete")
        self.assertEqual(saved["pages"]["P004"]["metadata"]["continuity_review"]["verdict"], "unavailable")

    def test_second_interruption_keeps_newly_saved_page(self):
        self.save_partial()
        def fail_at_six(*args):
            if args[1:3] == ("page", "P006"):
                raise RuntimeError("connection lost")
            return self.generate(*args)
        with self.assertRaises(RuntimeError):
            self.run_resume(generate=fail_at_six)
        self.calls.clear()
        self.run_resume("resume2")
        self.assertEqual([target for stage, target in self.calls if stage == "page"],
                         ["P006", "P007", "P008"])

    def test_completed_audits_are_not_repeated(self):
        bundle = self.save_partial(8)
        for entry in list(bundle["pages"].values())[:4]:
            entry["metadata"]["continuity_review"] = {"verdict": "pass"}
        e.save(self.folder / "bundle.json", bundle)
        self.run_resume()
        self.assertEqual(self.calls, [("audit", f"P{i:03}") for i in range(5, 9)])

    def test_changed_project_and_missing_checkpoint_never_generate(self):
        self.save_partial()
        self.request["base_hash"] = "outdated"
        with self.assertRaisesRegex(ValueError, "作品已变化"):
            self.run_resume()
        self.request.update(base_hash=e.digest(self.base), bundle_id="missing", resume_existing=True)
        with self.assertRaisesRegex(ValueError, "不会从头重做"):
            self.run_resume()
        self.assertEqual(self.calls, [])
        self.app.prepare_language.assert_not_called()

    def test_cancel_preserves_pages_and_cannot_resume(self):
        before = self.save_partial()
        with patch("comic_workflow.storyboard_tools.checkpoint", side_effect=TaskCancelled):
            with self.assertRaises(TaskCancelled):
                self.run_resume()
        saved = e.read(self.folder / "bundle.json")
        self.assertEqual(saved["pages"], before["pages"])
        self.assertEqual(saved["status"], "cancelled")
        with self.assertRaisesRegex(ValueError, "不能继续"):
            self.run_resume()

    def test_old_job_restores_from_draft_and_original_request_without_inference(self):
        self.save_partial()
        e.save(self.folder / "P001/0/request.json", {
            "response_format": {"json_schema": {"name": "page"}},
            "messages": [{"role": "user", "content": json.dumps({
                "instruction": PAGE_INSTRUCTION + self.request["instruction"]})}]})
        e.save(self.root / "jobs/book.json", {"id": "book", "project": self.pid,
            "label": "生成整部分镜", "status": "interrupted", "created": "1"})
        with patch("comic_workflow.jobs.DATA", self.root):
            jobs = Jobs()
        restore_jobs(self.repo, jobs)
        job = jobs.get("book")
        self.assertEqual(job["status"], "paused")
        self.assertEqual(job["request"]["instruction"], self.request["instruction"])
        self.assertEqual(job["progress"]["completed"], 4)
        self.assertEqual(jobs.controls, {})
        self.assertEqual(self.calls, [])

    def test_restart_and_resume_through_application_dispatch(self):
        self.save_partial()
        e.save(self.root / "jobs/book.json", {"id": "book", "project": self.pid,
            "label": "生成整部分镜", "status": "running", "created": "1", "request": self.request})
        with patch("comic_workflow.jobs.DATA", self.root):
            app = Application(self.repo)
        self.assertEqual(app.jobs.get("book")["status"], "paused")
        app.prepare_language = Mock()
        app.model.stop = Mock()
        with patch("comic_workflow.storyboard_tools.generate", side_effect=self.generate):
            result = app.action({"action": "resume_job", "job": "book"})
            deadline = time.monotonic() + 5
            while result["job"] in app.jobs.controls and time.monotonic() < deadline:
                time.sleep(.01)
        job = app.jobs.get(result["job"])
        self.assertEqual(job["status"], "done", job.get("message"))
        self.assertEqual(job["result"]["bundle"], "book")
        self.assertEqual([t for stage, t in self.calls if stage == "page"], [f"P{i:03}" for i in range(5, 9)])
        self.assertEqual(app.jobs.get("book")["status"], "resumed")

    def test_trashed_work_keeps_checkpoint_and_can_resume_after_restart(self):
        self.repo.delete(self.pid, self.request["base_hash"])
        self.test_restart_and_resume_through_application_dispatch()
        self.assertEqual(self.repo.listing(), [])
        with self.assertRaisesRegex(ValueError, "回收站"):
            self.repo.get(self.pid)
        self.repo.restore_deleted(self.pid)
        self.assertEqual(len(e.read(self.folder / "bundle.json")["pages"]), 8)

    def test_new_submission_persists_request_and_stable_bundle_id(self):
        with patch("comic_workflow.jobs.DATA", self.root):
            app = Application(self.repo)
        with patch.object(app.jobs, "submit", return_value="job") as submit:
            app.action({"action": "storyboard_all", "project": self.pid,
                        "base_hash": e.digest(self.base), "instruction": "test"})
        request = submit.call_args.kwargs["request"]
        self.assertEqual(request["action"], "storyboard_all")
        self.assertTrue(request["bundle_id"])
        self.assertEqual(request["instruction"], "test")

    def test_already_complete_checkpoint_does_not_load_model(self):
        saved = self.save_partial(8)
        saved["status"] = "complete"
        e.save(self.folder / "bundle.json", saved)
        self.run_resume()
        self.app.prepare_language.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_invalid_saved_page_does_not_crash_startup_or_enable_resume(self):
        saved = self.save_partial()
        saved["pages"]["P002"]["payload"]["panels"] = "invalid"
        e.save(self.folder / "bundle.json", saved)
        e.save(self.root / "jobs/book.json", {"id": "book", "project": self.pid,
            "label": "生成整部分镜", "status": "running", "created": "1", "request": self.request})
        with patch("comic_workflow.jobs.DATA", self.root):
            app = Application(self.repo)
        job = app.jobs.get("book")
        self.assertEqual(job["status"], "interrupted")
        self.assertTrue(job["resume_error"])
        self.assertEqual(app.jobs.controls, {})

    def failed_app(self):
        bundle = self.save_partial(7)
        bundle.update(status="failed", error="invalid last page")
        e.save(self.folder / "bundle.json", bundle)
        e.save(self.root / "jobs/book.json", {"id": "book", "project": self.pid,
            "label": "生成整部分镜", "status": "failed", "created": "1",
            "request": self.request, "message": "invalid last page",
            "progress": {"completed": 7, "total": 8, "unit": "页"}})
        with patch("comic_workflow.jobs.DATA", self.root):
            app = Application(self.repo)
        app.prepare_language = Mock()
        app.model.stop = Mock()
        return app, bundle

    def test_retry_failed_job_only_regenerates_missing_page_and_rejects_duplicate(self):
        app, before = self.failed_app()
        with patch("comic_workflow.storyboard_tools.generate", side_effect=self.generate):
            result = app.action({"action": "retry_job", "job": "book"})
            deadline = time.monotonic() + 5
            while result["job"] in app.jobs.controls and time.monotonic() < deadline:
                time.sleep(.01)
        job = app.jobs.get(result["job"])
        self.assertEqual(job["status"], "done", job.get("message"))
        self.assertEqual(job["request"]["bundle_id"], "book")
        self.assertTrue(job["request"]["resume_existing"])
        self.assertEqual([t for stage, t in self.calls if stage == "page"], ["P008"])
        saved = e.read(self.folder / "bundle.json")
        for key, entry in before["pages"].items():
            self.assertEqual(saved["pages"][key]["payload"], entry["payload"])
        self.assertEqual(saved["status"], "complete")
        self.assertEqual(self.repo.get(self.pid), self.base)
        self.assertEqual(app.jobs.get("book")["resumed_as"], result["job"])
        with self.assertRaisesRegex(ValueError, "只能重新生成"):
            app.action({"action": "retry_job", "job": "book"})

    def test_retry_rejects_missing_checkpoint_before_loading_model(self):
        app, _ = self.failed_app()
        (self.folder / "bundle.json").unlink()
        with patch.object(app.jobs, "submit") as submit:
            with self.assertRaisesRegex(ValueError, "不会从头重做"):
                app.action({"action": "retry_job", "job": "book"})
        submit.assert_not_called()
        app.prepare_language.assert_not_called()
        self.assertEqual(app.jobs.get("book")["status"], "failed")

    def test_retry_rejects_changed_project_before_loading_model(self):
        app, _ = self.failed_app()
        changed = copy.deepcopy(self.base)
        changed["brief"]["title"] = "Changed title"
        e.save(self.repo.folder(self.pid) / "project.json", changed)
        with patch.object(app.jobs, "submit") as submit:
            with self.assertRaisesRegex(ValueError, "作品已变化"):
                app.action({"action": "retry_job", "job": "book"})
        submit.assert_not_called()
        app.prepare_language.assert_not_called()

    def test_retry_rejects_other_task_types_and_keeps_original_error(self):
        app, _ = self.failed_app()
        job = app.jobs.get("book")
        job["request"] = {"action": "draw_imported"}
        app.jobs.save("book", job)
        with self.assertRaisesRegex(ValueError, "仅用于"):
            app.action({"action": "retry_job", "job": "book"})
        self.assertEqual(app.jobs.get("book"), job)
        self.assertEqual(app.jobs.controls, {})
