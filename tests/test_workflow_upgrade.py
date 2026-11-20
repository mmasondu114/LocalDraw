import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PIL import Image
from comic_workflow import engine as e, comfyui, layout
from comic_workflow.repository import Repository
from comic_workflow.character_binding import identity_hash
from comic_workflow.phased_drawing import draw_project
from comic_workflow.storyboard_tools import (
    candidate_units,
    decide,
    generate_all,
    accept_bundle,
)


class WorkflowUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Repository(Path(self.tmp.name) / "projects")
        self.pid = self.repo.create(None, True)
        self.p = self.repo.get(self.pid)
        self.panels = [p for page in self.p["pages"].values() for p in page["panels"]]
        for panel in self.panels:
            self.p.setdefault("drawing_prompts", {})[panel["id"]] = {
                "panel_hash": e.digest(panel),
                "identity_hash": identity_hash(self.p, panel),
                "payload": {"framing": {"test": True}, "visual_plan": {"version": 1}},
            }
        e.save(self.repo.folder(self.pid) / "project.json", self.p)
        self.events = []
        self.app = SimpleNamespace(
            repo=self.repo,
            model=Mock(),
            comfy=Mock(),
            prepare_language=Mock(
                side_effect=lambda **kw: self.events.append("load-review")
            ),
        )
        self.request = {"base_hash": e.digest(self.p), "checkpoint": "test.safetensors"}
        self.failed = False

    def render(
        self,
        repo,
        pid,
        target,
        update,
        checkpoint,
        count,
        mode,
        loras,
        indices,
        repairs,
        review_required,
        render_options=None,
    ):
        self.events.append(("draw", target, indices[0]))
        index = comfyui.drawing_state(repo, pid)
        bid = "batch" + str(len(index["batches"]))
        image = {
            "id": bid + "-1",
            "file": bid + "/1.png",
            "view_index": indices[0] + 1,
            "positive": "test",
            "review": {"verdict": "pending"},
        }
        path = repo.folder(pid) / "images" / image["file"]
        path.parent.mkdir(parents=True)
        path.write_bytes(b"fixture")
        batch = {"id": bid, "panel_id": target, "status": "done", "images": [image]}
        index["batches"].append(batch)
        e.save(repo.folder(pid) / "images/index.json", index)
        return batch

    def test_advanced_count_steps_and_seed_reach_each_generated_slot(self):
        self.request.update(candidate_count=2, steps=12, seed=42)
        received = []
        def draw(*args, **kwargs):
            received.append(kwargs["render_options"])
            return self.render(*args, **kwargs)
        result = self.run_phases(draw=draw)
        self.assertEqual(result["candidates_per_panel"], 2)
        self.assertEqual(len(received), len(self.panels) * 2)
        self.assertEqual(received[:2], [{"steps": 12, "seed": 42}, {"steps": 12, "seed": 43}])

    def test_invalid_advanced_parameters_are_rejected_before_drawing(self):
        for values in [{"candidate_count": 17}, {"steps": 0}, {"seed": -1}]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                draw_project(self.app, self.pid, {**self.request, **values}, lambda _: None, "invalid")

    def review(self, *args, **kwargs):
        self.events.append("review")
        return {"verdict": "pass", "reason": "mock check", "repair": ""}

    def run_phases(self, draw=None, review=None):
        with (
            patch(
                "comic_workflow.phased_drawing.comfyui.run_batch",
                side_effect=draw or self.render,
            ),
            patch(
                "comic_workflow.phased_drawing.creative.review_image",
                side_effect=review or self.review,
            ),
            patch(
                "comic_workflow.comfy_recovery.recover_batch",
                side_effect=lambda repo, pid, batch, cancel_running=False: batch,
            ),
        ):
            return draw_project(
                self.app, self.pid, self.request, lambda event: None, "test"
            )

    def test_all_16_slots_render_before_first_review(self):
        result = self.run_phases()
        count = len(self.panels) * 16
        self.assertEqual(sum(isinstance(x, tuple) for x in self.events), count)
        first = self.events.index("review")
        self.assertEqual(sum(isinstance(x, tuple) for x in self.events[:first]), count)
        self.app.comfy.start.assert_called_once()
        self.app.prepare_language.assert_called_once()
        self.assertFalse(result["unresolved"])
        self.assertEqual(self.repo.get(self.pid), self.p)

    def test_failed_image_is_retried_after_other_panels_without_duplicate_success(self):
        def draw(*args, **kwargs):
            if not self.failed:
                self.failed = True
                self.events.append(("failure", args[2], args[8][0]))
                raise RuntimeError("one image failed")
            return self.render(*args, **kwargs)

        result = self.run_phases(draw=draw)
        self.assertFalse(result["unresolved"])
        draws = [x for x in self.events if isinstance(x, tuple) and x[0] == "draw"]
        self.assertEqual(len(draws), len(self.panels) * 16)
        self.assertEqual(draws[-1][1:], (self.panels[0]["id"], 0))
        self.assertEqual(len(set(draws)), len(draws))
        self.assertEqual(len(result["errors"]), 1)

    def test_review_error_retries_saved_image_without_new_render(self):
        def review(*args, **kwargs):
            if not self.failed:
                self.failed = True
                raise RuntimeError("vision response invalid")
            return self.review(*args, **kwargs)

        result = self.run_phases(review=review)
        self.assertFalse(result["unresolved"])
        self.assertEqual(
            sum(isinstance(x, tuple) for x in self.events), len(self.panels) * 16
        )
        self.assertEqual(len(result["errors"]), 1)

    def test_rejected_images_never_become_pass_and_stop_at_limit(self):
        self.request["targets"] = [self.panels[0]["id"]]
        result = self.run_phases(
            review=lambda *a, **k: {
                "verdict": "reject",
                "reason": "wrong character",
                "repair": "fix mask",
            }
        )
        self.assertEqual(len([x for x in self.events if isinstance(x, tuple)]), 48)
        self.assertEqual(result["unresolved"][0]["views"], list(range(1, 17)))
        self.assertEqual(
            {
                i["review"]["verdict"]
                for b in comfyui.drawing_state(self.repo, self.pid)["batches"]
                for i in b["images"]
            },
            {"reject"},
        )

    def test_ambiguous_submission_not_duplicated_but_other_slots_continue(self):
        def draw(*args, **kwargs):
            if not self.failed:
                self.failed = True
                index = comfyui.drawing_state(self.repo, self.pid)
                index["batches"].append(
                    {
                        "id": "unknown",
                        "panel_id": args[2],
                        "status": "uncertain",
                        "images": [],
                    }
                )
                e.save(self.repo.folder(self.pid) / "images/index.json", index)
                raise TimeoutError("no history yet")
            return self.render(*args, **kwargs)

        self.request["targets"] = [self.panels[0]["id"]]
        result = self.run_phases(draw=draw)
        self.assertEqual(len([x for x in self.events if isinstance(x, tuple)]), 15)
        self.assertEqual(result["unresolved"][0]["views"], [1])

    def test_changes_to_source_stop_before_next_submission(self):
        def draw(*args, **kwargs):
            result = self.render(*args, **kwargs)
            p = self.repo.get(self.pid)
            p["pages"]["P001"]["panels"][0]["purpose"] += " changed"
            e.save(self.repo.folder(self.pid) / "project.json", p)
            return result

        with self.assertRaisesRegex(ValueError, "分镜已变化"):
            self.run_phases(draw=draw)
        self.assertEqual(len([x for x in self.events if isinstance(x, tuple)]), 1)

    def test_apply_one_change_cancel_another_preserves_rest(self):
        payload = copy.deepcopy(self.p["pages"]["P001"])
        payload["panels"][0]["drawing_description"] += " new detail"
        payload["panels"][1]["drawing_description"] += " another detail"
        c = self.repo.propose(self.pid, "page", "P001", payload, e.digest(self.p))
        _, units = candidate_units(self.repo, self.pid, c["id"])
        self.assertEqual(len(units), 2)
        decide(self.repo, self.pid, c["id"], units[0]["id"], "apply")
        decide(self.repo, self.pid, c["id"], units[1]["id"], "cancel")
        current = self.repo.get(self.pid)
        self.assertEqual(current["pages"]["P001"]["panels"][0], payload["panels"][0])
        self.assertEqual(
            current["pages"]["P001"]["panels"][1:],
            self.p["pages"]["P001"]["panels"][1:],
        )
        self.assertEqual(current["pages"]["P002"], self.p["pages"]["P002"])

    def test_conflicting_unit_cannot_overwrite_newer_edit(self):
        payload = copy.deepcopy(self.p["pages"]["P001"])
        payload["panels"][0]["drawing_description"] += " proposal"
        c = self.repo.propose(self.pid, "page", "P001", payload, e.digest(self.p))
        newer = copy.deepcopy(self.p)
        newer["pages"]["P001"]["panels"][0]["drawing_description"] += " newer"
        self.repo.commit(self.pid, newer, e.digest(self.p), {"test": True})
        with self.assertRaisesRegex(ValueError, "不能覆盖"):
            decide(self.repo, self.pid, c["id"], "P001-K01", "apply")

    def test_whole_book_remains_draft_until_confirmation(self):
        base = copy.deepcopy(self.p)
        base["pages"] = {}
        base["drawing_prompts"] = {}
        e.save(self.repo.folder(self.pid) / "project.json", base)
        calls = []

        def generate(p, stage, target, *a):
            if stage == "audit":
                return {"verdict": "pass", "issues": []}, {}
            calls.append(target)
            if target == "P002":
                self.assertIn("P001", p["pages"])
            return copy.deepcopy(self.p["pages"][target]), {}

        with patch("comic_workflow.storyboard_tools.generate", side_effect=generate):
            generate_all(
                self.app,
                self.pid,
                {"base_hash": e.digest(base)},
                lambda _: None,
                "book",
            )
        self.assertEqual(calls, ["P001", "P002"])
        self.assertEqual(self.repo.get(self.pid), base)
        accept_bundle(self.repo, self.pid, "book")
        self.assertEqual(self.repo.get(self.pid)["pages"], self.p["pages"])

    def test_partial_book_survives_generation_error(self):
        base = copy.deepcopy(self.p)
        base["pages"] = {}
        base["drawing_prompts"] = {}
        e.save(self.repo.folder(self.pid) / "project.json", base)
        with (
            patch(
                "comic_workflow.storyboard_tools.generate",
                side_effect=[
                    (self.p["pages"]["P001"], {}),
                    RuntimeError("bad response"),
                ],
            ),
            self.assertRaises(RuntimeError),
        ):
            generate_all(
                self.app,
                self.pid,
                {"base_hash": e.digest(base)},
                lambda _: None,
                "partial",
            )
        draft = e.read(
            self.repo.folder(self.pid) / "storyboard-drafts/partial/bundle.json"
        )
        self.assertEqual(list(draft["pages"]), ["P001"])
        self.assertEqual(self.repo.get(self.pid), base)

    def test_cover_removes_padding_and_dialogue_has_ellipse(self):
        page = self.p["pages"]["P001"]
        index = {"batches": [], "selections": {}}
        for panel in page["panels"]:
            name = panel["id"] + "/1.png"
            path = self.repo.folder(self.pid) / "images" / name
            path.parent.mkdir(parents=True)
            Image.new("RGB", (100, 400), "#dd4444").save(path)
            index["selections"][panel["id"]] = {
                "panel_hash": e.digest(panel),
                "image": {"file": name},
            }
        e.save(self.repo.folder(self.pid) / "images/index.json", index)
        design = layout.default_layout(page)
        result = layout.compose(self.repo, self.pid, page["id"], design)
        folder = self.repo.folder(self.pid) / "layouts" / result["bundle"]
        with Image.open(folder / "page.png") as im:
            for rect in design["panels"].values():
                self.assertEqual(
                    im.getpixel((rect["x"] + 8, rect["y"] + rect["h"] // 2)),
                    (221, 68, 68),
                )
        self.assertIn(
            "<ellipse", (folder / "lettering.svg").read_text(encoding="utf-8")
        )
        self.assertEqual(design["panels"]["P001-K01"]["x"], 24)
