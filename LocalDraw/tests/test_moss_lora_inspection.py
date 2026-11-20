import json
import struct
import tempfile
import unittest
from pathlib import Path

from comic_workflow.lora_inspection import inspect_lora


class MossInspectionTests(unittest.TestCase):
    def inspect(self, prefix, down, up, checkpoint, wrong_shape=False):
        header = {}
        offset = 0
        for module, dims in [('q', (3072, 3072)), ('ffn_down', (3072, 8192))]:
            for direction, shape in [(down, [16, dims[1]]), (up, [dims[0], 16])]:
                if wrong_shape:
                    shape = [16, 2048]
                size = shape[0] * shape[1] * 2
                header[f'{prefix}layers.0.proj.{module}.lora_{direction}.weight'] = {
                    'dtype': 'BF16', 'shape': shape, 'data_offsets': [offset, offset + size]}
                offset += size
        raw = json.dumps(header).encode()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'adapter.safetensors'
            path.write_bytes(struct.pack('<Q', len(raw)) + raw + bytes(offset))
            return inspect_lora(path, checkpoint)

    def test_published_layouts_without_architecture_metadata(self):
        for prefix, down, up in [('diffusion_model.', 'A', 'B'), ('diffusion_model.', 'down', 'up'), ('', 'A', 'B')]:
            with self.subTest(prefix=prefix, down=down):
                result = self.inspect(prefix, down, up, 'moss_image_sft_bf16.safetensors')
                self.assertEqual(result['family'], 'MOSS-Image')
                self.assertIs(result['structurally_compatible'], True)

    def test_moss_adapter_rejected_for_other_model_family(self):
        result = self.inspect('diffusion_model.', 'A', 'B', 'krea2_turbo_bf16.safetensors')
        self.assertIs(result['structurally_compatible'], False)

    def test_names_alone_do_not_accept_other_dimensions(self):
        result = self.inspect('diffusion_model.', 'A', 'B', 'moss_image_sft_bf16.safetensors', True)
        self.assertIsNone(result['structurally_compatible'])
