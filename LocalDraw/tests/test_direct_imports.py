import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from comic_workflow import direct_imports as direct, engine as e, manual_drawing, prompt_exchange
from comic_workflow.repository import Repository
from comic_workflow.server import Application


class DirectImportsTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.repo = Repository(Path(folder.name) / "projects")
        self.addCleanup(self.repo._temporary.cleanup)
        self.library = Mock(listing=lambda: [])
        self.pid = self.repo.create(None, True)
        self.book = self.repo.get(self.pid)
        self.app = Application.__new__(Application)
        self.app.repo = self.repo
        self.app.characters = self.library
        # The normal action path must work without a model, GPU, queue or image service.

    def call(self, action, **args):
        return self.app.action({"action": action, "project": self.pid, **args})

    def test_bundle_pair_preview_and_save_creates_copy(self):
        base = {**self.book, "pages": {}, "plan": None}
        bundle = {"status": "complete", "base_hash": e.digest(base), "plan": self.book["plan"],
                  "pages": {k: {"payload": v} for k, v in self.book["pages"].items()}}
        with self.assertRaisesRegex(ValueError, "匹配"):
            self.call("preview_direct_story", document=bundle, base_project=self.book)
        request = {"document": json.dumps(bundle), "base_project": json.dumps(base)}
        preview = self.call("preview_direct_story", **request)
        self.assertEqual((preview["page_count"], preview["panel_count"]), (2, 6))
        args = {**request, "preview_hash": preview["preview_hash"], "import_key": "a" * 32, "confirm": True}
        result = self.call("save_direct_story", **args)
        self.assertEqual(self.repo.get(result["project"])["pages"], self.book["pages"])
        self.assertEqual(self.repo.get(self.pid), self.book)
        self.assertTrue(self.call("save_direct_story", **args)["reused"])
        self.assertFalse((self.repo.folder(result["project"]) / "images").exists())

    def test_exchange_and_complete_project_files(self):
        export = prompt_exchange.export_storyboard(self.repo, self.pid)
        for doc in [self.book, export]:
            self.assertEqual(self.call("preview_direct_story", document=doc)["panel_count"], 6)
        export["panels"].pop()
        with self.assertRaisesRegex(ValueError, "部分画格"):
            self.call("preview_direct_story", document=export)

    def test_single_download_round_trips_into_empty_workspace_and_maps_prompts(self):
        self.book["brief"]["character_refs"] = [{"name": "unshared", "local_path": "private-path"}]
        e.save(self.repo.folder(self.pid) / "project.json", self.book)
        export = prompt_exchange.export_download(self.repo, self.pid)
        self.assertNotIn("portable_storyboard", prompt_exchange.export_storyboard(self.repo, self.pid))
        self.assertNotIn("character_refs", export["portable_storyboard"]["brief"])
        other = Repository(self.repo.root.parent / "isolated")
        self.addCleanup(other._temporary.cleanup)
        self.app.repo = other
        preview = self.call("preview_direct_story", document=export)
        result = self.call("save_direct_story", document=export, confirm=True, import_key="1"*32,
                           preview_hash=preview["preview_hash"])
        self.assertEqual(self.book["pages"], other.get(result["project"])["pages"])
        prompt_doc = {**export, "panels": [dict(export["panels"][0], positive="  direct  ", negative="")]}
        preview = self.call("preview_direct_prompts", project=result["project"], document=prompt_doc)
        self.assertEqual(preview["mapped_from"], self.pid)
        self.assertEqual(preview["panels"][0]["panel_id"], export["panels"][0]["panel_id"])
        prompt_doc["panels"][0]["storyboard"]["action"] += " changed"
        with self.assertRaisesRegex(ValueError, "摘要不匹配"):
            self.call("preview_direct_prompts", project=result["project"], document=prompt_doc)

    def save_prompts(self, rows):
        p = self.call("preview_direct_prompts", document=rows)
        return self.call("save_direct_prompts", document=rows, preview_hash=p["preview_hash"], confirm=True)

    def test_partial_prompt_import_merges_by_id_and_preserves_unrelated_manual_edit(self):
        rows = [{"panel_id": "P001-K02", "positive": " B\n", "negative": ""},
                {"panel_id": "P001-K01", "positive": "A", "negative": " blur "}]
        self.save_prompts(rows)
        current = prompt_exchange.latest_bundle(self.repo, self.pid)
        previous = dict(current["panels"][0], positive="manual B")
        manual_drawing.save_confirmed_prompts(self.repo, self.pid,
            {**current, "created": e.stamp(), "panels": [previous]})
        changes = [{**rows[1], "positive": " changed A "}]
        preview = self.call("preview_direct_prompts", document=changes)
        self.assertEqual(preview["replace"], ["P001-K01"])
        self.save_prompts(changes)
        texts = manual_drawing.saved_texts(self.repo, self.pid, self.book)
        self.assertEqual(texts["P001-K01"]["positive"], " changed A ")
        self.assertEqual(texts["P001-K02"]["positive"], "manual B")
        self.assertEqual(self.repo.get(self.pid), self.book)

    def test_drafts_invalid_ids_and_stale_previews(self):
        row = {"panel_id": "P001-K01", "positive": "original", "negative": ""}
        for rows in [[row, row], [dict(row, panel_id="P011-K02")], [dict(row, page_id="P003")],
                     [dict(row, positive="")], {"project_id": "other", "panels": [row]}]:
            with self.assertRaises(ValueError):
                self.call("preview_direct_prompts", document=rows)
        with self.assertRaisesRegex(ValueError, "字段重复"):
            self.call("preview_direct_prompts", document='{"panels":[],"panels":[]}')
        preview = self.call("preview_direct_prompts", document=[row])
        self.save_prompts([dict(row, positive="newer")])
        with self.assertRaisesRegex(ValueError, "重新预览"):
            self.call("save_direct_prompts", document=[row], confirm=True, preview_hash=preview["preview_hash"])
        draft = {"hash": e.digest(self.book["pages"]["P001"]["panels"][0]), "source": ["newer", ""],
                 "positive": "unsaved", "negative": ""}
        state = {"panelPromptDrafts": {row["panel_id"]: copy.deepcopy(draft)}}
        ws = self.repo.folder(self.pid) / "workspace.json"
        e.save(ws, state)
        with self.assertRaisesRegex(ValueError, "未提交"):
            self.call("preview_direct_prompts", document=[row])
        self.assertEqual(e.read(ws), state)

    def test_standalone_generates_each_grid_by_count_without_story(self):
        rows = [{"id": "P003-K02", "visual_prompt": "  rainy window  ", "negative_prompt": "blur",
                 "dialogue": "Listen.", "sound_effect": "Tap"},
                {"panel_id": "P004-K01", "positive": "sunlit desk"}]
        args = {"project": "", "document": rows}
        preview = self.call("preview_direct_prompts", **args)
        self.assertEqual([p["panel_id"] for p in preview["panels"]], ["P003-K02", "P004-K01"])
        args.update(confirm=True, import_key="a"*32, preview_hash=preview["preview_hash"])
        saved = self.call("save_direct_prompts", **args)
        self.assertEqual(self.call("save_direct_prompts", **args), saved)
        pid = saved["project"]
        frozen = prompt_exchange.prepare_request(self.repo, pid, {**saved, "candidate_count": 3, "checkpoint":"test"})
        app = Mock(repo=self.repo)
        with patch.object(prompt_exchange.comfyui, "run_batch", return_value={"id":"demo"}) as draw:
            report = prompt_exchange.draw_imported(app, pid, frozen, lambda event: None, "test")
        self.assertEqual(report["errors"], [])
        self.assertEqual([(c.args[2], c.kwargs["prompt_override"]["positive"], c.kwargs["prompt_override"]["negative"])
                          for c in draw.call_args_list],
                         [("P001-K01", "  rainy window  ", "blur")] * 3 + [("P002-K01", "sunlit desk", "")] * 3)
        self.assertEqual(self.repo.get(self.pid), self.book)
        with self.assertRaises(ValueError):
            self.call("preview_direct_prompts", project="", document=["valid", " "])

    def test_visual_prompt_list_matches_existing_panels_by_id(self):
        row = {"id": "P001-K02", "visual_prompt": "  rain\non glass  ", "dialogue": "Hi", "sound_effect": "Tap"}
        self.save_prompts([row])
        saved = manual_drawing.saved_texts(self.repo, self.pid, self.book)[row["id"]]
        self.assertEqual((saved["positive"], saved["negative"]), (row["visual_prompt"], ""))
        with self.assertRaisesRegex(ValueError, "重复"):
            self.call("preview_direct_prompts", document=[row, row])

    def test_import_setup_freezes_cast_loras_and_individual_counts(self):
        rows = [{"panel_id": k, "positive": f"  Original {k}  ", "negative": "blur"}
                for k in ("P001-K01", "P001-K02")]
        saved = self.save_prompts(rows)
        card = {"id": "actor", "name": "Rain", "revision": 1, "profile": {"hair": "blue hair"}}
        e.save(self.repo.root.parent / "characters/cards/actor/current.json", card)
        chosen = [{"file": "anime/style.safetensors", "weight": 0.5}]
        options = {"characters": [{"name": "Rain", "revision": 1}], "loras": chosen,
                   "counts": {"P001-K02": 3}}
        request = {**saved, "candidate_count": 1, "checkpoint": "test", "import_setup": options}
        with patch("comic_workflow.quick_sample.selected_loras", return_value=chosen) as resolve:
            frozen = prompt_exchange.prepare_request(self.repo, self.pid, request)
            resolve.assert_called_once_with(chosen, "test")
        with patch.object(prompt_exchange.comfyui, "run_batch", return_value={"id":"demo"}) as draw:
            result = prompt_exchange.draw_imported(Mock(repo=self.repo), self.pid, frozen, lambda _:None, "setup")
        self.assertEqual(result["total"], 4)
        self.assertEqual([c.args[2] for c in draw.call_args_list], ["P001-K01"] + ["P001-K02"]*3)
        for call in draw.call_args_list:
            self.assertEqual(call.args[7], chosen)
            self.assertEqual(call.kwargs["prompt_override"]["positive"], f"  Original {call.args[2]}  \nblue hair")
            self.assertEqual(call.kwargs["prompt_override"]["negative"], "blur")
        original = prompt_exchange.load_import(self.repo, self.pid, saved["bundle"])
        self.assertEqual([r["positive"] for r in original["panels"]], [r["positive"] for r in rows])
        options["counts"]["P001-K02"] = 0
        with self.assertRaisesRegex(ValueError, "张数"):
            prompt_exchange.prepare_request(self.repo, self.pid, request)

    def test_negative_edits_preserve_import_and_survive_job_reload(self):
        ids = ["P001-K01", "P001-K02"]
        saved = self.save_prompts([{"panel_id": k, "positive": "rain", "negative": "  blur  "} for k in ids])
        setup = {"common_negative": "watermark\ntext", "negative_by_panel": {ids[1]: ""}}
        request = {**saved, "candidate_count": 1, "checkpoint": "test", "import_setup": setup}
        for edited in ("", "  extra hands  "):
            setup["negative_by_panel"][ids[1]] = edited
            frozen = json.loads(json.dumps(prompt_exchange.prepare_request(self.repo, self.pid, request)))
            with patch.object(prompt_exchange.comfyui, "run_batch", return_value={"id": "demo"}) as draw:
                prompt_exchange.draw_imported(Mock(repo=self.repo), self.pid, frozen, lambda _: None, "run")
            negatives = [call.kwargs["prompt_override"]["negative"] for call in draw.call_args_list]
            self.assertEqual(negatives, ["  blur  \nwatermark\ntext", (edited + "\n" if edited else "") + "watermark\ntext"])
        original = prompt_exchange.load_import(self.repo, self.pid, saved["bundle"])
        self.assertEqual([row["negative"] for row in original["panels"]], ["  blur  "] * 2)
        for invalid in ({"common_negative": "x" * 20000}, {"negative_by_panel": {"unknown": ""}},
                        {"negative_by_panel": {ids[0]: None}}):
            with self.assertRaisesRegex(ValueError, "负面提示词"):
                prompt_exchange.prepare_request(self.repo, self.pid, {**request, "import_setup": invalid})
