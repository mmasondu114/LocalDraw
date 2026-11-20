import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from comic_workflow import engine as e, quick_sample
from comic_workflow.characters import CharacterLibrary, DETAILS
from comic_workflow.repository import Repository


class PromptTranslationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = SimpleNamespace(repo=Repository(self.root / "projects"), prepare_language=Mock(), model=Mock())
        library = CharacterLibrary(self.root / "characters")
        profile = {key: "虚构角色的详细资料，供翻译流程测试使用。" for key in DETAILS}
        profile.update(observed=[], suggested=[], unknowns=[])
        library.save("甲", profile, {"mode": "text"})
        self.request = {"text": "雪地上的全身舞姿", "negative": "文字，水印",
                        "characters": [{"name": "甲", "revision": 1, "prompt": "蓝发，白裙"}]}
        self.result = {"positive": "1girl, solo, full body, dancing, snow", "negative": "text, watermark",
                       "characters": [{"name": "甲", "prompt": "blue hair, white dress"}]}

    def test_translation_is_reviewable_without_history_and_unloads_without_drawing(self):
        with patch("comic_workflow.paths.DATA", self.root), \
             patch("comic_workflow.creative.structured", return_value=(self.result, {})), \
             patch("comic_workflow.comfyui.run_batch") as render:
            result = quick_sample.translate(self.app, self.request, Mock(), "test")
        self.assertEqual(result, self.result)
        self.assertFalse((self.root / "prompt-translations").exists())
        self.assertEqual(self.app.repo.listing(), [])
        render.assert_not_called()
        self.app.model.stop.assert_not_called()  # Jobs owns final idle release

    def test_failed_loading_or_translation_unloads_and_does_not_overwrite_input(self):
        for stage in ["load", "generate", "identity", "language", "chat_card"]:
            with self.subTest(stage=stage):
                self.app.model.reset_mock()
                self.app.prepare_language.side_effect = RuntimeError("load failed") if stage == "load" else None
                result = {**self.result}
                if stage == "identity":
                    result["characters"] = []
                if stage == "language":
                    result["positive"] = "仍是中文"
                if stage == "chat_card":
                    result["characters"] = [{"name": "甲", "prompt": "blue hair\nPersonality: kind"}]
                with patch("comic_workflow.paths.DATA", self.root), \
                     patch("comic_workflow.creative.structured", return_value=(result, {}),
                           side_effect=RuntimeError("generate failed") if stage == "generate" else None), \
                     self.assertRaises((RuntimeError, ValueError)):
                    quick_sample.translate(self.app, self.request, Mock(), stage)
                self.app.model.stop.assert_not_called()  # Jobs owns final idle release
                self.assertFalse((self.root / "prompt-translations" / stage / "draft.json").exists())
                self.assertEqual(self.request["text"], "雪地上的全身舞姿")
