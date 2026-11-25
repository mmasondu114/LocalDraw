import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from comic_workflow import lora_binding as lb, comfyui, drawing_settings, engine as e
from comic_workflow.character_binding import identity_hash


class LoraBindingTests(unittest.TestCase):
    def test_editing_labels_preserves_imported_version_prompt_and_preference(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            file = "明日方舟/测试版本.safetensors"
            e.save(root / "lora-labels.json", {file: {
                "name": "测试角色", "character_id": "saved-a", "character": "甲",
                "trigger_words": "old", "weight": .7, "preferred": True,
                "appearance_prompt": "red hair, dark coat", "source_page": "https://example.com/model",
            }})
            with patch.object(comfyui, "DATA", root), \
                 patch.object(comfyui, "discover", return_value={"loras": [file]}), \
                 patch("comic_workflow.characters.CharacterLibrary.listing", return_value=[{"id": "saved-a", "name": "甲"}]):
                result = comfyui.save_lora_labels([{"file": file, "name": "测试角色", "weight": .8, "trigger_words": "new"}])[file]
            self.assertTrue(result["preferred"])
            self.assertEqual(result["appearance_prompt"], "red hair, dark coat")
            self.assertEqual(result["source_page"], "https://example.com/model")
            self.assertEqual(result["trigger_words"], "new")

    def setUp(self):
        self.p = {"brief": {"character_refs": [
            {"id": "saved-a", "name": "甲", "revision": 1, "profile": {}},
            {"id": "saved-b", "name": "甲乙", "revision": 1, "profile": {}},
        ]}, "canon": {"characters": [{"id": "C001", "name": "甲"}, {"id": "C002", "name": "甲乙"}]}}
        self.panel = {"id": "P001-K01", "characters": [{"character_id": "C001"}, {"character_id": "C002"}], "aspect_ratio": "3:2"}
        self.payload = {"positive": "two people, street", "negative": "blurry", "composition_variants": [],
            "character_anchors": [{"character_id":"C001","tags":"red hair"},{"character_id":"C002","tags":"blue hair"}],
            "framing": {"subjects": [{"character_id":"C001","region":[0,0,.5,1]},
                                     {"character_id":"C002","region":[.5,0,1,1]}]}}
        self.loras = [self.lora("style"), self.lora("genshin/same.safetensors", "saved-a"),
                      self.lora("arknights/same.safetensors", "saved-b")]
        self.cfg = {"width":1216,"height":832,"steps":28,"cfg":5,"sampler_name":"euler_ancestral","scheduler":"normal"}

    def lora(self, file, owner=""):
        return {"file":file,"character_id":owner,"weight":.7,"clip_weight":.7,"trigger_words":file,
                "compatible_checkpoint":"test","confirmed_compatibility":True}

    def resolved(self):
        return lb.route(self.loras,self.p,self.panel)

    def test_absent_character_is_excluded_and_name_prefix_is_not_a_match(self):
        panel={**self.panel,"characters":[{"character_id":"C001"}]}
        result=lb.route(self.loras,self.p,panel)
        self.assertEqual([l["file"] for l in result],["style","genshin/same.safetensors"])
        self.assertEqual(result[1]["target_character_ids"],["C001"])
        self.p["brief"]["character_refs"][0]["id"]="new-card-with-same-name"
        self.assertEqual([l["file"] for l in lb.route(self.loras,self.p,panel)],["style"])

    def test_shared_binding_overrides_stale_client_or_retry_metadata(self):
        labels={"style":{"character_id":"saved-b","character":"甲乙"}}
        panel={**self.panel,"characters":[{"character_id":"C001"}]}
        self.assertEqual(lb.route([self.loras[0]],self.p,panel,labels),[])
        rebound=lb.route(self.resolved(),self.p,self.panel,{self.loras[1]["file"]:{"character_id":""}})
        self.assertNotIn("target_character_ids",rebound[1])
        self.assertEqual(rebound[1]["character_id"],"")

    def test_legacy_multi_owner_is_never_silently_mixed(self):
        item={"file":"old","scope":"characters","characters":["甲","甲乙"]}
        with self.assertRaisesRegex(ValueError,"只能关联一个角色"):
            lb.route([item],self.p,self.panel)





    def test_comic_auto_selection_matches_saved_ids(self):
        prefs={"mode":"characters","checkpoint":"test","items":[
            {**l,"name":l["file"],"selected":False,"confirmed":True} for l in self.loras]}
        panel={**self.panel,"characters":[{"character_id":"C002"}]}
        with patch.object(comfyui,"lora_labels",return_value={}):
            result=drawing_settings.loras_for_panel(prefs,self.p,panel)
        self.assertEqual([l["file"] for l in result],["arknights/same.safetensors"])




    def test_full_paths_and_partial_label_saves_preserve_other_folders(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(comfyui,"DATA",Path(tmp)), \
             patch.object(comfyui,"discover",return_value={"loras":[l["file"] for l in self.loras]}), \
             patch("comic_workflow.characters.CharacterLibrary.listing",return_value=self.p["brief"]["character_refs"]):
            for item in self.loras[1:]:
                comfyui.save_lora_labels([{**item,"name":"同名角色文件"}])
            labels=comfyui.lora_labels()
            self.assertEqual(len(labels),2)
            self.assertEqual(labels["genshin/same.safetensors"]["character_id"],"saved-a")
            self.assertEqual(labels["arknights/same.safetensors"]["character_id"],"saved-b")
            self.assertEqual(len(list((Path(tmp)/"lora-label-history").glob("*.json"))),1)




if __name__ == "__main__":
    unittest.main()
