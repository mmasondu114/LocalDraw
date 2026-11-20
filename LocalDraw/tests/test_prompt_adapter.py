import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from comic_workflow import engine as e, model
from comic_workflow.prompt_adapter import compile_prompt, detailed, profile, split_tags
from comic_workflow.repository import Repository


class PromptAdapterTests(unittest.TestCase):
    def test_model_dialects_preserve_identity_action_camera_and_negative_constraints(self):
        scene = "doctor (arknights), black hood, covered face, (gloved hand, pen:1.2), pen tip touching document, top-down view, high score, great score, masterpiece"
        negative = "white coat, extra people, low score, bad score"
        for checkpoint, expected in [
            ("animagine-xl-4.0-opt.safetensors", "high score"),
            ("animagine-xl-3.1.safetensors", "very aesthetic"),
            ("NoobAI-XL-v1.1.safetensors", "highres"),
            ("Illustrious-XL-v2.0.safetensors", "best quality"),
        ]:
            with self.subTest(checkpoint=checkpoint):
                positive, neg, dialect = compile_prompt(scene, negative, checkpoint)
                self.assertIn(expected, positive)
                for fact in ["doctor (arknights)", "black hood", "covered face", "(gloved hand, pen:1.2)", "pen tip touching document", "top-down view"]:
                    self.assertIn(fact, positive)
                self.assertIn("white coat", neg)
                self.assertIn("extra people", neg)
                self.assertNotIn("furry", neg)
                if dialect["id"] != "animagine4":
                    self.assertNotIn("high score", positive)
                    self.assertNotIn("low score", neg)
                self.assertEqual(compile_prompt(positive, neg, checkpoint)[:2], (positive, neg))

    def test_unknown_model_is_not_guessed_and_weighted_phrases_are_not_split(self):
        self.assertEqual(profile("C:\\models\\NoobAI-XL-v1.1.safetensors")["id"], "noobai11")
        self.assertEqual(profile("my-noobai-experiment.safetensors")["id"], "generic")
        self.assertEqual(compile_prompt("custom trigger, high score", "custom negative", "custom.safetensors")[:2],
                         ("custom trigger, high score", "custom negative"))
        self.assertEqual(split_tags("(gloved hand, pen:1.2), desk"), ["(gloved hand, pen:1.2)", "desk"])
        self.assertFalse(detailed({"payload": {}}))

    def test_qwen_receives_selected_model_and_must_produce_detailed_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository(Path(tmp) / "projects")
            pid = repo.create(None, True)
            p = repo.get(pid)
            original = copy.deepcopy(p)
            panel = p["pages"]["P001"]["panels"][0]
            ids = [c["character_id"] for c in panel["characters"]]
            payload = {
                "panel_id": panel["id"], "positive": "person at desk, gloved hand gripping pen, pen tip touching paper",
                "negative": "text, extra people", "composition_variants": [f"view {i}, gloved hand gripping pen, pen tip touching paper" for i in range(6)],
                "character_anchors": [{"character_id": cid, "tags": "dark coat"} for cid in ids],
                "adaptation_notes": "人工离线测试，非真实生成",
                "framing": {"version": 2, "camera": "upper body", "forbidden": "full body",
                            "subjects": [{"character_id": cid, "visible": "upper", "region": [0, 0, 1, 1]} for cid in ids],
                            "guide_lines": [], "guide_ellipses": [], "guide_note": "测试"},
                "visual_plan": {"version": 1, "detailed_scene": "人工测试：人物握笔，笔尖接触桌面文件。", "must_show": ["笔尖接触文件"], "relationships": ["手握笔，笔尖接触纸面"], "uncertain": []},
            }
            response = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload)}}]}
            captured = []
            def stream(endpoint, body, *args):
                captured.append(body)
                return copy.deepcopy(response)
            with patch.object(model, "count_messages", return_value=100), patch.object(model, "chat_stream", side_effect=stream):
                actual, _ = model.generate(p, "drawing_prompt", panel["id"], "", Path(tmp) / "run",
                                           drawing_checkpoint="NoobAI-XL-v1.1.safetensors")
            self.assertEqual(actual, payload)
            body = captured[0]
            context = json.loads(body["messages"][1]["content"])
            self.assertEqual(context["image_model"]["id"], "noobai11")
            self.assertIn("visual_plan", body["response_format"]["json_schema"]["schema"]["required"])
            self.assertEqual(p, original)
            # Old histories remain valid; future generation may not omit the new plan.
            old = copy.deepcopy(payload)
            old.pop("visual_plan")
            e.merged(p, "drawing_prompt", panel["id"], old)
            response["choices"][0]["message"]["content"] = json.dumps(old)
            with patch.object(model, "count_messages", return_value=100), patch.object(model, "chat_stream", side_effect=stream):
                with self.assertRaises(e.jsonschema.ValidationError):
                    model.generate(p, "drawing_prompt", panel["id"], "", Path(tmp) / "bad-run",
                                   drawing_checkpoint="NoobAI-XL-v1.1.safetensors")


if __name__ == "__main__":
    unittest.main()
