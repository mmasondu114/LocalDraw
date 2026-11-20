import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from comic_workflow import engine as e, story_import
from comic_workflow.paths import ROOT
from comic_workflow.repository import Repository


def document():
    source = e.read(ROOT / "config/example.json")
    return {"brief": source["brief"], "canon": source["canon"], "pages": list(source["pages"].values())}


class StoryImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Repository(Path(self.temp.name) / "projects")
        self.addCleanup(self.repo._temporary.cleanup)
        self.library = Mock()
        self.library.listing.return_value = []
        self.doc = document()

    def request(self, doc=None, key="a" * 32):
        doc = self.doc if doc is None else doc
        return {"document": doc, "confirm": True, "import_key": key,
                "preview_hash": story_import.preview(doc, self.library)["preview_hash"]}

    def test_preview_is_read_only_and_import_preserves_full_authored_data(self):
        original = copy.deepcopy(self.doc)
        preview = story_import.preview("\ufeff" + json.dumps(self.doc), self.library)
        self.assertEqual((preview["page_count"], preview["panel_count"]), (2, 6))
        self.assertEqual(self.repo.listing(), [])
        result = story_import.save_import(self.repo, self.library, self.request())
        project = self.repo.get(result["project"])
        self.assertEqual(project["brief"], self.doc["brief"])
        self.assertEqual(project["canon"], self.doc["canon"])
        self.assertEqual(list(project["pages"].values()), self.doc["pages"])
        self.assertEqual(project["workflow"], "direct")
        e.validate_project(project, complete=True)
        self.assertEqual(self.doc, original)
        folder = self.repo.folder(result["project"])
        self.assertEqual(e.read(folder / "story-import/source.json"), original)
        self.assertEqual(e.read(folder / "workspace.json")["stage"], "page")
        for line in self.doc["pages"][0]["lettering"]:
            self.assertIn(line["text"], project["script"])
        # The imported pages can immediately use existing editing and export paths.
        edited = copy.deepcopy(project["pages"]["P001"])
        edited["panels"][0]["purpose"] += " Updated."
        self.repo.propose(result["project"], "page", "P001", edited, e.digest(project))
        self.assertIn("script.json", self.repo.export(result["project"])["files"])

    def test_existing_projects_and_workspaces_are_untouched(self):
        old_id = self.repo.create(None, True)
        folder = self.repo.folder(old_id)
        e.save(folder / "workspace.json", {"draft": "keep me"})
        e.save(folder / "images/index.json", {"selections": {"P001-K01": "old-choice"}})
        before = {str(path): path.read_bytes() for path in folder.rglob("*") if path.is_file()}
        story_import.save_import(self.repo, self.library, self.request())
        self.assertEqual(before, {str(path): path.read_bytes() for path in folder.rglob("*") if path.is_file()})

    def test_save_requires_preview_confirmation_and_unchanged_content(self):
        req = self.request()
        for changes in ({"confirm": False}, {"preview_hash": "old"}, {"import_key": "../escape"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                story_import.save_import(self.repo, self.library, {**req, **changes})
        req["document"]["pages"][0]["panels"][0]["purpose"] += " Changed"
        with self.assertRaisesRegex(ValueError, "重新预览"):
            story_import.save_import(self.repo, self.library, req)
        self.assertEqual(self.repo.listing(), [])

    def test_retry_reuses_same_project_but_new_selection_can_create_another(self):
        req = self.request()
        first = story_import.save_import(self.repo, self.library, req)
        second = story_import.save_import(self.repo, self.library, req)
        self.assertEqual(first["project"], second["project"])
        self.assertTrue(second["reused"])
        third = story_import.save_import(self.repo, self.library, self.request(key="b" * 32))
        self.assertNotEqual(first["project"], third["project"])
        changed = copy.deepcopy(self.doc)
        changed["brief"]["title"] += " Different"
        with self.assertRaisesRegex(ValueError, "另一份内容"):
            story_import.save_import(self.repo, self.library, self.request(changed))
        self.assertEqual(len(self.repo.listing()), 2)

    def test_local_character_binding_does_not_trust_imported_assets_or_edit_canon(self):
        name = self.doc["canon"]["characters"][0]["name"]
        card = {"name": name, "id": "local-card", "revision": 1, "image_id": "local-image", "profile": {"clothing": "default"}}
        self.library.listing.return_value = [card]
        self.doc["brief"]["character_refs"] = [{"name": name, "image_id": "../../private", "profile": {"clothing": "foreign"}}]
        preview = story_import.preview(self.doc, self.library)
        self.assertTrue(preview["characters"][0]["local_match"])
        saved = story_import.save_import(self.repo, self.library, self.request())
        p = self.repo.get(saved["project"])
        self.assertEqual(p["brief"]["character_refs"], [card])
        self.assertEqual(p["canon"], self.doc["canon"])
        self.assertEqual(self.library.listing.return_value, [card])
        req = self.request(key="c" * 32)
        card["revision"] = 2
        with self.assertRaisesRegex(ValueError, "角色卡已变化"):
            story_import.save_import(self.repo, self.library, req)

    def test_rejects_invalid_cross_references_ids_states_and_layouts(self):
        mutations = [
            (lambda d: d["pages"].reverse(), "页号"),
            (lambda d: d["pages"].pop(), "实际页数"),
            (lambda d: d["pages"][1].update(id="P001"), "页号"),
            (lambda d: d["pages"][0]["panels"][0].update(id="../../escape"), "P001"),
            (lambda d: d["pages"][0]["panels"][0].update(scene_id="S999"), "未知场景"),
            (lambda d: d["pages"][0]["panels"][0]["characters"][0].update(character_id="C999"), "未知角色"),
            (lambda d: d["pages"][0]["panels"][0]["props"].append({"prop_id": "O999", "position": "here", "state": "new"}), "道具"),
            (lambda d: d["pages"][0]["lettering"][0].update(speaker_id="C999"), "未知角色|说话者"),
            (lambda d: d["pages"][0]["lettering"][0].update(order=99), "气泡"),
            (lambda d: d["pages"][1]["state_in"].update({next(iter(d["pages"][1]["state_in"])): "changed"}), "P002.*前页"),
            (lambda d: d["pages"][0]["panels"][0].update(state_after={}), "P001.*状态"),
            (lambda d: d["pages"][0].update(state_out={}), "页末状态"),
            (lambda d: d["pages"][0].update(layout_rows=[{"weight": 1, "panels": [{"panel_id": "P001-K01", "weight": 1}]}]), "排版"),
        ]
        for mutate, error in mutations:
            with self.subTest(error=error):
                doc = copy.deepcopy(self.doc)
                mutate(doc)
                with self.assertRaisesRegex(ValueError, error):
                    story_import.preview(doc, self.library)
        self.assertEqual(self.repo.listing(), [])

    def test_schema_errors_include_field_location(self):
        del self.doc["pages"][0]["panels"][0]["drawing_description"]
        with self.assertRaisesRegex(ValueError, r"pages\[0\].panels\[0\].*drawing_description"):
            story_import.preview(self.doc, self.library)

    def test_rejects_partial_files_unknown_fields_duplicate_keys_and_oversize(self):
        for value in ([], {}, self.doc["pages"][0], {**self.doc, "images": []},
                      {**self.doc, "format": "future"}, {**self.doc, "pages": {}},
                      '{"brief": {}, "brief": {}}', '{"brief": NaN}', '{oops'):
            with self.subTest(value=str(value)[:40]), self.assertRaises(ValueError):
                story_import.preview(value, self.library)
        with patch.object(story_import, "MAX_BYTES", 20), self.assertRaisesRegex(ValueError, "5 MB"):
            story_import.preview(self.doc, self.library)

    def test_publish_is_atomic_on_write_failure(self):
        save = e.save
        def fail_project(path, value):
            self.assertEqual(self.repo.listing(), [])
            if Path(path).name == "project.json":
                raise OSError("disk full")
            save(path, value)
        with patch.object(story_import.e, "save", side_effect=fail_project), self.assertRaisesRegex(OSError, "disk full"):
            story_import.save_import(self.repo, self.library, self.request())
        self.assertEqual(self.repo.listing(), [])
        self.assertFalse((self.repo.root / ("story-" + "a" * 32)).exists())
        self.assertEqual(list((self.repo.root / ".story-imports").iterdir()), [])

    def test_optional_format_and_rtl_survive(self):
        self.doc["format"] = story_import.FORMAT
        self.doc["brief"]["reading_direction"] = "RTL"
        self.doc["brief"]["page_mode"] = "print_spreads"
        for page in self.doc["pages"]:
            page["reading_direction"] = "RTL"
        saved = story_import.save_import(self.repo, self.library, self.request())
        p = self.repo.get(saved["project"])
        self.assertEqual(list(p["pages"].values()), self.doc["pages"])
        self.assertEqual([page["turn_after"] for page in p["plan"]["pages"]], [False, True])

    def test_english_lettering_uses_word_limits_and_chinese_keeps_character_limits(self):
        line = self.doc["pages"][0]["lettering"][0]
        line["text"] = "Extraordinarily " * 25
        warnings = story_import.preview(self.doc, self.library)["warnings"]
        self.assertFalse(any(line["id"] in item and "对白超过" in item for item in warnings))
        line["text"] = "Word " * 56
        warnings = story_import.preview(self.doc, self.library)["warnings"]
        self.assertTrue(any(line["id"] in item and "25个英文单词" in item for item in warnings))
        self.assertTrue(any(line["panel_id"] in item and "55个英文单词" in item for item in warnings))
        line["text"] = "字" * 61
        warnings = story_import.preview(self.doc, self.library)["warnings"]
        self.assertTrue(any(line["id"] in item and "28字" in item for item in warnings))
        self.assertTrue(any(line["panel_id"] in item and "60字" in item for item in warnings))
