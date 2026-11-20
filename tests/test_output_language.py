import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from comic_workflow import creative, engine as e
from comic_workflow.model import generate
from comic_workflow.output_language import DEFAULT_OUTPUT_POLICY
from comic_workflow.repository import Repository
from comic_workflow.schemas import obj, S


class OutputLanguageTests(unittest.TestCase):
    def test_structured_requests_default_to_chinese_lettering_and_english_art(self):
        response = {"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"description": "A brass key rests on the table."})
        }}]}
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(creative, "request_json", return_value={"tokens": [1]}),
            patch.object(creative, "chat_stream", return_value=response) as stream,
        ):
            payload, _ = creative.structured(
                "Extract scene details.", {"source": "A brass key rests on the table."},
                obj({"description": S}), Path(tmp),
            )
            prompt = stream.call_args.args[1]["messages"][0]["content"]
            self.assertTrue(prompt.endswith(DEFAULT_OUTPUT_POLICY))
            self.assertIn("Simplified Chinese", prompt)
            self.assertIn("Keep positive and negative image-generation prompts in English", prompt)
            self.assertEqual(payload["description"], "A brass key rests on the table.")

    def test_page_and_single_panel_allow_detailed_english_without_changing_ids(self):
        description = (
            "Lynn stands to the left of the wooden table, holding a soaked paper boat in her right hand. "
            "The folded signature is hidden underneath the boat. Across the table, the shopkeeper "
            "rests two fingers on a brass key engraved with yesterday's date. A wall clock is visible "
            "behind the shopkeeper, and rain streaks the window on the right."
        )
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository(Path(tmp) / "projects")
            p = repo.get(repo.create(None, True))
            page = p["pages"]["P001"]
            page["layout_rows"] = [{
                "weight": 1,
                "panels": [{"panel_id": panel["id"], "weight": 1} for panel in page["panels"]],
            }]
            page["panels"][0]["drawing_description"] = description
            pid = page["panels"][0]["id"]
            for stage, target, payload in (
                ("page", "P001", page),
                ("panel", pid, {"panel": page["panels"][0],
                                "lettering": [x for x in page["lettering"] if x["panel_id"] == pid]}),
            ):
                with (
                    self.subTest(stage=stage),
                    patch("comic_workflow.model.count_messages", return_value=1000),
                    patch("comic_workflow.model.chat_stream", return_value={"choices": [{
                        "finish_reason": "stop", "message": {"content": json.dumps(payload)}
                    }]}) as stream,
                ):
                    result, _ = generate(p, stage, target, "", Path(tmp) / stage)
                    body = stream.call_args.args[1]
                    prompt = body["messages"][0]["content"]
                    self.assertTrue(prompt.endswith(DEFAULT_OUTPUT_POLICY))
                    self.assertIn("lettering.text 默认使用简体中文", prompt)
                    self.assertNotIn("对白使用英文", prompt)
                    self.assertNotIn("所有自然语言内容用简体中文", prompt)
                    schema = body["response_format"]["json_schema"]["schema"]
                    e.jsonschema.validate(result, schema)
                    panel = result["panels"][0] if stage == "page" else result["panel"]
                    self.assertEqual(panel["drawing_description"], description)
                    self.assertEqual(panel["id"], pid)


if __name__ == "__main__":
    unittest.main()
