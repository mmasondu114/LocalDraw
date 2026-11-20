import copy
import unittest

from comic_workflow import comfyui, regional_prompt


class RegionalPromptTests(unittest.TestCase):
    def setUp(self):
        self.spec = {"text": "2people, holding hands, room", "regional_characters": True,
                     "characters": [
                         {"name": "阿米娅", "prompt": "rabbit ears, maid dress", "region": [0, 0, 0.5, 1]},
                         {"name": "博士", "prompt": "black hood, full-face mask", "region": [0.5, 0, 1, 1]},
                     ]}
        self.cfg = {"width": 1216, "height": 832, "steps": 28, "cfg": 5,
                    "sampler_name": "euler_ancestral", "scheduler": "normal"}

    def test_appearances_are_separately_encoded_and_masked_into_the_sampler(self):
        graph = comfyui.graph("test.safetensors", "global plus both appearances", "text", 1, self.cfg)
        graph["2"]["inputs"]["clip"] = ["lora-clip", 1]
        original = copy.deepcopy(self.spec)
        regions = regional_prompt.apply(graph, self.spec, self.cfg)
        self.assertTrue(graph["2"]["inputs"]["text"].startswith("2people"))
        self.assertEqual(graph["2"]["inputs"]["text"].count("2people"), 1)
        self.assertEqual(graph["600"]["inputs"], {"text": "rabbit ears, maid dress", "clip": ["lora-clip", 1]})
        self.assertEqual(graph["610"]["inputs"]["text"], "black hood, full-face mask")
        self.assertEqual(graph["613"]["inputs"]["x"], 608)
        self.assertEqual(graph["604"]["inputs"]["mask"], ["603", 0])
        self.assertEqual(graph["614"]["inputs"]["mask"], ["613", 0])
        self.assertEqual(graph["615"]["inputs"]["conditioning_1"], ["605", 0])
        self.assertEqual(graph["5"]["inputs"]["positive"], ["615", 0])
        self.assertEqual([r["name"] for r in regions], ["阿米娅", "博士"])
        self.assertEqual(self.spec, original)

    def test_legacy_requests_keep_the_original_graph(self):
        graph = comfyui.graph("test.safetensors", "raw", "", 1, self.cfg)
        original = copy.deepcopy(graph)
        self.assertEqual(regional_prompt.apply(graph, {}, self.cfg), [])
        self.assertEqual(graph, original)

    def test_ambiguous_or_overlapping_regions_keep_whole_scene_graph(self):
        for area in [None, [0, 0, 0.7, 1], [0.5, 0, 0.5, 1]]:
            spec = copy.deepcopy(self.spec)
            spec["characters"][1]["region"] = area
            graph = {"unchanged": {}}
            with self.subTest(area=area):
                self.assertEqual(regional_prompt.apply(graph, spec, self.cfg), [])
            self.assertEqual(graph, {"unchanged": {}})

    def test_hooked_scene_keeps_the_cast_and_avoids_unhooked_duplicate_roles(self):
        spec = {'regional_characters': True,
                'text': '3girls, standing side by side, medium shot, park',
                'characters': [{'name': str(i), 'prompt': '1girl',
                                'region': [i/3, 0, (i+1)/3, 1]} for i in range(3)]}
        cfg = {**self.cfg, 'regional_lora_hooks': True}
        for passes in [None, spec['characters'][:2]]:
            graph = comfyui.graph('test', spec['text'], '', 1, cfg)
            regional_prompt.apply(graph, spec, cfg, passes)
            self.assertIn('3girls', graph['2']['inputs']['text'])
            self.assertIn('600', graph)
        graph = comfyui.graph('test', spec['text'], '', 1, cfg)
        regional_prompt.apply(graph, spec, cfg, spec['characters'])
        self.assertIn('3girls', graph['2']['inputs']['text'])
        self.assertNotIn('600', graph)
