import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from comic_workflow import creative, engine as e
from comic_workflow.repository import Repository


class StoryPreservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = Repository(self.root / "projects")
        project = self.repo.get(self.repo.create(None, True))
        self.metadata = {
            "brief": copy.deepcopy(project["brief"]),
            "canon": copy.deepcopy(project["canon"]),
            "outline": copy.deepcopy(project["outline"]),
            "assumptions": [],
            "changes": [],
        }
        self.metadata["brief"].pop("character_refs", None)
        self.metadata["brief"]["panel_mode"] = "flexible"
        self.library = Mock()
        self.library.resolve.return_value = []
        self.library.freeze_brief.side_effect = lambda brief: brief

    def draft(self, original, mode="structure", sections=None):
        captured = {"calls": []}

        def generate(prompt, context, schema, *args, **kwargs):
            captured.update(context=context, schema=schema)
            captured["calls"].append({"context": copy.deepcopy(context), "schema": copy.deepcopy(schema)})
            payload = copy.deepcopy(self.metadata)
            if mode == "structure":
                payload["sections"] = sections or [{"start_block": 1, "heading": "Rainy night"}]
                for character in payload["canon"]["characters"]:
                    character["source_notes"] = ["Source block 1"]
            else:
                payload["story"] = original + "\nThe shopkeeper opened the door."
            payload = {key: value for key, value in payload.items() if key in schema["properties"]}
            e.jsonschema.validate(payload, schema)
            return payload, {"seconds": 1}

        with patch.object(creative, "structured", side_effect=generate):
            result = creative.story_draft(
                self.library, {"text": original, "mode": mode}, self.root, lambda _: None
            )
        return result, captured

    def test_dialogue_actions_motivation_and_repeated_lines_survive(self):
        original = (
            "Lynn paused outside, holding the soaked paper boat in her right hand.\r\n\r\n"
            "Afraid the shopkeeper would recognize her handwriting, she folded the signature underneath.\r\n"
            "Lynn: 'Do you remember?'\r\n"
            "The shopkeeper silently slid the brass key across the table.\r\n"
            "Lynn: 'Do you remember?'\r\n"
            "Only after three chimes did she notice yesterday's date engraved on the key."
        )
        sections = [{"start_block": 1, "heading": "Outside"}, {"start_block": 4, "heading": "At the table"}]
        result, captured = self.draft(original, sections=sections)
        story = result["payload"]["story"]
        self.assertEqual(
            story.removeprefix("[Outside]\n").replace("\n\n[At the table]\n", ""), original
        )
        self.assertEqual(story.count("Lynn: 'Do you remember?'"), 2)
        self.assertNotRegex(story + " ".join(result["payload"]["changes"]), r"[\u3400-\u9fff]")
        self.assertEqual("".join(captured["context"]["source_blocks"]), original)
        self.assertNotIn("story", captured["schema"]["properties"])
        self.assertIn("story", creative.STORY["properties"])
        accepted = self.repo.get(creative.create_story(self.repo, self.library, result))
        self.assertEqual(accepted["script"], story)
        self.assertEqual(accepted["script_source"]["original"], original)

    def test_long_source_and_single_unbroken_paragraph_are_not_shortened(self):
        for original in ("Keep every action. " * 600, "Lynn: Stay.\n" * 1000):
            with self.subTest(length=len(original)):
                result, _ = self.draft(original)
                self.assertEqual(result["payload"]["story"], "[Rainy night]\n" + original.strip())

    def test_bad_heading_positions_fail_instead_of_losing_or_reordering_source(self):
        blocks = creative.story_source_blocks("第一段。\n第二段。\n第三段。")
        for starts in ([], [2], [1, 1], [1, 3, 2], [1, 4], [0], [True]):
            with self.subTest(starts=starts), self.assertRaises(ValueError):
                creative.assemble_story(blocks, [
                    {"start_block": n, "heading": "场景"} for n in starts
                ])

    def test_blank_heading_is_rejected(self):
        with self.assertRaises(ValueError):
            creative.assemble_story(["原文。"], [{"start_block": 1, "heading": "  "}])

    def test_expansion_and_continuation_still_use_generated_prose(self):
        for mode in ("expand", "continue"):
            with self.subTest(mode=mode):
                result, captured = self.draft("Lynn knocked three times.", mode)
                self.assertEqual(result["payload"]["story"], "Lynn knocked three times.\nThe shopkeeper opened the door.")
                self.assertIn("story", captured["schema"]["properties"])
                self.assertNotIn("source_blocks", captured["context"])

    def test_source_references_cannot_run_beyond_the_real_story(self):
        schema = creative.story_generation_schema(253)
        notes = schema["properties"]["canon"]["properties"]["characters"]["items"]["properties"]["source_notes"]
        e.jsonschema.validate(["Source block 1", "Source block 253"], notes)
        for invalid in (
            ["Source block 0"], ["Source block 254"], ["Source block 5666"],
            ["Source: Block " + ", ".join(str(n) for n in range(10, 5667, 4))],
            ["Source block 1"] * 4,
        ):
            with self.subTest(invalid=invalid[:1]), self.assertRaises(e.jsonschema.ValidationError):
                e.jsonschema.validate(invalid, notes)

    def test_structure_splits_budgets_but_keeps_full_source_and_merges_results(self):
        original = "Lynn arrives.\n\nThe shopkeeper opens the door."
        result, captured = self.draft(original)
        calls = captured["calls"]
        self.assertEqual(len(calls), 2)
        self.assertEqual(set(calls[0]["schema"]["properties"]), {"canon"})
        self.assertNotIn("canon", calls[1]["schema"]["properties"])
        for call in calls:
            self.assertEqual("".join(call["context"]["source_blocks"]), original)
        self.assertEqual(result["payload"]["canon"]["characters"][0]["name"], self.metadata["canon"]["characters"][0]["name"])
        self.assertEqual(result["metrics"]["seconds"], 2)
        stored = copy.deepcopy(result["payload"])
        stored["brief"].pop("character_refs")
        e.jsonschema.validate(stored, creative.STORY)

    def test_failed_canon_stops_before_second_call_or_partial_application(self):
        with patch.object(creative, "structured", side_effect=ValueError("模型未完成草稿")) as generate:
            with self.assertRaisesRegex(ValueError, "模型未完成"):
                creative.story_draft(self.library, {"text": "Lynn arrives."}, self.root, lambda _: None)
        self.assertEqual(generate.call_count, 1)
        self.assertFalse((self.root / "draft.json").exists())

    def test_limits_are_local_and_do_not_truncate_prose_or_other_fields(self):
        before = copy.deepcopy(creative.STORY)
        schema = creative.story_generation_schema(3)
        character = schema["properties"]["canon"]["properties"]["characters"]["items"]["properties"]
        with self.assertRaises(e.jsonschema.ValidationError):
            e.jsonschema.validate(["repeated " * 1000], character["verified_facts"])
        with self.assertRaises(e.jsonschema.ValidationError):
            e.jsonschema.validate("x" * 501, character["appearance"])
        e.jsonschema.validate("x" * 12000, schema["properties"]["story"])
        self.assertEqual(creative.STORY, before)
        self.assertNotIn("enum", creative.story_generation_schema()["properties"]["canon"]["properties"]["characters"]["items"]["properties"]["source_notes"]["items"])


if __name__ == "__main__":
    unittest.main()
