import unittest
from comic_workflow import comfyui, delta_style, prompt_models


class DeltaStyleTests(unittest.TestCase):
    def graph(self, checkpoint=delta_style.CHECKPOINT, **options):
        cfg = {**comfyui.config(), **prompt_models.defaults(checkpoint), "width": 960, "height": 1280, **options}
        return comfyui.graph(checkpoint, "a person beside a lake", "watermark", 17, cfg)

    def test_default_reaches_both_conditioning_nodes_without_changing_sampling(self):
        graph = self.graph()
        self.assertIn(delta_style.POSITIVE, graph["2"]["inputs"]["text"])
        self.assertIn("a person beside a lake", graph["2"]["inputs"]["text"])
        self.assertIn(delta_style.NEGATIVE, graph["3"]["inputs"]["text"])
        self.assertEqual(graph["5"]["inputs"]["seed"], 17)
        self.assertEqual(graph["5"]["inputs"]["cfg"], 4.5)

    def test_disabled_keeps_exact_input(self):
        graph = self.graph(delta_matte_skin=False)
        self.assertEqual(graph["2"]["inputs"]["text"], "a person beside a lake")
        self.assertEqual(graph["3"]["inputs"]["text"], "watermark")

    def test_other_model_is_unaffected_even_when_enabled(self):
        graph = self.graph("anima-aesthetic-v1.1.safetensors", delta_matte_skin=True)
        self.assertEqual(graph["2"]["inputs"]["text"], "a person beside a lake")
        self.assertEqual(graph["3"]["inputs"]["text"], "watermark")

    def test_reusing_effective_prompt_does_not_repeat_preset(self):
        cfg = {delta_style.KEY: True}
        first = delta_style.apply(delta_style.CHECKPOINT, "scene", "", cfg)
        self.assertEqual(first, delta_style.apply(delta_style.CHECKPOINT, *first, cfg))
