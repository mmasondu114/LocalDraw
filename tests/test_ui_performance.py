import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from comic_workflow import comfyui, engine as e, lora_inspection


class InventoryPerformanceTests(unittest.TestCase):
    def test_local_inventory_does_not_wait_for_model_server(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(comfyui, "ROOT", Path(tmp)), \
                patch.object(comfyui, "config", return_value={"checkpoint": "base"}), \
                patch.object(comfyui, "api", side_effect=AssertionError("Must not probe")) as api:
            path = Path(tmp) / "models/loras/sdxl/test.safetensors"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"test")
            result = comfyui.discover(probe=False)
            self.assertEqual(result["loras"], ["sdxl/test.safetensors"])
            self.assertIsNone(result["ready"])
            api.assert_not_called()

    def test_header_cache_tracks_file_checkpoint_and_download_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "models/loras/test.safetensors"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"initial")
            e.save(root / "config/comfyui.json", {"checkpoint": "base"})
            with patch.object(lora_inspection, "ROOT", root), \
                    patch.object(lora_inspection, "DATA", root / "data"), \
                    patch.object(lora_inspection, "inspect_lora", return_value={"note": "ok"}) as inspect:
                first = lora_inspection.inspect_local()
                first["test.safetensors"]["note"] = "mutated by caller"
                self.assertEqual(lora_inspection.inspect_local()["test.safetensors"]["note"], "ok")
                self.assertEqual(inspect.call_count, 1)
                path.write_bytes(b"replacement payload")
                lora_inspection.inspect_local()
                self.assertEqual(inspect.call_count, 2)
                lora_inspection.inspect_local(checkpoint="different")
                self.assertEqual(inspect.call_count, 3)
                Path(str(path) + ".aria2").touch()
                lora_inspection.inspect_local()
                self.assertEqual(inspect.call_count, 4)
                inspect.assert_called_with(path, checkpoint="base")


if __name__ == "__main__":
    unittest.main()
