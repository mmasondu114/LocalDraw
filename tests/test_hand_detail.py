import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image, ImageChops
from comic_workflow import comfyui, engine as e, hand_detail


class HandDetailTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        data_patch = patch.object(hand_detail, "DATA", self.root / "data")
        data_patch.start(); self.addCleanup(data_patch.stop)
        self.image = self.root / "1.png"
        Image.new("RGB", (320, 160), "navy").save(self.image)
        self.original = self.image.read_bytes()
        self.settings = {"detector": "test.pt", "sha256": "test", "confidence": .3,
                         "max_regions": 4, "crop_size": 768, "denoise": .38,
                         "steps": 24, "timeout_seconds": 1}
        self.cfg = {"source_directory": str(self.root / "comfy"), "width": 320,
                    "height": 160, "steps": 28, "cfg": 5., "sampler_name": "euler_ancestral",
                    "scheduler": "normal", "endpoint": "http://127.0.0.1:8188"}
        self.found = {"size": [320, 160], "boxes": [
            {"box": [100, 65, 125, 95], "confidence": .8}]}

    def render(self, graph, folder, cfg, settings, state, persist):
        for key in ("210", "211"):
            self.assertTrue((self.root / "data/comfy-input" / graph[key]["inputs"]["image"]).is_file())
        with Image.open(folder / "crop.png") as image:
            size = image.size
        result = folder / "result.png"
        Image.new("RGB", size, "white").save(result)
        state["prompt_id"] = "mock-detail"
        persist()
        return result

    def run_detail(self, renderer=None, found=None):
        with (patch.object(hand_detail, "config", return_value=self.settings),
              patch.object(hand_detail, "detect", return_value=found or self.found),
              patch.object(hand_detail, "render", side_effect=renderer or self.render)):
            return hand_detail.enhance(self.image, "chosen.safetensors", self.cfg,
                                       42, [], Mock())

    def test_compositing_preserves_canvas_and_pixels_outside_hand_mask(self):
        report = self.run_detail()
        self.assertEqual(report["status"], "done")
        self.assertEqual((self.root / "base-1.png").read_bytes(), self.original)
        with Image.open(self.image) as result, Image.open(self.root / "base-1.png") as base:
            self.assertEqual(result.size, base.size)
            changed = ImageChops.difference(result, base).getbbox()
            self.assertIsNotNone(changed)
            self.assertGreater(changed[0], 80)
            self.assertLess(changed[2], 145)
            self.assertGreater(changed[1], 40)
            self.assertLess(changed[3], 120)
            self.assertEqual(result.getpixel((0, 0)), base.getpixel((0, 0)))

    def test_no_hands_keeps_original_without_submitting_or_creating_comfy_inputs(self):
        render = Mock()
        report = self.run_detail(render, {"size": [320, 160], "boxes": []})
        self.assertEqual(report["status"], "no_hands")
        render.assert_not_called()
        self.assertEqual(self.image.read_bytes(), self.original)
        self.assertFalse((self.root / "data/comfy-input").exists())

    def test_touching_hands_merge_and_disjoint_regions_are_bounded(self):
        touching = [{"box": [10, 10, 30, 40]}, {"box": [31, 11, 50, 41]}]
        self.assertEqual(len(hand_detail.regions(touching, (500, 100), 4)), 1)
        boxes = [{"box": [10 + 80 * n, 10, 30 + 80 * n, 40], "confidence": 1 - n / 20}
                 for n in range(10)]
        result = hand_detail.regions(boxes, (1000, 100), 99)
        self.assertEqual(len(result), 8)
        self.assertEqual(len(hand_detail.regions(boxes, (1000, 100), 4)), 4)
        invalid = [{"box": [float("nan"), 0, 10, 10]}, {"box": [80, 80, 5, 5]},
                   {"box": [-100, -100, -20, -20]}]
        self.assertEqual(hand_detail.regions(invalid, (100, 100), 4), [])

    def test_later_pass_failure_never_commits_partial_enhancement(self):
        found = {"size": [320, 160], "boxes": [{"box": [30, 65, 50, 95]},
                                                        {"box": [230, 65, 250, 95]}]}
        calls = []
        def fail_later(*args):
            calls.append(args)
            if len(calls) == 2:
                raise ValueError("offline failure")
            return self.render(*args)
        report = self.run_detail(fail_later, found)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.image.read_bytes(), self.original)
        self.assertIn("offline failure", report["error"])

    def test_uncertain_submission_is_logged_and_never_retried(self):
        state = {}
        with patch.object(comfyui, "api", side_effect=TimeoutError("unknown submission")) as api:
            with self.assertRaises(hand_detail.PendingDetail):
                hand_detail.render({}, self.root, self.cfg, self.settings, state, Mock())
        self.assertEqual(api.call_count, 1)
        self.assertTrue(state["submission_pending"])
        self.assertTrue(state["client_id"])

    def test_pending_detail_preserves_base_and_reports_uncertainty(self):
        report = self.run_detail(Mock(side_effect=hand_detail.PendingDetail("check queue")))
        self.assertEqual(report["status"], "uncertain")
        self.assertEqual(self.image.read_bytes(), self.original)
        self.assertEqual(e.read(self.root / "hand-detail-1/status.json")["status"], "uncertain")

    def test_detail_graph_uses_selected_model_and_encoded_original_pose(self):
        loras = [{"file": "style.safetensors", "weight": .4, "clip_weight": .4}]
        original = copy.deepcopy(loras)
        graph = hand_detail.workflow("NoobAI-XL-v1.1.safetensors", self.cfg, self.settings,
                                     43, "hands/crop.png", "hands/mask.png", loras)
        self.assertEqual(graph["1"]["inputs"]["ckpt_name"], "NoobAI-XL-v1.1.safetensors")
        self.assertEqual(graph["213"]["class_type"], "VAEEncode")
        self.assertEqual(graph["5"]["inputs"]["denoise"], .38)
        self.assertEqual(graph["5"]["inputs"]["latent_image"], ["214", 0])
        self.assertEqual(graph["8"]["inputs"]["lora_name"], "style.safetensors")
        self.assertEqual(loras, original)
        self.assertNotIn("2people", graph["2"]["inputs"]["text"])

    def test_detection_failure_keeps_original_and_exposes_reason(self):
        with (patch.object(hand_detail, "config", return_value=self.settings),
              patch.object(hand_detail, "detect", side_effect=ValueError("missing detector")),
              patch.object(hand_detail, "render") as render):
            report = hand_detail.enhance(self.image, "chosen", self.cfg, 42, [], Mock())
        render.assert_not_called()
        self.assertEqual(report["status"], "failed")
        self.assertIn("missing detector", report["error"])
        self.assertEqual(self.image.read_bytes(), self.original)
