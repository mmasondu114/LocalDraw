import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from comic_workflow import engine as e
from comic_workflow.repository import Repository
from comic_workflow.paths import ROOT
from comic_workflow.model import build_context
from comic_workflow.sudowrite import package, import_result


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Repository(self.temp.name)
        self.pid = self.repo.create(None, True)
        self.p = self.repo.get(self.pid)

    def tearDown(self):
        self.temp.cleanup()

    def patch(self):
        panel = copy.deepcopy(self.p["pages"]["P002"]["panels"][2])
        panel["expression_note"] = "not allowed"
        del panel["expression_note"]
        panel["purpose"] += "（修订）"
        return {
            "panel": panel,
            "lettering": copy.deepcopy(
                [
                    t
                    for t in self.p["pages"]["P002"]["lettering"]
                    if t["panel_id"] == panel["id"]
                ]
            ),
        }

    def test_new_project_requires_canon(self):
        pid = self.repo.create(self.p["brief"])
        p = self.repo.get(pid)
        with self.assertRaises(ValueError):
            e.merged(p, "options", "", self.p["options"])
        with self.assertRaises(ValueError):
            e.validate_project(p, True)

    def test_candidate_never_changes_confirmed(self):
        self.repo.propose(self.pid, "panel", "P002-K03", self.patch(), e.digest(self.p))
        self.assertEqual(self.repo.get(self.pid), self.p)

    def test_stale_and_double_accept_rejected(self):
        c = self.repo.propose(
            self.pid, "panel", "P002-K03", self.patch(), e.digest(self.p)
        )
        self.repo.accept(self.pid, c["id"])
        with self.assertRaises(ValueError):
            self.repo.accept(self.pid, c["id"])
        with self.assertRaises(ValueError):
            self.repo.propose(
                self.pid, "panel", "P002-K03", self.patch(), e.digest(self.p)
            )

    def test_concurrent_accept_only_one_wins(self):
        c = self.repo.propose(
            self.pid, "panel", "P002-K03", self.patch(), e.digest(self.p)
        )
        out = []

        def accept():
            try:
                self.repo.accept(self.pid, c["id"])
                out.append(True)
            except ValueError:
                out.append(False)

        a = threading.Thread(target=accept)
        b = threading.Thread(target=accept)
        a.start()
        b.start()
        a.join()
        b.join()
        self.assertEqual(sorted(out), [False, True])

    def test_restore_and_restart_keep_history(self):
        c = self.repo.propose(
            self.pid, "panel", "P002-K03", self.patch(), e.digest(self.p)
        )
        q = self.repo.accept(self.pid, c["id"])
        restored = self.repo.restore(self.pid, "v000001", e.digest(q))
        self.assertEqual(restored["pages"], self.p["pages"])
        self.assertEqual(restored["version"], 3)
        reopened = Repository(self.temp.name)
        self.assertEqual(reopened.get(self.pid), restored)
        self.assertEqual(len(reopened.history(self.pid)), 2)

    def test_fork_keeps_original(self):
        pid = self.repo.fork(self.pid, "canon")
        p = self.repo.get(pid)
        self.assertIsNone(p["canon"])
        self.assertEqual(p["pages"], {})
        self.assertEqual(self.repo.get(self.pid), self.p)

    def test_full_page_state_boundary(self):
        page = copy.deepcopy(self.p["pages"]["P001"])
        page["state_out"]["time"] = "次日"
        with self.assertRaises(ValueError):
            e.merged(self.p, "page", "P001", page)

    def test_unknown_option(self):
        p = copy.deepcopy(self.p)
        p["outline"]["selected_option"] = "missing"
        with self.assertRaises(ValueError):
            e.validate_project(p)

    def test_missing_required_detail(self):
        p = copy.deepcopy(self.p)
        p["pages"]["P001"]["panels"][0]["main_instant"] = " "
        with self.assertRaises(ValueError):
            e.validate_project(p)

    def test_paper_turn_mapping(self):
        b = copy.deepcopy(self.p["brief"])
        b.update(
            page_mode="print_spreads",
            reading_direction="LTR",
            first_story_page_side="right",
        )
        self.assertEqual([e.turn_after(b, i) for i in [1, 2, 3]], [True, False, True])
        b.update(reading_direction="RTL", first_story_page_side="left")
        self.assertEqual([e.turn_after(b, i) for i in [1, 2, 3]], [True, False, True])
        b["first_story_page_side"] = "right"
        self.assertFalse(e.turn_after(b, 1))

    def test_export_version_matches(self):
        result = self.repo.export(self.pid)
        folder = self.repo.folder(self.pid) / "exports" / result["bundle"]
        self.assertEqual(
            e.read(folder / "manifest.json")["project_sha256"], e.digest(self.p)
        )
        self.assertEqual(e.read(folder / "script.json"), self.p)
        self.assertIn("人工编排", (folder / "script.md").read_text(encoding="utf-8"))

    def test_path_escape(self):
        with self.assertRaises(ValueError):
            self.repo.get("../outside")

    def test_audit_invalid_reference(self):
        report = {
            "verdict": "needs_revision",
            "issues": [
                {
                    "severity": "warning",
                    "page_id": "P999",
                    "panel_id": None,
                    "category": "test",
                    "evidence": "x",
                    "suggestion": "x",
                }
            ],
            "unknown_canon": [],
            "reviewed_scope": "P001",
        }
        with self.assertRaises(ValueError):
            e.merged(self.p, "audit", "P001", report)

    def test_context_never_sends_whole_book(self):
        ctx = build_context(self.p, "panel", "P002-K03", "")
        self.assertNotIn("pages", ctx)
        self.assertIn("start_state", ctx)

    def test_sudowrite_preserves_invalid_source(self):
        h = package(self.repo, self.pid, "panel", "P002-K03", "修改对白")
        with self.assertRaises(ValueError):
            import_result(self.repo, self.pid, h["id"], "只有散文")
        self.assertEqual(
            len(
                list(
                    (self.repo.folder(self.pid) / "sudowrite/responses").glob("*.json")
                )
            ),
            1,
        )
