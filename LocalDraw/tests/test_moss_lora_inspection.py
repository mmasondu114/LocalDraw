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

    def test_krea2_lycoris_kohya_and_diffusers_layouts_without_base_metadata(self):
        layouts = [
            ('lora_unet_blocks_0_attn_', 'gate', 'wk', 'down', 'up'),
            ('diffusion_model.blocks.14.attn.', 'gate', 'wk', 'A', 'B'),
            ('transformer.transformer_blocks.0.attn.', 'to_gate', 'to_k', 'A', 'B'),
            ('lycoris_transformer_blocks_0_attn_', 'to_gate', 'to_k', 'down', 'up'),
        ]
        for prefix, gate_mod, k_mod, down, up in layouts:
            with self.subTest(prefix=prefix):
                header = {'__metadata__': {'ss_network_module': 'lycoris.kohya'}}
                offset = 0
                for mod, out_dim in [(gate_mod, 6144), (k_mod, 1536)]:
                    for direction, shape in [(down, [16, 6144]), (up, [out_dim, 16])]:
                        size = shape[0] * shape[1] * 2
                        header[f'{prefix}{mod}.lora_{direction}.weight'] = {
                            'dtype': 'BF16', 'shape': shape, 'data_offsets': [offset, offset + size]}
                        offset += size
                raw = json.dumps(header).encode()
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / 'krea2_adapter.safetensors'
                    path.write_bytes(struct.pack('<Q', len(raw)) + raw + bytes(offset))
                    result = inspect_lora(path, 'krea2_turbo_bf16.safetensors')
                self.assertEqual(result['family'], 'Krea2')
                self.assertIs(result['structurally_compatible'], True)

    def test_sdxl_animagine_model_name_is_not_misclassified_as_anima(self):
        header = {
            '__metadata__': {
                'ss_base_model_version': 'sdxl_base_v1-0',
                'modelspec.architecture': 'stable-diffusion-xl-v1-base/lora',
                'ss_sd_model_name': 'animagine-xl-3.0.safetensors',
                'ss_network_module': 'networks.lora',
            },
            'weight': {'dtype': 'F32', 'shape': [1], 'data_offsets': [0, 4]},
        }
        raw = json.dumps(header).encode()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sdxl_animagine.safetensors'
            path.write_bytes(struct.pack('<Q', len(raw)) + raw + bytes(4))
            result = inspect_lora(path, 'animagine-xl-4.0-opt.safetensors')
        self.assertEqual(result['family'], 'SDXL')
        self.assertIs(result['structurally_compatible'], True)

    def test_anima_with_stale_sdxl_modelspec_stays_anima(self):
        header = {
            '__metadata__': {
                'ss_base_model_version': 'anima',
                'modelspec.architecture': 'stable-diffusion-xl-v1-base/lora',
                'ss_network_module': 'networks.lora',
            },
            'weight': {'dtype': 'F32', 'shape': [1], 'data_offsets': [0, 4]},
        }
        raw = json.dumps(header).encode()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'anima_stale_spec.safetensors'
            path.write_bytes(struct.pack('<Q', len(raw)) + raw + bytes(4))
            result = inspect_lora(path, 'animaDeltaMix38BQwen0_deltaMixV60.safetensors')
        self.assertEqual(result['family'], 'Anima')
        self.assertIs(result['structurally_compatible'], True)
