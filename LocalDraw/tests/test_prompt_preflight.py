import copy
import unittest
from unittest.mock import Mock, patch

from comic_workflow.prompt_preflight import analyze, visual_cast, with_render_plan


class PromptPreflightTests(unittest.TestCase):
    def setUp(self):
        self.project = {
            "brief": {"character_refs": [
                {"id": "saved-a", "revision": 1, "name": "甲", "profile": {"identity": "Alice (original)", "hair": "red hair"}},
                {"id": "saved-b", "revision": 1, "name": "乙", "profile": {"identity": "Beatrice (original)", "marks": "wolf ears", "personality": "private background"}},
            ]},
            "canon": {"characters": [{"id": "A", "name": "甲"}, {"id": "B", "name": "乙"}]},
            "pages": {"P001": {"panels": [
                {"id": "P001-K01", "characters": [{"character_id": "A"}], "shot": "全景", "angle": "平视"},
                {"id": "P001-K02", "characters": [{"character_id": "B"}], "shot": "特写", "angle": "侧视"},
            ]}},
        }

    def test_absent_character_and_actual_cast_without_rewriting(self):
        rows = [{"panel_id": "P001-K01", "positive": "Alice and Beatrice in a cafe. 全景，站在门口。", "negative": " blur "}]
        before = copy.deepcopy((self.project, rows))
        result = analyze(self.project, rows)
        self.assertEqual({w["code"] for w in result["warnings"]}, {"absent_character", "mixed_language"})
        self.assertEqual([c["id"] for c in result["panel_details"]["P001-K01"]["visible_characters"]], ["A"])
        self.assertEqual((self.project, rows), before)

    def test_character_names_match_boundaries_and_explicit_names_only(self):
        result = analyze(self.project, [{"panel_id": "P001-K01", "positive": "Alice reads a Beatricesque novel."}])
        self.assertFalse(result["warnings"])
        result = analyze(self.project, [{"panel_id": "P001-K01", "positive": "ALICE stands beside BEATRICE."}])
        self.assertEqual(result["warnings"][0]["code"], "absent_character")

    def test_detects_shared_prefix_even_when_actions_differ(self):
        prefix = "Two people wearing detailed clothing in the same room. " * 4
        rows = [{"panel_id": "P001-K01", "positive": prefix + "Standing."},
                {"panel_id": "P001-K02", "positive": prefix + "Reading."}]
        result = analyze(self.project, rows)
        self.assertEqual(result["unique_positive_count"], 2)
        self.assertIn("shared_prefix", {w["code"] for w in result["warnings"]})

    def test_duplicate_whitespace_normalization_and_empty_drafts(self):
        rows = [{"panel_id": "P001-K01", "positive": "A quiet room."},
                {"panel_id": "P001-K02", "positive": "  A quiet\nroom.  "}]
        result = analyze(self.project, rows)
        self.assertEqual(result["unique_positive_count"], 1)
        self.assertEqual(result["warnings"][0]["code"], "identical_prompts")
        self.assertFalse(analyze(self.project, [{"panel_id": "P001-K01", "positive": ""}])["warnings"])

    def test_export_visual_profiles_only_include_visible_people_and_visual_fields(self):
        cast = visual_cast(self.project, self.project["pages"]["P001"]["panels"][1])
        self.assertEqual(len(cast), 1)
        self.assertEqual(cast[0]["appearance"], {"identity": "Beatrice (original)", "marks": "wolf ears"})

    def test_render_plan_reports_text_only_character_and_reads_catalog_once(self):
        rows = [{"panel_id": "P001-K01", "positive": "Alice reads."},
                {"panel_id": "P001-K02", "positive": "Beatrice waits."}]
        def routed(prefs, project, panel, *, labels):
            self.assertEqual(prefs["checkpoint"], "selected-model")
            self.assertEqual(labels, {"catalog": True})
            return [{"file": "alice", "label": "Alice", "weight": .7, "target_character_ids": ["A"]}] if panel["id"].endswith("01") else []
        with patch("comic_workflow.drawing_settings.load", return_value={"mode": "characters", "checkpoint": "old-model"}), \
             patch("comic_workflow.comfyui.lora_labels", return_value={"catalog": True}) as catalog, \
             patch("comic_workflow.drawing_settings.loras_for_panel", side_effect=routed):
            result = with_render_plan(Mock(), "test", self.project, rows, "selected-model")
        catalog.assert_called_once()
        self.assertEqual(result["warnings"][0]["code"], "text_only_character")
        self.assertEqual(result["warnings"][0]["panels"], ["P001-K02"])
        self.assertEqual(result["panel_details"]["P001-K01"]["loras"][0]["character_ids"], ["A"])

    def test_deliberately_disabled_loras_do_not_warn_or_scan_catalog(self):
        with patch("comic_workflow.drawing_settings.load", return_value={"mode": "none", "checkpoint": "model"}), \
             patch("comic_workflow.comfyui.lora_labels") as catalog:
            result = with_render_plan(Mock(), "test", self.project, [{"panel_id": "P001-K01", "positive": "Alice reads."}])
        catalog.assert_not_called()
        self.assertFalse(result["warnings"])
        self.assertEqual(result["panel_details"]["P001-K01"]["loras"], [])

    def test_matching_cast_shares_routing_only_within_one_preview(self):
        self.project["pages"]["P001"]["panels"][1]["characters"] = [{"character_id": "A"}]
        rows = [{"panel_id": "P001-K01", "positive": "Alice reads."},
                {"panel_id": "P001-K02", "positive": "Alice waves."}]
        with patch("comic_workflow.drawing_settings.load", return_value={"mode": "characters", "checkpoint": "model"}), \
             patch("comic_workflow.comfyui.lora_labels", return_value={}), \
             patch("comic_workflow.drawing_settings.loras_for_panel", return_value=[]) as route:
            with_render_plan(Mock(), "test", self.project, rows)
            self.assertEqual(route.call_count, 1)
            with_render_plan(Mock(), "test", self.project, rows)
            self.assertEqual(route.call_count, 2)
