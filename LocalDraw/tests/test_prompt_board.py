import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image
from comic_workflow import comfyui, engine as e, prompt_board
from comic_workflow.repository import Repository


class PromptBoardTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Repository(Path(temp.name) / "projects")
        self.addCleanup(self.repo._temporary.cleanup)
        self.app = SimpleNamespace(repo=self.repo, characters=Mock(listing=lambda: []), night_queue=Mock())
        self.new = {"import_key": "a" * 32, "title": "雨夜",
                    "storyboard": "P01 K01 | 1:1 | 车站\nP02 K01 | 16:9 | 空街"}
        self.pid = prompt_board.create(self.app, self.new)["project"]

    def test_storyboard_creation_and_invalid_numbering(self):
        p = self.repo.get(self.pid)
        self.assertTrue(p["prompt_board"])
        self.assertNotIn("drawing_prompts", p)
        self.assertEqual(p["pages"]["P002"]["panels"][0]["aspect_ratio"], "16:9")
        self.assertEqual(prompt_board.create(self.app, self.new)["project"], self.pid)
        with self.assertRaisesRegex(ValueError, "跳号"):
            prompt_board.create(self.app, {**self.new, "storyboard": "P03 K01 | 1:1 | 跳过两页"})

    def test_title_only_calls_model_and_confirmation_uses_its_pages(self):
        self.app.jobs = Mock(root=self.repo.root.parent / "jobs")
        jid = "b" * 32
        self.app.jobs.submit.return_value = jid
        self.app.prepare_language = Mock()
        with patch.object(prompt_board.creative, "structured", return_value=({"pages": [
            {"panels": [{"description": "候车的人收起雨伞。", "aspect_ratio": "2:3"}]},
            {"panels": [{"description": "列车驶进站台。", "aspect_ratio": "16:9"}]}]}, {})) as model:
            self.assertEqual(prompt_board.generate(self.app, {"title": "雨夜"}), {"job": jid})
            label, worker = self.app.jobs.submit.call_args.args
            draft = worker(Mock(), jid)
            self.app.prepare_language.assert_called_once()
            self.assertEqual(model.call_args.args[1], {"title": "雨夜", "idea": ""})
        self.assertFalse(self.repo.folder("panels-" + jid).exists())
        self.app.jobs.get.return_value = {"label": label, "status": "done", "result": draft}
        saved = prompt_board.confirm(self.app, jid)
        self.assertEqual(prompt_board.confirm(self.app, jid), saved)
        work = self.repo.get(saved["project"])
        self.assertEqual(work["pages"]["P002"]["panels"][0]["aspect_ratio"], "16:9")
        self.assertEqual(work["board_source"]["job"], jid)
        self.app.jobs.get.return_value["status"] = "failed"
        with self.assertRaisesRegex(ValueError, "生成完成"):
            prompt_board.confirm(self.app, jid)

    def test_queue_freezes_panel_and_ratio_separately_for_each_submission(self):
        with patch.object(comfyui, "discover", return_value={"checkpoints": ["test.safetensors"]}):
            for key in ("P001-K01", "P002-K01"):
                prompt_board.submit(self.app, self.pid, {"target": key, "checkpoint": "test.safetensors",
                                  "text": key + " literal", "aspect_ratio": "storyboard"}, True)
        for call, key, ratio in zip(self.app.night_queue.add.call_args_list, ["P001-K01", "P002-K01"], ["1:1", "16:9"]):
            sample = prompt_board.sample_input(self.repo, self.pid, call.args[2])
            self.assertEqual((sample["board_target"], sample["prompt_input"]["aspect_ratio"]), (key, ratio))
            self.assertEqual(sample["prompt_input"]["text"], key + " literal")
        with self.assertRaisesRegex(ValueError, "不属于"):
            prompt_board.submit(self.app, self.pid, {"target": "P003-K01"}, True)

    def test_missing_and_stale_selection_block_compose(self):
        board = self.repo.get(self.pid)
        image_dir = self.repo.folder(self.pid) / "images"
        (image_dir / "sample").mkdir(parents=True)
        Image.new("RGB", (16, 16), "blue").save(image_dir / "sample/1.png")
        batches = []
        for page in board["pages"].values():
            panel = page["panels"][0]
            batches.append({"id": page["id"], "panel_id": panel["id"], "panel_hash": e.digest(panel),
                            "images": [{"id": page["id"], "file": "sample/1.png"}]})
        e.save(image_dir / "index.json", {"batches": batches, "selections": {}})
        for batch in batches:
            comfyui.select_image(self.repo, self.pid, batch["panel_id"], batch["id"])
        first = prompt_board.page_state(self.repo, self.pid)[0]
        request = {**first, "target": first["id"]}
        result = prompt_board.compose(self.repo, self.pid, request)
        self.assertTrue((self.repo.folder(self.pid) / "layouts" / result["bundle"] / "page.png").is_file())
        with self.assertRaisesRegex(ValueError, "已变化"):
            prompt_board.unselect(self.repo, self.pid, "P001-K01", "stale-id")
        prompt_board.unselect(self.repo, self.pid, "P001-K01", "P001")
        state = prompt_board.page_state(self.repo, self.pid)[0]
        self.assertEqual(state["missing"], ["P001-K01"])
        self.assertIsNone(state["bundle"])
        with self.assertRaisesRegex(ValueError, "P001-K01"):
            prompt_board.compose(self.repo, self.pid, request)
