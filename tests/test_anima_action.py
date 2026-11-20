import unittest

from comic_workflow import anima, comfyui


class AnimaActionTests(unittest.TestCase):
    prompt = (
        "Nahida from Genshin Impact, 1girl, white-and-green dress.\n\n"
        "Reclining on grass, bent-knee kick toward the camera, foot in the foreground.\n\n"
        "Mildly annoyed expression, rounded 2.5D anime shading."
    )
    cfg = dict(width=768, height=1344, steps=28, cfg=5,
               sampler_name="res_multistep", scheduler="beta")

    def test_only_complete_action_contract_triggers(self):
        self.assertEqual(anima.action_mode_for_prompt(self.prompt), "reclining_kick")
        for unrelated in (
            "Nahida portrait, 2.5D, annoyed",
            "feet, foreground, 2.5D, annoyed",
            self.prompt.replace("bent-knee kick", "straight-leg stretch"),
            self.prompt.replace("Reclining", "Standing"),
            self.prompt.replace("2.5D", "watercolor"),
            self.prompt.replace("annoyed", "smiling"),
            self.prompt.replace("bent-knee kick", "no bent-knee kick"),
        ):
            with self.subTest(prompt=unrelated):
                self.assertEqual(anima.action_mode_for_prompt(unrelated), "standard")

    def test_chinese_action(self):
        self.assertEqual(anima.action_mode_for_prompt(
            "纳西妲躺在草地上，弯膝踢向镜头，脚在前景，微恼表情，圆润2.5D风格。"
        ), "reclining_kick")

    def test_literal_import_does_not_add_action_conditioning(self):
        graph = anima.graph(anima.CHECKPOINT, self.prompt, "blur", 42, {**self.cfg, "literal_prompt": True})
        self.assertNotIn("80", graph)
        self.assertEqual(graph["2"]["inputs"]["prompt"], self.prompt)
        self.assertEqual(graph["3"]["inputs"]["text"], "blur")

    def test_action_uses_two_semantic_conditions_on_one_unchanged_sampler(self):
        loras = [dict(file="anime/nahida.safetensors", weight=.7, clip_weight=0)]
        graph = anima.graph(anima.CHECKPOINT, self.prompt, "cropped foot", 42, self.cfg, loras)
        self.assertEqual(graph["2"]["inputs"]["prompt"], self.prompt)
        self.assertEqual(graph["3"]["inputs"]["text"], "cropped foot")
        self.assertEqual(graph["80"]["inputs"]["model"], ["20", 0])
        self.assertEqual(graph["80"]["inputs"]["native_clip"], ["20", 1])
        self.assertIn("white-and-green dress", graph["80"]["inputs"]["prompt"])
        self.assertEqual(graph["81"]["inputs"]["end"], graph["82"]["inputs"]["start"])
        self.assertEqual(graph["81"]["inputs"]["conditioning"], ["80", 0])
        self.assertEqual(graph["82"]["inputs"]["conditioning"], ["2", 0])
        sampler = graph["5"]["inputs"]
        self.assertEqual(sampler["positive"], ["83", 0])
        self.assertEqual(sampler["latent_image"], ["4", 0])
        self.assertEqual(sampler["seed"], 42)
        self.assertEqual(sampler["steps"], self.cfg["steps"])
        self.assertEqual(sampler["sampler_name"], self.cfg["sampler_name"])
        self.assertEqual(sum(n["class_type"] == "KSampler" for n in graph.values()), 1)

    def test_unrelated_anima_and_sdxl_keep_original_graph(self):
        plain = anima.graph(anima.CHECKPOINT, "Venti holding an apple", "", 42, self.cfg)
        self.assertNotIn("80", plain)
        self.assertEqual(plain["5"]["inputs"]["positive"], ["2", 0])
        sdxl = comfyui.graph("wai.safetensors", self.prompt, "", 42, self.cfg)
        self.assertNotIn("80", sdxl)
