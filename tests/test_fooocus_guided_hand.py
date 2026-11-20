import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from comic_workflow import fooocus_guided_hand as hand, guided_hand


class FooocusGuidedHandTests(unittest.TestCase):
    def prepare(self, data):
        folder = data / 'comfy-input/repairs/test'
        folder.mkdir(parents=True)
        Image.new('RGB', (800, 600), 'blue').save(folder / 'original.png')
        Image.new('RGB', (800, 600)).save(folder / 'depth.png')
        mask = Image.new('RGB', (800, 600))
        draw = ImageDraw.Draw(mask)
        draw.polygon([(430, 250), (490, 250), (510, 360), (455, 350)], fill='white')
        mask.save(folder / 'mask.png')
        return {'original': 'repairs/test/original.png', 'mask': 'repairs/test/mask.png',
                'depth': 'repairs/test/depth.png', 'placement': {'size': [800, 600]}}

    def test_crop_keeps_protected_cuff_and_exact_full_resolution_composite_mask(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            repair = self.prepare(data)
            with patch.object(hand, 'DATA', data):
                crop, size, prefix = hand.prepare_crop(repair)
            self.assertEqual(size[0], 768)
            self.assertEqual(size[1] % 64, 0)
            self.assertEqual(prefix, 'repairs/test')
            with Image.open(data / 'comfy-input' / prefix / 'fooocus-exact-mask.png') as mask:
                self.assertEqual(mask.size, (crop[2] - crop[0], crop[3] - crop[1]))
                self.assertEqual(mask.getpixel((420 - crop[0], 300 - crop[1])), (0, 0, 0))
                self.assertEqual(mask.getpixel((475 - crop[0], 300 - crop[1])), (255, 255, 255))
            with patch.object(hand, 'DATA', data), self.assertRaises(ValueError):
                hand.prepare_crop({**repair, 'original': '../../outside.png'})

    def test_author_latent_roles_gpu_head_and_exact_crop_mask_are_connected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / 'data'
            repair = self.prepare(data)
            weights = {}
            for name in hand.WEIGHTS:
                path = root / 'models/inpaint' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'verified-test-weight')
                weights[name] = hashlib.sha256(path.read_bytes()).hexdigest()
            cfg = {'hand_lora': {'file': '原神/手部修复_original.safetensors', 'weight': .35}}
            with patch.object(hand, 'ROOT', root), patch.object(hand, 'DATA', data), \
                 patch.object(hand, 'WEIGHTS', weights), \
                 patch.object(hand.subprocess, 'check_output', return_value=hand.REVISION):
                graph = hand.workflow('waiIllustriousSDXL_v170.safetensors', 123, repair, cfg,
                                      {'name': 'depth.safetensors'}, 'one hand', 'bad hands')
            self.assertEqual(graph['16']['inputs']['latent'], ['15', 2])
            self.assertEqual(graph['17']['inputs']['latent_image'], ['15', 3])
            self.assertEqual(graph['16']['inputs']['patch'], ['14', 0])
            self.assertEqual(graph['14']['class_type'], 'LocalDrawGpuFooocusGuard')
            self.assertEqual(graph['90']['inputs']['mask'], ['49', 0])
            self.assertEqual(graph['49']['inputs']['image'], ['48', 0])
            self.assertEqual(graph['90']['inputs']['destination'], ['47', 0])
            self.assertEqual(graph['44']['inputs']['operation'], 'multiply')
            self.assertEqual(graph['32']['inputs']['lora_name'], cfg['hand_lora']['file'])
            self.assertEqual(graph['17']['inputs']['sampler_name'], 'dpmpp_2m_sde_gpu')

    def test_pose_control_uses_neural_crop_coordinates_and_keeps_source_style_and_hand(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=root/'data';repair=self.prepare(data);weights={}
            for name in hand.WEIGHTS:
                path=root/'models/inpaint'/name;path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(b'checked-weight');weights[name]=hashlib.sha256(path.read_bytes()).hexdigest()
            pose_file=root/'models/controlnet/pose.safetensors';pose_file.parent.mkdir(parents=True)
            pose_file.write_bytes(b'checked-pose')
            control={'name':pose_file.name,'sha256':hashlib.sha256(pose_file.read_bytes()).hexdigest()}
            style='原神/动画截图风格_original.safetensors'
            path=root/'models/loras'/style;path.parent.mkdir(parents=True);path.touch()
            repair['source_loras']=[{'file':style,'weight':.35,'clip_weight':.2}]
            repair['placement']['placement']={'side':'right','wrist':[475.,300.],'rotation':0.,'scale':1.}
            cfg={'hand_lora':{'file':'原神/手部修复_original.safetensors','weight':.35},
                 'pose_control':True,'pose_only':True,'retain_source_style_loras':True,
                 'depth_file':'data/comfy-input/repairs/test/depth.png',
                 'projected_joints':[[i,i/2] for i in range(21)]}
            with patch.object(hand,'ROOT',root),patch.object(hand,'DATA',data),\
                 patch.object(guided_hand,'ROOT',root),patch.object(hand,'WEIGHTS',weights),\
                 patch.object(hand.e,'read',return_value=control),\
                 patch.object(hand.subprocess,'check_output',return_value=hand.REVISION):
                graph=hand.workflow('waiIllustriousSDXL_v170.safetensors',123,repair,cfg,
                                    {'name':'depth.safetensors'},'one hand','bad hands')
            record=json.loads((data/'comfy-input/repairs/test/fooocus-crop.json').read_text())
            crop,size=record['crop'],record['neural_size']
            pose=json.loads(graph['22']['inputs']['pose_json'])
            joints=pose['people'][0]['hand_right_keypoints_2d']
            self.assertEqual(len(joints),63)
            self.assertEqual([pose['canvas_width'],pose['canvas_height']],size)
            self.assertAlmostEqual(joints[0],(475-crop[0])*size[0]/(crop[2]-crop[0]))
            self.assertAlmostEqual(joints[1],(300-crop[1])*size[1]/(crop[3]-crop[1]))
            self.assertEqual(graph['23']['inputs']['control_net'],['20',0])
            self.assertNotIn('21',graph)
            self.assertEqual(graph['20']['inputs']['control_net_name'],pose_file.name)
            self.assertEqual(graph['32']['inputs']['lora_name'],cfg['hand_lora']['file'])
            self.assertEqual(graph['hand-source-style-0']['inputs']['lora_name'],style)
            self.assertEqual(graph['hand-source-style-0']['inputs']['strength_clip'],.2)
            self.assertEqual(graph['31']['inputs']['clip'],['hand-source-style-0',1])
            self.assertEqual(graph['90']['inputs']['mask'],['49',0])


if __name__ == '__main__':
    unittest.main()
