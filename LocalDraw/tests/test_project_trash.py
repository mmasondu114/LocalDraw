import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from comic_workflow import engine as e
from comic_workflow.model import GENERATION_LOCK
from comic_workflow.repository import Repository
from comic_workflow.app_core import StudioApp as Application


class ProjectTrashTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        with patch("comic_workflow.jobs.DATA", self.root):
            self.app = Application(Repository(self.root / "projects"))
        self.repo = self.app.repo
        self.pid = self.repo.create(None, True)
        self.folder = self.repo.folder(self.pid)
        self.hash = e.digest(self.repo.get(self.pid))
        self.action = {"action": "delete_project", "project": self.pid,
                       "base_hash": self.hash, "confirm": True}

    def test_roundtrip_preserves_every_file_and_blocks_stale_autosave(self):
        for path in ["images/chosen.png", "history/v000001.json", "drafts/page.json",
                     "workspace.json", "runs/task/report.json"]:
            f = self.folder / path
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"preserve exactly")
        before = {f.relative_to(self.folder): f.read_bytes() for f in self.folder.rglob("*") if f.is_file()}
        other = self.repo.create(None, True)
        self.app.action(self.action)
        self.assertEqual([p["id"] for p in self.repo.listing()], [other])
        self.assertEqual(self.repo.trash()[0]["id"], self.pid)
        with self.assertRaisesRegex(ValueError, "回收站"):
            self.app.action({"action": "save_workspace", "project": self.pid,
                             "state": {"updated": 1}})
        with self.assertRaises(ValueError):
            self.repo.get(self.pid)
        self.app.action({"action": "restore_deleted_project", "project": self.pid})
        after = {f.relative_to(self.folder): f.read_bytes() for f in self.folder.rglob("*") if f.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(e.digest(self.repo.get(self.pid)), self.hash)
        self.assertEqual(self.repo.trash(), [])

    def test_confirmation_stale_hash_and_delete_while_generation_busy(self):
        for fields in [{"confirm": False}, {"base_hash": "old"}]:
            with self.assertRaises(ValueError):
                self.app.action({**self.action, **fields})
        GENERATION_LOCK.acquire()
        try:
            self.app.action(self.action)
            self.assertEqual(self.repo.listing(), [])
            self.app.action({"action": "restore_deleted_project", "project": self.pid})
        finally:
            GENERATION_LOCK.release()
        self.assertEqual(len(self.repo.listing()), 1)
        self.assertEqual(self.repo.trash(), [])
        with self.assertRaises(ValueError):
            self.repo.restore_deleted("../outside")

    def test_unacknowledged_render_state_is_preserved(self):
        state = {"batches": [{"status": "uncertain", "active_prompt": "keep-this-id"}]}
        e.save(self.folder / "images/index.json", state)
        self.app.action(self.action)
        self.assertEqual(e.read(self.folder / "images/index.json"), state)
        self.assertEqual(self.app.trash_state()[0]["drawing_states"], ["uncertain"])
        self.app.action({"action": "restore_deleted_project", "project": self.pid})
        self.assertEqual(e.read(self.folder / "images/index.json"), state)

    def test_live_task_continues_writing_after_single_and_bulk_delete(self):
        for bulk in (False, True):
            with self.subTest(bulk=bulk):
                started, proceed, written, finish = (threading.Event() for _ in range(4))
                def worker(update, jid):
                    started.set()
                    if not proceed.wait(5):
                        raise TimeoutError("test did not release worker")
                    self.assertEqual(e.digest(self.repo.get(self.pid)), self.hash)
                    e.save(self.repo.folder(self.pid) / "runs/live.json", {"status": "running", "completed": 1})
                    update({"progress": {"completed": 1, "total": 2, "unit": "张"}})
                    written.set()
                    if not finish.wait(5):
                        raise TimeoutError("test did not finish worker")
                    return {"finished": True}
                jid = self.app.jobs.submit("background fixture", worker, self.pid)
                try:
                    self.assertTrue(started.wait(2))
                    request = {"action": "delete_projects", "confirm": True,
                               "items": [{"id": self.pid, "base_hash": self.hash}]} if bulk else self.action
                    self.app.action(request)
                    self.assertEqual(self.app.jobs.get(jid)["status"], "running")
                    with self.assertRaisesRegex(ValueError, "回收站"):
                        self.repo.get(self.pid)
                    with self.assertRaisesRegex(ValueError, "回收站"):
                        self.app.action({"action": "save_workspace", "project": self.pid, "state": {"updated": 5}})
                    proceed.set()
                    self.assertTrue(written.wait(2))
                    job = self.app.trash_state()[0]["jobs"][0]
                    self.assertEqual(job["status"], "running")
                    self.assertEqual(job["progress"]["completed"], 1)
                    self.app.action({"action": "restore_deleted_project", "project": self.pid})
                    self.assertEqual(e.read(self.repo.folder(self.pid) / "runs/live.json")["completed"], 1)
                    self.assertEqual(self.app.jobs.get(jid)["status"], "running")
                finally:
                    proceed.set(); finish.set()
                    for _ in range(200):
                        if jid not in self.app.jobs.controls:
                            break
                        time.sleep(.01)
                self.assertEqual(self.app.jobs.get(jid)["status"], "done")
