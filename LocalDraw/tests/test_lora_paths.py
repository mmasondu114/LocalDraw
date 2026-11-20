import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from comic_workflow import comfyui, engine as e, quick_sample


class LoraPathTests(unittest.TestCase):
    def test_quick_sample_restores_legacy_paths_and_rejects_alias_duplicates(self):
        with patch("comic_workflow.lora_inspection.inspect_local", return_value={
                 "arknights/same.safetensors": {"structurally_compatible": True}}), \
             patch.object(comfyui, "lora_labels", return_value={}):
            selected = quick_sample.selected_loras(
                [{"file": "arknights\\same.safetensors"}], "base",
                {"loras": ["arknights/same.safetensors"]})
            self.assertEqual(selected[0]["file"], "arknights/same.safetensors")
            with self.assertRaisesRegex(ValueError, "重复"):
                quick_sample.selected_loras([
                    {"file": "arknights/same.safetensors"},
                    {"file": "arknights\\same.safetensors"}], "base",
                    {"loras": ["arknights\\same.safetensors"]})

    def test_discovery_keeps_portable_ids_and_exact_backend_names(self):
        names = ["方舟样式.safetensors", "arknights\\阿米娅.safetensors",
                 "genshin\\阿米娅.safetensors"]

        def api(path, **kwargs):
            if path == "/system_stats":
                return {}
            if path == "/object_info/CheckpointLoaderSimple":
                return {"CheckpointLoaderSimple": {"input": {"required": {
                    "ckpt_name": [["base.safetensors"]]}}}}
            if path == "/object_info/LoraLoader":
                return {"LoraLoader": {"input": {"required": {"lora_name": [names]}}}}
            raise AssertionError(path)

        with patch.object(comfyui, "api", side_effect=api), patch.object(comfyui, "lora_path_aliases", return_value={}):
            status = comfyui.discover()
        self.assertTrue(status["ready"])
        self.assertEqual(status["loras"], [name.replace("\\", "/") for name in names])
        self.assertEqual(status["lora_files"]["arknights/阿米娅.safetensors"], names[1])
        self.assertEqual(status["lora_files"]["genshin/阿米娅.safetensors"], names[2])

    def test_label_save_accepts_both_separators_and_preserves_binding(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(comfyui, "DATA", Path(tmp)), \
             patch.object(comfyui, "discover", return_value={"loras": [
                 "arknights\\same.safetensors", "genshin\\same.safetensors"]}), \
             patch("comic_workflow.characters.CharacterLibrary.listing", return_value=[
                 {"id": "saved-a", "name": "阿米娅"}]):
            original = {
                "arknights\\same.safetensors": {"character_id": "saved-a"},
                "genshin/same.safetensors": {"name": "other"},
            }
            e.save(Path(tmp) / "lora-labels.json", original)
            for file in ["arknights/same.safetensors", "arknights\\same.safetensors"]:
                labels = comfyui.save_lora_labels([
                    {"file": file, "name": "阿米娅", "weight": 0.7}])
                self.assertEqual(len(labels), 2)
                self.assertEqual(labels["arknights/same.safetensors"]["character_id"], "saved-a")
                self.assertEqual(labels["genshin/same.safetensors"], original["genshin/same.safetensors"])
            with self.assertRaisesRegex(ValueError, "文件未找到"):
                comfyui.save_lora_labels([
                    {"file": "missing/same.safetensors", "name": "missing", "weight": 0.7}])
            backups = list((Path(tmp) / "lora-label-history").glob("*.json"))
            self.assertTrue(any(e.read(path) == original for path in backups))


if __name__ == "__main__":
    unittest.main()
