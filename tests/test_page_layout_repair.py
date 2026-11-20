import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from comic_workflow import engine as e
from comic_workflow.model import generate, repair_page_layout
from comic_workflow.repository import Repository


class PageLayoutRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        repo = Repository(self.root / "projects")
        self.project = repo.get(repo.create(None, True))
        self.page = copy.deepcopy(self.project["pages"]["P001"])
        self.project["brief"]["panel_mode"] = "flexible"
        self.project["plan"]["pages"][0]["panel_count"] = 6
        while len(self.page["panels"]) < 6:
            item = copy.deepcopy(self.page["panels"][-1])
            number = len(self.page["panels"]) + 1
            item.update(id=f"P001-K{number:02}", number=number)
            self.page["panels"].append(item)
        self.ids = [p["id"] for p in self.page["panels"]]
        self.page["layout_rows"] = [
            {"weight": 1.0, "panels": [{"panel_id": key, "weight": 1.0}]}
            for key in self.ids[:5]
        ]

    def test_restore_sixth_reference_without_exceeding_five_rows_or_changing_text(self):
        before = copy.deepcopy(self.page)
        fixed, changes = repair_page_layout(self.page, self.ids)
        self.assertEqual(len(fixed["layout_rows"]), 5)
        self.assertEqual(fixed["layout_rows"][-1]["panels"][-1]["panel_id"], self.ids[-1])
        self.assertEqual(len(changes), 1)
        self.assertEqual(self.page, before)
        for key in before:
            if key != "layout_rows":
                self.assertEqual(fixed[key], before[key])
        e.merged(self.project, "page", "P001", fixed)
        self.assertEqual(repair_page_layout(fixed, self.ids), (fixed, []))

    def test_missing_actual_panel_is_not_fabricated(self):
        self.page["panels"].pop()
        fixed, changes = repair_page_layout(self.page, self.ids)
        self.assertEqual(fixed, self.page)
        self.assertEqual(changes, [])
        with self.assertRaisesRegex(ValueError, "必须有 6 格，实际 5 格"):
            e.merged(self.project, "page", "P001", fixed)

    def test_ambiguous_order_and_duplicates_are_not_silently_rewritten(self):
        for wrong in (self.ids[1], "unknown"):
            with self.subTest(wrong=wrong):
                self.page["layout_rows"][0]["panels"][0]["panel_id"] = wrong
                fixed, changes = repair_page_layout(self.page, self.ids)
                self.assertEqual(fixed, self.page)
                self.assertEqual(changes, [])

    def test_generation_repairs_layout_without_second_model_call_and_constrains_count(self):
        response = {"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(self.page)}}]}
        with patch("comic_workflow.model.count_messages", return_value=1000), \
             patch("comic_workflow.model.chat_stream", return_value=response) as stream:
            payload, metrics = generate(self.project, "page", "P001", "", self.root / "run")
        self.assertEqual(stream.call_count, 1)
        schema = stream.call_args.args[1]["response_format"]["json_schema"]["schema"]
        self.assertEqual(schema["properties"]["panels"]["minItems"], 6)
        self.assertEqual(schema["properties"]["panels"]["maxItems"], 6)
        self.assertTrue(metrics["layout_reference_repairs"])
        self.assertEqual(e.read(self.root / "run/0/validated-payload.json"), payload)
