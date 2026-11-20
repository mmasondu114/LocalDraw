import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from comic_workflow import anima, comfyui, quick_sample
from comic_workflow.lora_folders import matches
from comic_workflow.lora_names import search_identity
from scripts.split_lora_folders import migrate


class FolderTests(unittest.TestCase):
    def test_migration_preserves_weights_bindings_and_old_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = {'Anima/原神/甘雨_1.safetensors': b'anima', '原神/甘雨_2.safetensors': b'sdxl'}
            for name, content in files.items():
                path = root / 'models/loras' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            data = root / 'data'
            (data / 'lora-curation').mkdir(parents=True)
            (data / 'lora-labels.json').write_text(json.dumps({'原神/甘雨_2.safetensors': {'character_id': 'ganyu'}}))
            (data / 'lora-curation/rename-result.json').write_text(json.dumps({'mapping': {'old.safetensors': '原神/甘雨_2.safetensors'}}))
            result = migrate(root)
            self.assertEqual(result['counts'], {'sdxl': 1, 'anime': 1})
            self.assertEqual((root / 'models/loras/anime/原神/甘雨_1.safetensors').read_bytes(), b'anima')
            self.assertEqual((root / 'models/loras/sdxl/原神/甘雨_2.safetensors').read_bytes(), b'sdxl')
            with patch.object(comfyui, 'DATA', data):
                self.assertEqual(comfyui.lora_file_key('old.safetensors'), 'sdxl/原神/甘雨_2.safetensors')
                self.assertEqual(comfyui.lora_file_key('Anima\\原神\\甘雨_1.safetensors'), 'anime/原神/甘雨_1.safetensors')
                self.assertEqual(comfyui.lora_labels()['sdxl/原神/甘雨_2.safetensors']['character_id'], 'ganyu')
            self.assertEqual(migrate(root)['status'], 'already_migrated')

    def test_migration_collision_leaves_source_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / 'models/loras/sdxl/test.safetensors'
            target.parent.mkdir(parents=True)
            target.write_bytes(b'new')
            source = root / 'models/loras/test.safetensors'
            source.write_bytes(b'old')
            with self.assertRaisesRegex(ValueError, 'overwrite'):
                migrate(root)
            self.assertEqual(source.read_bytes(), b'old')
            self.assertEqual(target.read_bytes(), b'new')

    def test_directory_routing_and_backend_rejection(self):
        self.assertTrue(matches('anime/原神/甘雨.safetensors', anima.CHECKPOINT))
        self.assertFalse(matches('sdxl/原神/甘雨.safetensors', anima.CHECKPOINT))
        self.assertTrue(matches('sdxl\\原神\\甘雨.safetensors', 'NoobAI-XL-v1.1.safetensors'))
        file = 'sdxl/原神/test.safetensors'
        with patch('comic_workflow.lora_inspection.inspect_local', return_value={file: {'structurally_compatible': True}}), \
             patch.object(comfyui, 'lora_labels', return_value={}):
            with self.assertRaisesRegex(ValueError, '文件夹'):
                quick_sample.selected_loras([{'file': file}], anima.CHECKPOINT, {'loras': [file]})

    def test_both_folder_names_preserve_game_search_identity(self):
        roster = {('原神', '甘雨'): {'english': 'Ganyu', 'pinyin': 'gan yu'}}
        for folder in ['sdxl', 'anime']:
            self.assertIn('Ganyu', search_identity(f'{folder}/原神/甘雨_123.safetensors', roster)['english_aliases'])
