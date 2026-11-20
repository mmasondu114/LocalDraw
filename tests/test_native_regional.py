import json
import copy
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from PIL import Image, ImageChops

from comic_workflow import comfyui, lora_binding, native_regional, regional_prompt, interaction_masks
from tests import test_regional_lora as pose_fixture


class NativeRegionalTests(unittest.TestCase):
    def config(self):
        return {'width':1024, 'height':768, 'steps':28, 'cfg':5.5,
                'sampler_name':'euler_ancestral', 'scheduler':'normal',
                'regional_lora_hooks':True, 'regional_lora_backend':'native_masked',
                'regional_auto_pose':True, 'regional_pose_margin':.18}

    def hug_fixture(self):
        roles=[{'name':'甘雨','prompt':'1girl, blue hair, black_gloves','region':[0,0,.5,1]},
               {'name':'刻晴','prompt':'1girl, purple hair, purple gloves','region':[.5,0,1,1]}]
        people=[]
        for side in (0,1):
            body=[0.]*54
            entries=({1:(50,40),2:(50,50),3:(100,80),4:(132,65),5:(65,50),6:(70,100),7:(80,145),8:(45,145),11:(65,145)}
                     if side==0 else
                     {1:(150,40),2:(145,50),3:(140,90),4:(103,143),5:(160,50),6:(115,100),7:(74,128),8:(145,145),11:(165,145)})
            for joint,xy in entries.items():body[joint*3:joint*3+3]=[*xy,1.]
            hand=[132.,65.,1.] if side==0 else [74.,128.,1.]
            for finger in range(5):
                for segment in range(4):
                    hand.extend([140+segment*5 if side==0 else 66-segment*5,
                                 50+finger*6 if side==0 else 112+finger*6,1.])
            people.append({'pose_keypoints_2d':body,'face_keypoints_2d':[],
                'hand_right_keypoints_2d':hand if side==0 else [],
                'hand_left_keypoints_2d':hand if side==1 else []})
        return roles,{'canvas_width':200,'canvas_height':200,'people':people}

    def test_hand_partition_preserves_owner_weights_and_does_not_complete_partial_hands(self):
        roles,pose=self.hug_fixture();before=copy.deepcopy(pose)
        owned,bodies,hands=interaction_masks.split_complete_hand_masks(roles,pose,200,200)
        self.assertEqual([(h['owner_index'],h['side']) for h in hands],[(0,'right'),(1,'left')])
        for index in range(2):
            total=bodies[index]
            for hand in hands:
                if hand['owner_index']==index:
                    self.assertEqual(ImageChops.subtract(hand['mask'],owned[index]).getbbox(),None)
                    total=ImageChops.add(total,hand['mask'])
            self.assertEqual(total.tobytes(),owned[index].tobytes())
        self.assertEqual(pose,before)
        pose['people'][0]['hand_right_keypoints_2d'][62]=0
        owned,bodies,hands=interaction_masks.split_complete_hand_masks(roles,pose,200,200)
        self.assertEqual([(h['owner_index'],h['side']) for h in hands],[(1,'left')])
        self.assertEqual(owned[0].tobytes(),bodies[0].tobytes())
        self.assertEqual(pose['people'][0]['hand_right_keypoints_2d'][62],0)

    def test_hand_conditioning_keeps_shared_model_hooks_single_canvas_and_complete_pose(self):
        roles,pose=self.hug_fixture()
        globals=[{'file':'原神/动画截图风格_original.safetensors','weight':.35,'clip_weight':.35},
                 {'file':'原神/手部修复_original.safetensors','weight':.3,'clip_weight':.3}]
        passes=[{'name':role['name'],'positive':role['prompt'],'region':role['region'],
                 'loras':[{'file':'原神/'+role['name']+'_original.safetensors','weight':.7,'clip_weight':.7}]} for role in roles]
        cfg={**self.config(),'width':200,'height':200,'regional_interactions':True,
             'regional_interaction_pose':True,'regional_contact_masks':True,
             'regional_scene_share':.3,'regional_hug_hand_conditioning':True}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);weight=root/'models/controlnet/pose.safetensors';weight.parent.mkdir(parents=True);weight.write_bytes(b'pose')
            with patch.object(native_regional,'ROOT',root),patch.object(interaction_masks,'DATA',root/'data'),\
                 patch.object(native_regional.e,'read',return_value={'name':weight.name,'size':4}),\
                 patch.object(native_regional.interactions,'published_pose',return_value=copy.deepcopy(pose)):
                graph=comfyui.graph('test','2girls, full body, hugging each other, park','bad hands',1,cfg)
                native_regional.apply(graph,passes,cfg,'bad hands',characters=roles,global_loras=globals)
                for index,side,colour in ((0,'right','black'),(1,'left','purple')):
                    prefix='contact-hand-'+str(index)+'-'+side
                    positive=graph[prefix+'-positive']['inputs']
                    self.assertEqual(positive['clip'],['296',0])
                    self.assertIn(side+' hand',positive['text']);self.assertIn(colour+' glove',positive['text'])
                    self.assertNotIn('1girl',positive['text']);self.assertNotIn('hair',positive['text'])
                    masked=graph[prefix+'-positive-mask']['inputs']
                    self.assertAlmostEqual(masked['strength'],.7)
                    self.assertEqual(masked['set_cond_area'],'default')
                    self.assertEqual(graph[prefix+'-control']['inputs']['image'],[str(320+index*40),0])
                    self.assertEqual(json.loads(graph[str(320+index*40)]['inputs']['pose_json'])['people'],[pose['people'][index]])
                self.assertEqual(graph['296']['inputs']['hooks'],['201',0])
                self.assertEqual(graph['201']['inputs']['prev_hooks'],['200',0])
                types=[node['class_type'] for node in graph.values()]
                self.assertEqual(types.count('KSampler'),1);self.assertEqual(types.count('VAEDecode'),1)
                self.assertFalse(set(types)&{'ImageCrop','ImageCompositeMasked'})
                # Ordinary scenes keep the existing graph when the opt-in is off.
                off=comfyui.graph('test','2girls, full body, hugging each other, park','bad hands',1,cfg)
                native_regional.apply(off,passes,{**cfg,'regional_hug_hand_conditioning':False},'bad hands',characters=roles,global_loras=globals)
                self.assertFalse(any(key.startswith('contact-hand-') for key in off))
                with self.assertRaisesRegex(ValueError,'现有手部模型'):
                    native_regional.apply(comfyui.graph('test','2girls, hugging each other','bad hands',1,cfg),
                        passes,cfg,'bad hands',characters=roles,global_loras=globals[:1])

    def test_each_conditioning_has_one_full_canvas_pose_and_its_own_identity(self):
        cfg = self.config()
        roles = [{'name':'甘雨', 'prompt':'1girl, blue hair', 'region':[0,0,.5,1]},
                 {'name':'客人', 'prompt':'1boy, black hair', 'region':[.5,0,1,1]}]
        path = '原神/甘雨_original_name_with_underscores.safetensors'
        passes = [{'name':'甘雨', 'positive':roles[0]['prompt'], 'region':roles[0]['region'],
                   'loras':[{'file':path, 'weight':.7, 'clip_weight':.7}]}]
        style = [{'file':'原神/动画截图风格_original.safetensors', 'weight':.35, 'clip_weight':.35}]
        graph = comfyui.graph('test', '2people, full body, standing side by side, park', 'bad anatomy', 1, cfg, style)
        cast = regional_prompt.apply(graph, {'regional_characters':True,
                'text':'full body, standing side by side, park', 'characters':roles}, cfg, passes)
        def read(path):
            if path.name == 'multi-person-control.json':
                return {'name':'pose.safetensors', 'size':100}
            return {'people':[{'pose_keypoints_2d':pose_fixture.RegionalLoraTests.body}]}
        with patch.object(native_regional.e, 'read', side_effect=read), \
             patch.object(Path, 'is_file', return_value=True), \
             patch.object(Path, 'stat', return_value=SimpleNamespace(st_size=100)), \
             patch.object(Path, 'exists', return_value=False):
            lora_binding.apply_passes(graph, passes, style, cfg, 1, 'bad anatomy', False, cast)
        self.assertNotIn('8', graph)
        self.assertEqual(graph['300']['inputs']['lora_name'], path)
        self.assertEqual(graph['300']['inputs']['prev_hooks'], ['200',0])
        self.assertEqual(graph['348']['inputs']['hooks'], ['200',0])
        self.assertNotIn('black hair', graph['310']['inputs']['text'])
        self.assertNotIn('blue hair', graph['350']['inputs']['text'])
        self.assertNotIn('side by side', graph['310']['inputs']['text'])
        for base, left, right in [(300,0,512),(340,512,1024)]:
            hint = json.loads(graph[str(base+20)]['inputs']['pose_json'])
            self.assertEqual((hint['canvas_width'],hint['canvas_height']),(1024,768))
            self.assertEqual(len(hint['people']),1)
            points = hint['people'][0]['pose_keypoints_2d']
            self.assertTrue(all(left <= points[j] <= right for j in range(0,54,3)))
            self.assertEqual(graph[str(base+21)]['inputs']['image'],[str(base+20),0])
            self.assertEqual(graph[str(base+14)]['inputs']['set_cond_area'],'default')
        types = [node['class_type'] for node in graph.values()]
        self.assertEqual(types.count('KSampler'),1)
        self.assertEqual(types.count('VAEDecode'),1)
        self.assertFalse(set(types) & {'ImageCrop','ImageCompositeMasked','AttentionCouple'})
        self.assertEqual(graph['297']['class_type'],'PairConditioningSetDefaultCombine')

    def test_standing_pose_does_not_replace_an_action_or_upper_body_scene(self):
        roles = [{'name':'甲', 'prompt':'1girl, blue hair', 'region':[0,0,.5,1]},
                 {'name':'乙', 'prompt':'1girl, kneeling', 'region':[.5,0,1,1]}]
        passes = [{'name':'甲', 'positive':roles[0]['prompt'], 'region':roles[0]['region'], 'loras':[]}]
        for scene in ('2girls, full body, standing, park', '2girls, upper body, standing, park',
                      '2girls, not full body, standing, park'):
            graph = comfyui.graph('test', scene, 'bad anatomy', 1, self.config())
            with patch.object(native_regional.regional_lora,'standing_pose') as pose:
                native_regional.apply(graph, passes, self.config(), 'bad anatomy', characters=roles)
            pose.assert_not_called()
            self.assertFalse(any(node['class_type']=='ControlNetApplyAdvanced' for node in graph.values()))

    def test_explicit_sitting_uses_published_seated_pose_per_character(self):
        roles = [{'name':'甲','prompt':'1girl, blue hair','region':[0,0,.5,1]},
                 {'name':'乙','prompt':'1girl, purple hair','region':[.5,0,1,1]}]
        graph = comfyui.graph('test', '2girls, full body, sitting on a bench', 'bad anatomy', 1, self.config())
        original_read = native_regional.e.read
        def read(path):
            return {'name':'pose.safetensors','size':100} if path.name=='multi-person-control.json' else original_read(path)
        with patch.object(native_regional.e,'read',side_effect=read), \
             patch.object(Path,'is_file',return_value=True), \
             patch.object(Path,'stat',return_value=SimpleNamespace(st_size=100)), \
             patch.object(Path,'exists',return_value=False), \
             patch.object(native_regional.regional_lora,'standing_pose') as standing:
            native_regional.apply(graph, [], self.config(), 'bad anatomy', characters=roles)
        standing.assert_not_called()
        self.assertEqual(graph['290']['_meta']['pose'], 'seated')
        self.assertIn('all feet visible', graph['2']['inputs']['text'])
        for key in ('310', '350'):
            self.assertIn('head to toe', graph[key]['inputs']['text'])
        for key in ('3', '311', '351'):
            self.assertIn('cropped feet', graph[key]['inputs']['text'])
        for key in ('320','360'):
            hint=json.loads(graph[key]['inputs']['pose_json'])
            self.assertEqual(len(hint['people']),1)
            points=hint['people'][0]['pose_keypoints_2d']
            self.assertTrue(all(points[i*3+2]>0 for i in (8,9,10,11,12,13)))
            self.assertNotEqual(points[8*3], points[9*3])

    def test_white_negative_reference_requires_reviewed_scene_and_all_white_backdrops(self):
        from comic_workflow import reference_images
        references = [{'image': 'official.png', 'weight': .3, 'end_at': .65,
                       'position': 'left', 'region': [0, 0, .5, 1], 'white_background': True},
                      {'image': 'official2.png', 'weight': .3, 'end_at': .65,
                       'position': 'right', 'region': [.5, 0, 1, 1], 'white_background': True}]
        for pose, count, cast, white, share, expected in [('seated', 2, 2, True, 0., True), ('standing', 2, 2, True, .15, False),
                                                 ('seated', 4, 4, True, .15, False), ('seated', 2, 2, False, 0., False),
                                                 ('seated', 2, 4, True, 0., False), ('standing', 3, 3, True, .15, True),
                                                 ('standing', 4, 4, True, 0., False), ('standing', 4, 4, False, .15, False),
                                                 ('standing', 3, 4, True, .15, False)]:
            with self.subTest(pose=pose, count=count, cast=cast, white=white):
                refs = [dict(references[i % 2], white_background=white) for i in range(count)]
                graph = {'5': {'inputs': {'model': ['290', 0]}},
                         '290': {'_meta': {'pose': pose, 'characters': ['role'] * cast, 'scene_share': share}}}
                reference_images.apply_references(graph, refs, self.config())
                self.assertEqual('reference-white-background' in graph, expected)
                for node in graph.values():
                    if node.get('class_type') == 'IPAdapterAdvanced':
                        self.assertEqual('image_negative' in node['inputs'], expected)

    def test_shared_scene_retains_one_canvas_and_preserves_other_framing_recipes(self):
        cfg = {**self.config(), 'regional_scene_share': .15}
        for count, scene, expected in [(3, '3girls, full body, standing in a park', .85),
                                       (4, '4girls, full body, standing in a park', .85),
                                       (2, '2girls, full body, standing in a park', 1.),
                                       (2, '2girls, full body, sitting on a bench', 1.),
                                       (3, '3girls, upper body, standing in a park', 1.)]:
            with self.subTest(count=count, scene=scene):
                roles = [{'name': str(i), 'prompt': '1girl, blue hair',
                          'region': [i/count, 0, (i+1)/count, 1]} for i in range(count)]
                graph = comfyui.graph('test', scene, 'bad anatomy', 1, cfg)
                original_read = native_regional.e.read
                def read(path):
                    return {'name': 'pose.safetensors', 'size': 100} if path.name == 'multi-person-control.json' else original_read(path)
                with patch.object(native_regional.e, 'read', side_effect=read), \
                     patch.object(Path, 'is_file', return_value=True), \
                     patch.object(Path, 'stat', return_value=SimpleNamespace(st_size=100)), \
                     patch.object(Path, 'exists', return_value=False):
                    native_regional.apply(graph, [], cfg, 'bad anatomy', characters=roles)
                types = [node['class_type'] for node in graph.values()]
                self.assertEqual(types.count('KSampler'), 1)
                self.assertEqual(types.count('VAEDecode'), 1)
                self.assertFalse(set(types) & {'ImageCrop', 'ImageCompositeMasked'})
                self.assertNotIn('1girl', graph['2']['inputs']['text'])
                for i in range(count):
                    for offset in (14, 15):
                        self.assertEqual(graph[str(300+i*40+offset)]['inputs']['strength'], expected)

    def test_white_reference_background_requires_every_corner_without_changing_image(self):
        from comic_workflow import reference_images
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'reference.png'
            image = Image.new('RGB', (12, 16), 'white')
            image.putpixel((6, 8), (0, 0, 200))
            image.save(path)
            before = path.read_bytes()
            self.assertTrue(reference_images.white_reference_background(path))
            self.assertEqual(path.read_bytes(), before)
            image.putpixel((0, 0), (0, 0, 0))
            image.save(path)
            self.assertFalse(reference_images.white_reference_background(path))



if __name__ == '__main__':
    unittest.main()
