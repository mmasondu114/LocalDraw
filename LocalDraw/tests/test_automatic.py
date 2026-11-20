import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from comic_workflow import engine as e, comfyui
from comic_workflow.repository import Repository
from comic_workflow.automatic import (
    script_pages,
    preserve_source_lettering,
    prepare_project,
    run_automatic,
)
from comic_workflow.reference_images import apply_references
from comic_workflow.visual_policy import apply_visual_policy


class AutomaticTests(unittest.TestCase):
    def test_explicit_mask_policy_preserves_dialogue_and_state(self):
        p = e.read(Path(__file__).resolve().parents[1] / "config/example.json")
        page = copy.deepcopy(p["pages"]["P001"])
        p["visual_policy"] = {
            "fully_covered_faces": [page["panels"][0]["characters"][0]["character_id"]]
        }
        page["panels"][0]["characters"][0]["expression"] = "专注（紧抿嘴角）"
        page["panels"][0]["drawing_description"] = "露出紧抿的嘴角"
        fixed, changes = apply_visual_policy(page, p)
        self.assertTrue(changes)
        self.assertNotIn("嘴角", fixed["panels"][0]["drawing_description"])
        self.assertEqual(fixed["lettering"], page["lettering"])
        self.assertEqual(fixed["state_out"], page["state_out"])
        again, changes = apply_visual_policy(fixed, p)
        self.assertEqual(again, fixed)
        self.assertEqual(changes, [])

    def test_actual_sections_and_dialogue_are_preserved(self):
        source = "【页数建议】：4页\n### 第一页：开场\n**Panel 1**\n**博士**：“原话，不能改。”\n**音效**：*滴滴*（提示音）\n### 第二页：结束\n**Panel 1**\n**能天使**（通讯中）：“收到。”"
        parsed = script_pages(source)
        self.assertEqual(len(parsed), 2)
        page = {"panels": [{"id": "P001-K01"}], "lettering": []}
        result = preserve_source_lettering(
            page, parsed["P001"], [{"id": "C001", "name": "博士"}]
        )
        self.assertEqual(
            [t["text"] for t in result["lettering"]], ["原话，不能改。", "滴滴"]
        )
        self.assertEqual([t["speaker_id"] for t in result["lettering"]], ["C001", None])
        self.assertEqual(page["lettering"], [])

    def test_source_bubble_annotations_are_not_duplicated_on_repeated_processing(self):
        characters = [{"id": "C001", "name": "能天使"}]
        for annotation in ["（画外音）", "（通讯中）", "（通讯中，渐弱）"]:
            for position in [
                "画面右上方",
                "画面右上方；" + annotation,
                annotation + "，气泡指向终端",
            ]:
                with self.subTest(annotation=annotation, position=position):
                    page = {
                        "panels": [{"id": "P001-K01"}],
                        "lettering": [
                            {
                                "panel_id": "P001-K01",
                                "order": 1,
                                "bubble_position": position,
                            }
                        ],
                    }
                    original = copy.deepcopy(page)
                    source = {"panels": [f"**能天使** {annotation} ：“收到。”"]}
                    once = preserve_source_lettering(page, source, characters)
                    twice = preserve_source_lettering(once, source, characters)
                    self.assertEqual(once, twice)
                    self.assertEqual(page, original)
                    text = twice["lettering"][0]
                    self.assertEqual(text["bubble_position"].count(annotation), 1)
                    self.assertTrue(text["bubble_position"].startswith(position))
                    self.assertEqual(text["text"], "收到。")
                    self.assertEqual(text["speaker_id"], "C001")

    def test_reference_graph_chains_after_lora_and_uses_separate_masks(self):
        cfg = comfyui.config()
        g = comfyui.graph(
            "test",
            "prompt",
            "negative",
            1,
            cfg,
            [{"file": "style", "weight": 0.7, "clip_weight": 0.7}],
        )
        refs = [
            {"image": "a.png", "position": "左侧", "weight": 0.7, "end_at": 0.8},
            {"image": "b.png", "position": "右侧", "weight": 0.7, "end_at": 0.8},
        ]
        apply_references(g, refs, cfg)
        self.assertEqual(g["111"]["inputs"]["model"], ["8", 0])
        self.assertEqual(g["121"]["inputs"]["model"], ["111", 0])
        self.assertEqual(g["5"]["inputs"]["model"], ["121", 0])
        self.assertNotEqual(
            g["111"]["inputs"]["attn_mask"], g["121"]["inputs"]["attn_mask"]
        )
        self.assertEqual(g["110"]["inputs"]["image"], "a.png")
        self.assertEqual(g["120"]["inputs"]["image"], "b.png")
        self.assertEqual(g["2"]["inputs"]["clip"], ["8", 1])

    def test_failure_always_unloads_models_and_persists_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository(tmp)
            pid = repo.create(None, True)
            model, comfy = Mock(), Mock()
            model.status.return_value = {"ready": False}
            app = SimpleNamespace(repo=repo, model=model, comfy=comfy)
            with patch(
                "comic_workflow.automatic.prepare_project",
                side_effect=ValueError("test failure"),
            ):
                with self.assertRaisesRegex(ValueError, "test failure"):
                    run_automatic(app, pid, lambda x: None, "test")
            model.stop.assert_called_once()
            comfy.stop.assert_called_once()
            report = e.read(repo.folder(pid) / "runs/test/automatic.json")
            self.assertEqual(report["status"], "failed")
            self.assertTrue(report["models_unloaded"])

    def test_prepare_preserves_script_and_versions_existing_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository(tmp)
            pid = repo.create(None, True)
            p = repo.get(pid)
            p["pages"] = {}
            p["script"] = (
                "### 第一页：开场\n**Panel 1**\n画面\n### 第二页：收尾\n**Panel 1**\n结尾"
            )
            e.save(repo.folder(pid) / "project.json", p)
            updated = prepare_project(repo, pid)
            self.assertEqual(updated["script"], p["script"])
            self.assertEqual(updated["version"], p["version"] + 1)
            self.assertEqual(
                [x["panel_count"] for x in updated["plan"]["pages"]], [1, 1]
            )
            self.assertEqual(prepare_project(repo, pid), updated)
