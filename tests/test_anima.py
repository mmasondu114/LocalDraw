import json
import struct
import tempfile
import unittest
import io
from pathlib import Path
from unittest.mock import patch
from PIL import Image

from comic_workflow import anima, comfyui
from comic_workflow.lora_inspection import inspect_lora
from comic_workflow.lora_names import search_identity


class AnimaTests(unittest.TestCase):
    def test_anima_components_and_lora_conditioning(self):
        cfg = dict(width=832, height=1216, steps=28, cfg=5, sampler_name="euler", scheduler="beta")
        graph = comfyui.graph(anima.CHECKPOINT, "Ganyu in a garden", "low quality", 42, cfg,
                              [dict(file="Anima/原神/甘雨.safetensors", weight=.7, clip_weight=0)])
        self.assertEqual(graph["1"]["class_type"], "Anima38BV2Loader")
        self.assertEqual(graph["4"]["class_type"], "EmptySD3LatentImage")
        self.assertEqual(graph["6"]["inputs"]["vae"], ["12", 0])
        self.assertEqual(graph["2"]["inputs"]["model"], graph["5"]["inputs"]["model"])
        self.assertEqual(graph["2"]["inputs"]["native_clip"], ["20", 1])
        self.assertNotIn("CheckpointLoaderSimple", [n["class_type"] for n in graph.values()])

    def test_lora_family_rejects_cross_architecture(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.safetensors"
            for family, target, expected in [("sdxl", anima.CHECKPOINT, False),
                                              ("anima", anima.CHECKPOINT, True),
                                              ("anima", "sdxl.safetensors", False)]:
                header = json.dumps({"__metadata__": {"ss_base_model_version": family},
                    "weight": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}).encode()
                path.write_bytes(struct.pack("<Q", len(header)) + header + bytes(4))
                self.assertIs(inspect_lora(path, target)["structurally_compatible"], expected)

    def test_nested_game_identity(self):
        roster = {("原神", "甘雨"): {"english": "Ganyu", "pinyin": "gan yu"}}
        self.assertIn("Ganyu", search_identity("Anima/原神/甘雨_123.safetensors", roster)["english_aliases"])

    def test_staged_cuda_requires_cuda_and_matching_launch_mode(self):
        manager = comfyui.ComfyManager()
        cfg = {"memory_mode": "staged_cuda", "checkpoint": anima.CHECKPOINT}
        stats = {"devices": [{"type": "cuda"}], "system": {"argv": ["--highvram"]}}
        with patch.object(comfyui, "discover", return_value={"ready": True}), \
             patch.object(comfyui, "config", return_value=cfg), \
             patch.object(comfyui, "api", return_value=stats):
            manager.start()
            stats["system"]["argv"] = ["--gpu-only"]
            with self.assertRaisesRegex(ValueError, "显存模式"):
                manager.start()
            stats["system"]["argv"] = ["--highvram"]
            stats["devices"] = [{"type": "cpu"}]
            with self.assertRaisesRegex(ValueError, "CUDA"):
                manager.start()

    def test_auto_cuda_accepts_offloading_but_rejects_cpu_and_forced_vram(self):
        manager = comfyui.ComfyManager()
        cfg = {"memory_mode": "auto_cuda", "checkpoint": anima.CHECKPOINT}
        stats = {"devices": [{"type": "cuda"}], "system": {"argv": ["--reserve-vram", "2", "--disable-smart-memory"]}}
        with patch.object(comfyui, "config", return_value=cfg), \
             patch.object(comfyui, "discover", return_value={"ready": True}), \
             patch.object(comfyui, "api", return_value=stats):
            manager.start()
            for flag in ("--cpu", "--gpu-only", "--highvram"):
                stats["system"]["argv"].append(flag)
                with self.assertRaisesRegex(ValueError, "显存模式"):
                    manager.start()
                stats["system"]["argv"].pop()
            stats["devices"] = [{"type": "cpu"}]
            with self.assertRaisesRegex(ValueError, "CUDA"):
                manager.start()

    def test_batch_submits_anima_without_sdxl_postprocessors(self):
        from tests.test_comfyui import DrawingTests
        fixture = DrawingTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.addCleanup(fixture.tmp.cleanup)
        fixture.prompts = []
        data = io.BytesIO()
        Image.new("RGB", (32, 32), "white").save(data, format="PNG")

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, *args): return data.getvalue()

        class Opener:
            def open(self, *args, **kwargs): return Response()

        with patch.object(comfyui, "discover", return_value={"ready": True, "checkpoints": [anima.CHECKPOINT]}), \
             patch.object(comfyui, "api", fixture.mock_api), \
             patch("comic_workflow.telemetry.websocket.create_connection", side_effect=ConnectionError("test")), \
             patch("urllib.request.build_opener", return_value=Opener()):
            result = comfyui.run_batch(fixture.repo, fixture.pid, fixture.target, lambda _: None,
                                       anima.CHECKPOINT, count=1)
        self.assertEqual(result["status"], "done")
        self.assertEqual(fixture.prompts[0]["2"]["class_type"], "Anima38BV2Prompt")
        self.assertFalse(result["settings"]["hires_fix"])
