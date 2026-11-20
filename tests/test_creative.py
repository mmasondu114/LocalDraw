import base64
import copy
import io
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from comic_workflow import engine as e, layout, creative, comfyui
from comic_workflow.characters import CharacterLibrary, DETAILS
from comic_workflow.repository import Repository
from comic_workflow.lora_inspection import inspect_lora
from comic_workflow.reviewed_drawing import reviewed_batch


class CreativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.lib = CharacterLibrary(self.root / "characters")
        self.profile = {
            k: "测试用详细角色说明，保留用户确定的外貌和性格。" for k in DETAILS
        }
        self.profile.update(observed=[], suggested=[], unknowns=[])
        self.source = {"mode": "text", "text": "用户资料"}
        self.repo = Repository(self.root / "projects")
        self.pid = self.repo.create(None, True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_chinese_name_resolution_and_no_prefix_collision(self):
        self.lib.save("林晴", self.profile, self.source)
        self.lib.save("林晴雨", self.profile, self.source)
        self.assertEqual(
            [x["name"] for x in self.lib.resolve("让 /林晴 和 /林晴雨 交谈。")],
            ["林晴", "林晴雨"],
        )
        self.assertEqual(self.lib.resolve("https://example.com/test"), [])
        self.assertEqual(self.lib.resolve("让/林晴走进书店")[0]["name"], "林晴")
        with self.assertRaises(ValueError):
            self.lib.resolve("/不存在 出场")
        with self.assertRaises(ValueError):
            self.lib.save("../逃逸", self.profile, self.source)

    def test_card_revision_frozen_and_history_survives(self):
        old = self.lib.save("林晴", self.profile, self.source)
        brief = copy.deepcopy(self.repo.get(self.pid)["brief"])
        brief["character_material"] = "/林晴"
        frozen = self.lib.freeze_brief(brief)
        profile = copy.deepcopy(self.profile)
        profile["hair"] = "银色长发，新设定测试"
        self.lib.save("林晴", profile, self.source, base_revision=1)
        self.assertEqual(self.lib.freeze_brief(frozen)["character_refs"][0], old)
        self.assertEqual(len(CharacterLibrary(self.lib.root).history("林晴")), 2)
        with self.assertRaises(ValueError):
            self.lib.save("林晴", profile, self.source, base_revision=1)

    def test_middle_dot_names_can_be_saved_and_mentioned(self):
        short = self.lib.save("凯尔希", self.profile, self.source)
        long = self.lib.save("凯尔希·思衡托", self.profile, self.source)
        self.assertEqual(self.lib.resolve("/凯尔希·思衡托 与 /凯尔希 一起行动。"), [long, short])
        self.assertEqual(self.lib.get("凯尔希·思衡托"), long)
        with self.assertRaises(ValueError):
            self.lib.save("凯尔希/思衡托", self.profile, self.source)

    def test_search_metadata_does_not_change_saved_or_frozen_cards(self):
        saved = self.lib.save("林晴", self.profile, self.source)
        searchable = self.lib.searchable_listing()[0]
        self.assertIn("lin qing", searchable["search_terms"])
        self.assertEqual(self.lib.get("林晴"), saved)
        self.assertEqual(self.lib.listing(), [saved])
        self.assertEqual(self.lib.resolve("/林晴"), [saved])

    def test_image_upload_normalizes_and_rejects_invalid(self):
        buf = io.BytesIO()
        Image.new("RGB", (1800, 1200), "red").save(buf, format="PNG")
        r = self.lib.upload(
            "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        )
        self.assertLessEqual(r["width"], 1024)
        with Image.open(self.lib.image_path(r["image_id"])) as img:
            self.assertEqual(img.format, "PNG")
        with self.assertRaises(ValueError):
            self.lib.upload("data:image/svg+xml;base64,AAAA")
        with self.assertRaises(Exception):
            self.lib.upload("data:image/png;base64,AAAA")

    def test_variable_counts_and_mixed_layouts_rtl(self):
        p = self.repo.get(self.pid)
        p["brief"]["panel_mode"] = "flexible"
        p["plan"]["pages"][0]["panel_count"] = 4
        e.validate_plan(p["plan"], p)
        p["brief"]["panel_mode"] = "fixed"
        with self.assertRaises(ValueError):
            e.validate_plan(p["plan"], p)
        page = copy.deepcopy(p["pages"]["P001"])
        ids = [x["id"] for x in page["panels"]]
        page["layout_rows"] = [
            {"weight": 2, "panels": [{"panel_id": ids[0], "weight": 1}]},
            {
                "weight": 1,
                "panels": [
                    {"panel_id": k, "weight": i + 1} for i, k in enumerate(ids[1:])
                ],
            },
        ]
        left = layout.default_layout(page)
        self.assertGreater(left["panels"][ids[0]]["h"], left["panels"][ids[1]]["h"])
        page["reading_direction"] = "RTL"
        right = layout.default_layout(page)
        self.assertGreater(right["panels"][ids[1]]["x"], right["panels"][ids[2]]["x"])
        for preset in ["balanced", "hero", "cinema", "vertical"]:
            layout.validate_layout(layout.default_layout(page, preset), page)
        page["layout_rows"][1]["panels"][0]["panel_id"] = ids[0]
        with self.assertRaises(ValueError):
            layout.default_layout(page)

    def test_direct_story_does_not_require_world_options_confirmation(self):
        p = self.repo.get(self.pid)
        self.lib.save("林晴", self.profile, self.source)
        p["canon"]["characters"][0]["name"] = "/林晴"
        b = p["brief"]
        b["panel_mode"] = "flexible"
        b["character_material"] = "/林晴"
        b["character_refs"] = self.lib.listing()
        outline = p["outline"]
        outline["selected_option"] = "direct"
        result = {
            "payload": {
                "story": "林晴拿起纸船，原剧本。",
                "brief": b,
                "canon": p["canon"],
                "outline": outline,
                "assumptions": [],
                "changes": [],
            }
        }
        new = self.repo.get(creative.create_story(self.repo, self.lib, result))
        self.assertEqual(new["workflow"], "direct")
        self.assertIsNone(new["options"])
        self.assertEqual(new["canon"]["characters"][0]["name"], "林晴")
        e.validate_project(new)

    def test_lora_headers_detect_sdxl_and_sd1(self):
        for dimension, expected in [(768, False), (2048, True)]:
            h = json.dumps(
                {
                    "lora_unet_attn2_to_k.lora_down.weight": {
                        "shape": [1, dimension],
                        "dtype": "F16",
                        "data_offsets": [0, 2],
                    }
                }
            ).encode()
            path = self.root / f"{dimension}.safetensors"
            path.write_bytes(struct.pack("<Q", len(h)) + h + b"00")
            self.assertIs(inspect_lora(path)["structurally_compatible"], expected)

    def test_simplified_storyboard_attaches_audit_without_silent_page_commit(self):
        import time
        from comic_workflow.server import Application

        p = self.repo.get(self.pid)
        audit = {
            "verdict": "pass",
            "issues": [],
            "unknown_canon": [],
            "reviewed_scope": "P001",
        }
        with (
            patch("comic_workflow.jobs.DATA", self.root),
            patch.object(Application, "prepare_language"),
            patch(
                "comic_workflow.server.generate",
                side_effect=[(p["pages"]["P001"], {}), (audit, {})],
            ),
        ):
            app = Application(self.repo)
            jid = app.action(
                {
                    "action": "storyboard",
                    "project": self.pid,
                    "target": "P001",
                    "base_hash": e.digest(p),
                }
            )["job"]
            for _ in range(100):
                job = app.jobs.get(jid)
                if job["status"] != "running":
                    break
                time.sleep(0.01)
            self.assertEqual(job["status"], "done", job)
            self.assertEqual(e.digest(self.repo.get(self.pid)), e.digest(p))
            c = next(
                c
                for c in self.repo.candidates(self.pid)
                if c["id"] == job["result"]["candidate"]
            )
            self.assertEqual(c["metadata"]["continuity_review"], audit)

    def prepare_prompt(self):
        p = self.repo.get(self.pid)
        panel = p["pages"]["P001"]["panels"][0]
        payload = {
            "panel_id": panel["id"],
            "positive": "base",
            "negative": "text",
            "composition_variants": ["angle " + str(i) for i in range(6)],
            "character_anchors": [
                {"character_id": x["character_id"], "tags": "woman"}
                for x in panel["characters"]
            ],
            "adaptation_notes": "test",
        }
        c = self.repo.propose(
            self.pid, "drawing_prompt", panel["id"], payload, e.digest(p)
        )
        self.repo.accept(self.pid, c["id"])
        return panel

    def test_rejected_image_cannot_be_selected(self):
        panel = self.prepare_prompt()
        index = {
            "selections": {},
            "batches": [
                {
                    "id": "test",
                    "panel_id": panel["id"],
                    "panel_hash": e.digest(panel),
                    "images": [{"id": "test-1", "review": {"verdict": "reject"}}],
                }
            ],
        }
        e.save(self.repo.folder(self.pid) / "images/index.json", index)
        with self.assertRaises(ValueError):
            comfyui.select_image(self.repo, self.pid, panel["id"], "test-1")

    def make_resume_history(self, panel, verdicts_by_round):
        from comic_workflow.character_binding import identity_hash

        p = self.repo.get(self.pid)
        batches = []
        for number, verdicts in enumerate(verdicts_by_round):
            batch = {
                "id": f"saved-{number}",
                "panel_id": panel["id"],
                "panel_hash": e.digest(panel),
                "identity_hash": identity_hash(p, panel),
                "checkpoint": "resume-test",
                "loras": [],
                "review_required": True,
                "images": [],
            }
            for view, verdict in enumerate(verdicts, 1):
                file = f"saved-{number}/{view}.png"
                path = self.repo.folder(self.pid) / "images" / file
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"mock image")
                batch["images"].append(
                    {
                        "id": f"saved-{number}-{view}",
                        "file": file,
                        "view_index": view,
                        "positive": "p",
                        "review": {"verdict": verdict, "repair": "fix"},
                    }
                )
            batches.append(batch)
        e.save(
            self.repo.folder(self.pid) / "images/index.json",
            {"selections": {}, "batches": batches},
        )

    def test_resume_does_not_reset_exhausted_replacement_budget(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        panel = self.prepare_prompt()
        self.make_resume_history(panel, [["reject"] * 6] * 3)
        app = SimpleNamespace(
            repo=self.repo, model=Mock(), comfy=Mock(), prepare_language=Mock()
        )
        with (
            patch("comic_workflow.reviewed_drawing.comfyui.run_batch") as draw,
            patch("comic_workflow.reviewed_drawing.creative.review_image") as review,
        ):
            result = reviewed_batch(
                app,
                self.pid,
                {"target": panel["id"], "checkpoint": "resume-test", "resume": True},
                lambda x: None,
                "resume-limit",
            )
        draw.assert_not_called()
        review.assert_not_called()
        app.prepare_language.assert_not_called()
        self.assertEqual(result["unresolved_views"], list(range(1, 7)))
        self.assertTrue(result["reused_complete"])
        self.assertEqual(result["attempts_by_view"], {str(i): 3 for i in range(1, 7)})

    def test_resume_reviews_saved_images_without_regenerating_or_rechecking_passes(
        self,
    ):
        from types import SimpleNamespace
        from unittest.mock import Mock

        panel = self.prepare_prompt()
        self.make_resume_history(
            panel, [["pass", "pending", "pending", "pending", "pending", "pending"]]
        )
        app = SimpleNamespace(
            repo=self.repo, model=Mock(), comfy=Mock(), prepare_language=Mock()
        )
        with (
            patch("comic_workflow.reviewed_drawing.comfyui.run_batch") as draw,
            patch(
                "comic_workflow.reviewed_drawing.creative.review_image",
                return_value={"verdict": "uncertain", "repair": "", "checks": []},
            ) as review,
        ):
            result = reviewed_batch(
                app,
                self.pid,
                {"target": panel["id"], "checkpoint": "resume-test", "resume": True},
                lambda x: None,
                "resume-reviews",
            )
        draw.assert_not_called()
        self.assertEqual(review.call_count, 5)
        self.assertEqual(len(result["passed"]), 1)
        self.assertEqual(len(result["uncertain"]), 5)
        self.assertEqual(result["unresolved_views"], [])
        self.assertFalse(result["reused_complete"])

    def test_resume_legacy_hash_verifies_character_identity(self):
        from comic_workflow.reviewed_drawing import resumable_batches

        panel = self.prepare_prompt()
        self.make_resume_history(panel, [["reject"] * 6])
        p = self.repo.get(self.pid)
        index = comfyui.drawing_state(self.repo, self.pid)
        batch = index["batches"][0]
        batch.pop("identity_hash")
        batch["prompt_hash"] = e.digest(p["drawing_prompts"][panel["id"]])
        e.save(self.repo.folder(self.pid) / "images/index.json", index)
        self.assertEqual(
            len(resumable_batches(self.repo, self.pid, p, panel, "resume-test", [])), 1
        )
        changed = copy.deepcopy(p)
        changed["canon"]["characters"][0]["appearance"] += "new identity"
        self.assertEqual(
            resumable_batches(self.repo, self.pid, changed, panel, "resume-test", []),
            [],
        )

    def test_replacements_only_for_rejected_views_and_stop_after_limit(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        panel = self.prepare_prompt()
        app = SimpleNamespace(
            repo=self.repo, model=Mock(), comfy=Mock(), prepare_language=Mock()
        )
        app.model.status.return_value = {"ready": False}
        calls = []

        def batch(
            repo,
            pid,
            target,
            update,
            cp,
            count,
            mode,
            loras,
            indices,
            repairs,
            review_required,
        ):
            calls.append(list(indices))
            bid = "batch" + str(len(calls))
            return {
                "id": bid,
                "images": [
                    {
                        "id": bid + str(i),
                        "file": "unused.png",
                        "positive": str(i),
                        "view_index": i + 1,
                    }
                    for i in indices
                ],
            }

        def review(panel, prompt, *args):
            return {
                "verdict": "reject" if prompt == "2" else "pass",
                "repair": "fix",
                "reason": "test",
            }

        with (
            patch(
                "comic_workflow.reviewed_drawing.comfyui.run_batch", side_effect=batch
            ),
            patch(
                "comic_workflow.reviewed_drawing.creative.review_image",
                side_effect=review,
            ),
            patch("comic_workflow.reviewed_drawing.record_review"),
        ):
            r = reviewed_batch(
                app, self.pid, {"target": panel["id"]}, lambda text: None, "test"
            )
        self.assertEqual(calls, [list(range(6)), [2], [2]])
        self.assertEqual(r["unresolved_views"], [3])
        self.assertEqual(len(r["passed"]), 5)
        app.comfy.stop.assert_called_once()

    def test_uncertain_is_not_silently_passed_or_regenerated_forever(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        panel = self.prepare_prompt()
        app = SimpleNamespace(
            repo=self.repo, model=Mock(), comfy=Mock(), prepare_language=Mock()
        )
        app.model.status.return_value = {"ready": False}
        batch = {
            "id": "batch",
            "images": [
                {"id": "i", "file": "unused.png", "positive": "p", "view_index": 1}
            ],
        }
        with (
            patch(
                "comic_workflow.reviewed_drawing.comfyui.run_batch", return_value=batch
            ) as draw,
            patch(
                "comic_workflow.reviewed_drawing.creative.review_image",
                return_value={"verdict": "uncertain", "repair": "", "reason": "遮挡"},
            ),
            patch("comic_workflow.reviewed_drawing.record_review"),
        ):
            r = reviewed_batch(
                app, self.pid, {"target": panel["id"]}, lambda text: None, "uncertain"
            )
        draw.assert_called_once()
        self.assertEqual(r["passed"], [])
        self.assertEqual(r["uncertain"], ["i"])
