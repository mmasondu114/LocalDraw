import copy
import tempfile
import unittest

from comic_workflow import art_presets as art, drawing_settings, engine as e
from comic_workflow.repository import Repository
from comic_workflow.batch_drawing import available_views
from comic_workflow.reviewed_drawing import resumable_batches


class ArtPresetTests(unittest.TestCase):
    def settings(self):
        return {
            "mode": "manual",
            "checkpoint": art.CHECKPOINT,
            "items": [
                {
                    "file": art.LORA,
                    "name": "方舟样式",
                    "selected": True,
                    "confirmed": True,
                    "weight": 0.6,
                    "scope": "characters",
                    "characters": ["林晴"],
                    "trigger_words": "old",
                },
                {
                    "file": "another.safetensors",
                    "name": "另一套",
                    "selected": True,
                    "confirmed": True,
                    "weight": 0.8,
                },
            ],
        }

    def test_three_recipes_and_original_not_mutated(self):
        original = self.settings()
        before = copy.deepcopy(original)
        for key, mode, ref in [
            ("original", "none", False),
            ("lora", "manual", False),
            ("reference", "manual", True),
        ]:
            chosen = art.apply(original, key)
            drawing_settings.validate(chosen)
            self.assertEqual(chosen["mode"], mode)
            self.assertEqual(art.get(chosen)["reference"], ref)
            selected = [x for x in chosen["items"] if x["selected"]]
            self.assertEqual(len(selected), int(mode == "manual"))
            if selected:
                self.assertEqual(
                    (
                        selected[0]["weight"],
                        selected[0]["scope"],
                        selected[0]["trigger_words"],
                    ),
                    (0.7, "story", ""),
                )
        self.assertEqual(original, before)

    def test_invalid_or_modified_preset_cannot_claim_original_recipe(self):
        with self.assertRaises(ValueError):
            art.apply(self.settings(), "unknown")
        chosen = art.apply(self.settings(), "lora")
        chosen["items"][0]["weight"] = 1.3
        with self.assertRaisesRegex(ValueError, "自定义"):
            drawing_settings.validate(chosen)
        chosen["art_preset"] = "custom"
        drawing_settings.validate(chosen)

    def test_switch_retains_history_and_separates_retry_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository(tmp)
            pid = repo.create(None, True)
            p = repo.get(pid)
            panel = p["pages"]["P001"]["panels"][0]
            old = art.apply(self.settings(), "reference")
            new = art.apply(self.settings(), "lora")
            drawing_settings.save(repo, pid, old)
            drawing_settings.save(repo, pid, new)
            self.assertEqual(repo.get(pid), p)
            self.assertEqual(
                len(
                    list((repo.folder(pid) / "drawing-settings-history").glob("*.json"))
                ),
                1,
            )
            from comic_workflow.character_binding import identity_hash

            batch = {
                "panel_id": panel["id"],
                "panel_hash": e.digest(panel),
                "identity_hash": identity_hash(p, panel),
                "checkpoint": art.CHECKPOINT,
                "loras": [],
                "review_required": True,
                "art_preset_hash": art.signature(old),
                "images": [
                    {"view_index": i, "review": {"verdict": "pass"}}
                    for i in range(1, 7)
                ],
            }
            index = {"batches": [batch], "selections": {}}
            e.save(repo.folder(pid) / "images/index.json", index)
            self.assertEqual(
                available_views(index, panel, art.CHECKPOINT, [], art.signature(new)),
                set(),
            )
            self.assertEqual(
                resumable_batches(repo, pid, p, panel, art.CHECKPOINT, []), []
            )
            self.assertEqual(
                len(
                    available_views(
                        index, panel, art.CHECKPOINT, [], art.signature(old)
                    )
                ),
                6,
            )
            self.assertNotEqual(art.signature(old), art.signature(new))


if __name__ == "__main__":
    unittest.main()
