"""Check a complete two-pass graph and an integrity rejection before submission."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import hashlib

from comic_workflow import comfyui, hires


class HiresTests(unittest.TestCase):
    def test_hires_pose_copies_reachable_control_branches_without_changing_base(self):
        graph = {
            'positive':{'class_type':'CLIPTextEncode','inputs':{'text':'scene'}},
            'negative':{'class_type':'CLIPTextEncode','inputs':{'text':'bad anatomy'}},
            'local':{'class_type':'ControlNetApplyAdvanced','inputs':{
                'positive':['positive',0],'negative':['negative',0], 'end_percent':.6}},
            'shared':{'class_type':'ControlNetApplyAdvanced','inputs':{
                'positive':['positive',0],'negative':['negative',0], 'end_percent':.6}},
            'combined':{'class_type':'PairConditioningSetDefaultCombine','inputs':{
                'positive':['local',0], 'negative':['local',1],
                'positive_DEFAULT':['shared',0], 'negative_DEFAULT':['shared',1]}},
        }
        import copy
        original = copy.deepcopy(graph)
        roots, controls = hires.pose_conditioning(graph, [['combined',0],['combined',1]])
        self.assertEqual(roots, [['hires-pose-combined',0],['hires-pose-combined',1]])
        self.assertEqual(set(controls), {'hires-pose-local','hires-pose-shared'})
        for key in original:
            self.assertEqual(graph[key], original[key])
        for key in controls:
            self.assertEqual(graph[key]['inputs']['end_percent'],1.)
            self.assertEqual(graph[key]['inputs']['positive'],['positive',0])
        self.assertEqual(graph['hires-pose-combined']['inputs']['negative'],['hires-pose-local',1])

    def test_final_output_uses_whole_image_pass_and_preserves_conditioning(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'up.pth').write_bytes(b'verified fixture')
            recipe = {'file': 'up.pth', 'sha256': hashlib.sha256(b'verified fixture').hexdigest(),
                      'scale': 1.5, 'steps': 20, 'denoise': .35, 'source': 'author'}
            graph = comfyui.graph('model.safetensors', 'scene', 'negative', 31,
                                  {'width': 1024, 'height': 768, 'steps': 28, 'cfg': 5.5,
                                   'sampler_name': 'euler_ancestral', 'scheduler': 'normal'})
            graph['5']['inputs'].update(model=['character_model', 0], positive=['regions', 0], negative=['regions', 1])
            original = dict(graph['5']['inputs'])
            with patch.object(hires, 'ROOT', root), patch.object(hires.e, 'read', return_value=recipe):
                report = hires.apply(graph, {'width': 1024, 'height': 768}, 31)
            self.assertEqual((report['width'], report['height']), (1536, 1152))
            for key in ('model', 'positive', 'negative'):
                self.assertEqual(graph['904']['inputs'][key], original[key])
            self.assertEqual(graph['5']['inputs'], original)
            self.assertEqual(graph['7']['inputs']['images'], ['905', 0])
            self.assertEqual(sum(n['class_type'] == 'SaveImage' for n in graph.values()), 1)
            self.assertFalse(any(n['class_type'] in ('ImageCrop', 'ImageCompositeMasked') for n in graph.values()))

    def test_corrupted_upscaler_rejected_before_graph_mutation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'up.pth').write_bytes(b'wrong')
            with patch.object(hires, 'ROOT', root), patch.object(hires.e, 'read', return_value={'file': 'up.pth', 'sha256': '0' * 64}):
                with self.assertRaisesRegex(ValueError, '校验失败'):
                    hires.apply({}, {'width': 1024, 'height': 768}, 1)
