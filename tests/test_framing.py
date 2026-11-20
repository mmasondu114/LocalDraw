import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from PIL import Image
from comic_workflow import framing, comfyui, engine as e


class FramingTests(unittest.TestCase):
    def test_full_body_requests_do_not_include_negated_or_conflicting_camera_terms(self):
        for text in ('full body, sitting on a bench', 'head-to-toe portrait', '两个人全身坐在长椅上'):
            with self.subTest(text=text):
                self.assertTrue(framing.explicit_full_body(text))
        for text in ('not full body, standing', 'no full-body shot', '不要全身', '不是全身',
                     'full body, upper body portrait', 'full body, waist up', '全身，脸部特写'):
            with self.subTest(text=text):
                self.assertFalse(framing.explicit_full_body(text))

    def test_upper_body_requests_exclude_negated_camera_instructions(self):
        for text in ('waist up, hugging', 'upper-body portrait', '两个人半身拥抱', '腰部以上'):
            self.assertTrue(framing.explicit_upper_body(text),text)
        for text in ('not waist up, full body', 'no upper body', '不要半身，全身', '不要腰部以上'):
            self.assertFalse(framing.explicit_upper_body(text),text)

    def test_pilot_progress_does_not_finish_at_first_image(self):
        from comic_workflow import drawing_tools

        project = {"pages": {"P001": {"panels": [{"id": "P001-K01"}]}}}
        app = Mock()
        app.repo.get.return_value = project
        events = []

        def prepare(*args):
            args[-1]("准备提示词")

        def render(*args, **kwargs):
            args[3]({"progress": {"completed": 1, "total": 1}})
            return {"id": "batch", "images": []}

        def review(*args):
            args[-1]("审核中")

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(drawing_tools, "prepare", side_effect=prepare),
            patch.object(drawing_tools, "story_fingerprint", return_value="unchanged"),
            patch.object(drawing_tools.drawing_settings, "load", return_value=None),
            patch.object(drawing_tools.comfyui, "run_batch", side_effect=render),
            patch.object(drawing_tools, "review_batch", side_effect=review),
        ):
            app.repo.folder.return_value = Path(tmp)
            drawing_tools.pilot(
                app,
                "project",
                {"base_hash": e.digest(project), "targets": ["P001-K01"]},
                events.append,
                "job",
            )
        values = [x["progress"]["completed"] for x in events]
        self.assertEqual(values, sorted(values))
        self.assertTrue(all(x["progress"]["total"] == 5 for x in events))
        self.assertLess(values[-2], 5)
        self.assertEqual(values[-1], 5)
        app.model.stop.assert_called()
        app.comfy.stop.assert_called_once()

    def payload(self):
        return {
            "positive": "gloved hand pushing a plate",
            "negative": "text",
            "composition_variants": ["gloved hand on desk"] * 6,
            "character_anchors": [
                {
                    "character_id": "C002",
                    "tags": "black fingerless gloves, white sleeve",
                }
            ],
            "framing": {
                "version": 2,
                "camera": "top-down close-up",
                "forbidden": "full body, head",
                "subjects": [
                    {
                        "character_id": "C002",
                        "visible": "hands",
                        "region": [0, 0, 0.6, 1],
                    }
                ],
                "guide_lines": [[[0.1, 0.3], [0.6, 0.5]]],
                "guide_ellipses": [[0.5, 0.3, 0.8, 0.7]],
                "guide_note": "手推盘子",
            },
        }

    def test_hand_detail_rejects_full_costume_but_keeps_exclusions(self):
        p = self.payload()
        framing.validate_framing(p)
        before = copy.deepcopy(p)
        pos, neg = framing.apply_camera(p["positive"], p["negative"], p)
        self.assertTrue(pos.startswith("top-down close-up"))
        self.assertIn("full body", neg)
        self.assertEqual(p, before)
        p["character_anchors"][0]["tags"] += " red hair, halo"
        with self.assertRaisesRegex(ValueError, "手部特写"):
            framing.validate_framing(p)

    def test_partial_reference_does_not_feed_full_body_image(self):
        from comic_workflow.reference_images import references_for_panel

        p = self.payload()
        with patch(
            "comic_workflow.reference_images.panel_characters",
            return_value=[
                {
                    "id": "C002",
                    "name": "能天使",
                    "saved_character": {"reference_image_id": "any"},
                }
            ],
        ):
            refs = references_for_panel(
                {},
                {"characters": [{"character_id": "C002", "position": "left"}]},
                p["framing"],
            )
        self.assertEqual(refs, [])

    def test_guide_is_repeatable_and_bad_boxes_fail(self):
        p = self.payload()
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(framing, "DATA", Path(tmp)),
        ):
            a = framing.guide_image(p, 128, 96)
            b = framing.guide_image(p, 128, 96)
            self.assertEqual(a, b)
            with Image.open(Path(tmp) / "comfy-input" / a) as im:
                self.assertEqual(im.size, (128, 96))
                self.assertIsNotNone(im.getbbox())
        p["framing"]["subjects"][0]["region"] = [0.8, 0, 0.1, 1]
        with self.assertRaisesRegex(ValueError, "左上到右下"):
            framing.validate_framing(p)

    def test_control_conditions_both_branches_after_lora(self):
        cfg = {
            "width": 128,
            "height": 128,
            "steps": 28,
            "cfg": 5,
            "sampler_name": "euler_ancestral",
            "scheduler": "normal",
        }
        graph = comfyui.graph(
            "test",
            "positive",
            "negative",
            1,
            cfg,
            [{"file": "style", "weight": 0.7, "clip_weight": 0.7}],
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(framing, "ROOT", Path(tmp)),
        ):
            root = Path(tmp)
            (root / "models/controlnet").mkdir(parents=True)
            (root / "models/controlnet/test").write_bytes(b"x")
            e.save(
                root / "config/control-model.json",
                {"name": "test", "size": 1, "strength": 0.7, "end_percent": 0.85},
            )
            framing.apply_control(graph, "guides/test.png")
        self.assertEqual(graph["5"]["inputs"]["model"], ["8", 0])
        self.assertEqual(graph["203"]["inputs"]["positive"], ["2", 0])
        self.assertEqual(graph["5"]["inputs"]["negative"], ["203", 1])

    def test_masked_repair_composites_original_outside_mask(self):
        cfg = {
            "width": 128,
            "height": 128,
            "steps": 28,
            "cfg": 5,
            "sampler_name": "euler_ancestral",
            "scheduler": "normal",
        }
        graph = comfyui.graph("test", "positive", "negative", 1, cfg)
        framing.apply_local_repair(
            graph, "original.png", "mask.png", "hand holding pen"
        )
        self.assertEqual(graph["7"]["inputs"]["images"], ["214", 0])
        self.assertEqual(graph["214"]["inputs"]["destination"], ["210", 0])
        self.assertEqual(graph["214"]["inputs"]["mask"], ["212", 0])
        self.assertEqual(graph["5"]["inputs"]["latent_image"], ["213", 0])
