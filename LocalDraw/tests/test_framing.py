import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from PIL import Image
from comic_workflow import framing, comfyui, engine as e


class FramingTests(unittest.TestCase):



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




