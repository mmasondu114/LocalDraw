import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from comic_workflow import bubble_planner as b


class BubblePlannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        def page(n):
            return {"id": f"P{n:03}", "number": n, "reading_direction": "LTR",
                    "panels": [{"id": f"P{n:03}-K01", "characters": []}],
                    "lettering": [{"id": f"P{n:03}-T01", "panel_id": f"P{n:03}-K01",
                                   "kind": "dialogue", "text": "今天很开心！", "order": 1}]}
        self.project = {"brief": {"title": "测试"}, "pages": {f"P{n:03}": page(n) for n in (1, 2)}}
        self.repo = SimpleNamespace(get=lambda pid: copy.deepcopy(self.project), folder=lambda pid: self.root)
        self.state = {"selections": {}}
        self.addCleanup(patch.stopall)
        patch.object(b, "drawing_state", return_value=self.state).start()
        patch.object(b, "settings", return_value={"model_path": "local-test-model.gguf"}).start()

    def payload(self, page):
        return {"bubbles": [{"id": page["lettering"][0]["id"], "form": "soft", "anchor": "top-right",
                            "tail": "left", "columns": ["今天很", "开心！"], "weight": "normal", "reason": "Warm dialogue"}]}

    def test_rejects_rewrites_missing_and_duplicate_lines(self):
        page = self.project["pages"]["P001"]
        payload = self.payload(page)
        self.assertEqual(b.validate(payload, page)["P001-T01"]["form"], "soft")
        for bad in [{"bubbles": []}, {"bubbles": payload["bubbles"] * 2}]:
            with self.assertRaises(ValueError): b.validate(bad, page)
        payload["bubbles"][0]["columns"] = ["换了对白"]
        with self.assertRaises(ValueError): b.validate(payload, page)

    def test_read_does_not_call_model_and_changed_selection_invalidates(self):
        with patch.object(b.creative, "structured", side_effect=AssertionError("must not start model")):
            self.assertEqual(b.load(self.repo, "book")["pages"], {})
        with patch.object(b.creative, "structured", side_effect=lambda prompt, ctx, *a, **kw:
                          (self.payload(self.project["pages"][ctx["panels"][0]["id"].split("-")[0]]), {})) as call:
            result = b.generate(self.repo, "book", self.root / "logs", lambda _: None)
            self.assertEqual(len(result["completed"]), 2)
            self.assertEqual(call.call_count, 2)
            b.generate(self.repo, "book", self.root / "logs", lambda _: None)
            self.assertEqual(call.call_count, 2, "completed pages are reused")
        self.state["selections"]["P001-K01"] = {"image": {"id": "replacement"}}
        loaded = b.load(self.repo, "book")
        self.assertEqual(loaded["stale_pages"], ["P001"])
        self.assertEqual(list(loaded["pages"]), ["P002"])

    def test_page_failure_retries_then_continues(self):
        good = self.payload(self.project["pages"]["P002"])
        with patch.object(b.creative, "structured", side_effect=[ValueError("invalid plan"), ValueError("invalid again"), (good, {})]) as call:
            result = b.generate(self.repo, "book", self.root / "logs", lambda _: None)
        self.assertEqual(call.call_count, 3)
        self.assertEqual(result["completed"], ["P002"])
        self.assertEqual(result["failures"][0]["page"], "P001")

    def test_narration_has_no_speaker_tail(self):
        page = copy.deepcopy(self.project["pages"]["P001"])
        page["lettering"][0]["kind"] = "narration"
        choice = b.validate(self.payload(page), page)["P001-T01"]
        self.assertEqual((choice["form"], choice["tail"]), ("caption", "none"))

