import copy
import tempfile
import unittest
from comic_workflow.character_binding import (
    panel_characters,
    identity_hash,
    with_character_anchors,
)
from comic_workflow.repository import Repository
from comic_workflow import engine as e


class CharacterBindingTests(unittest.TestCase):
    def test_frozen_profile_is_bound_to_visible_character_and_changes_invalidate_hash(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository(tmp)
            p = repo.get(repo.create(None, True))
            panel = p["pages"]["P001"]["panels"][0]
            cid = panel["characters"][0]["character_id"]
            character = next(c for c in p["canon"]["characters"] if c["id"] == cid)
            p["brief"]["character_refs"] = [
                {
                    "id": "saved-id",
                    "name": character["name"],
                    "revision": 1,
                    "profile": {"clothing": "指定兜帽服装", "background": "指定作品"},
                    "image_id": "reference-id",
                }
            ]
            before = copy.deepcopy(p)
            bound = panel_characters(p, panel)
            item = next(c for c in bound if c["id"] == cid)
            self.assertEqual(
                item["saved_character"]["profile"]["clothing"], "指定兜帽服装"
            )
            self.assertEqual(
                {c["id"] for c in bound},
                {c["character_id"] for c in panel["characters"]},
            )
            digest = identity_hash(p, panel)
            item["saved_character"]["profile"]["clothing"] = "must not mutate project"
            self.assertEqual(p, before)
            p["brief"]["character_refs"][0]["profile"]["clothing"] = "new clothes"
            self.assertNotEqual(identity_hash(p, panel), digest)

    def test_actual_prompt_carries_omitted_anchors_once(self):
        anchors = [
            {
                "character_id": "C001",
                "tags": "doctor (arknights), black hood, concealed face",
            }
        ]
        result = with_character_anchors("rear view, seated at desk", anchors)
        self.assertIn(anchors[0]["tags"], result)
        self.assertIn("rear view, seated at desk", result)
        self.assertEqual(with_character_anchors(result, anchors), result)
