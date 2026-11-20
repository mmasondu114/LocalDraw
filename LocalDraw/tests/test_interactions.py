import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace
import json
import math
import tempfile
from PIL import ImageChops

from comic_workflow import comfyui, interactions, interaction_masks, native_regional, reference_images


class InteractionTests(unittest.TestCase):
    def test_hug_rejects_detector_face_alias_before_transform(self):
        # Measured nose/eye aliases from the rejected fullbody GPU workflow.
        heads = [((443.1306,236.6016), (438.7176,220.4203), (463.7249,223.3624)),
                 ((440.2572,234.1196), (438.7332,218.88), (463.1166,221.9279))]
        people = []
        for index, points in enumerate(heads):
            body = [0]*54
            body[3:6] = [300+index*100,320,1]
            for joint, point in zip((0,14,15), points):
                body[joint*3:joint*3+3] = [*point,1]
            people.append({'pose_keypoints_2d':body})
        pose = {'people':people}
        before = json.dumps(pose)
        roles = [{'name':'左','region':[0,0,.5,1]}, {'name':'右','region':[.5,0,1,1]}]
        with patch.object(interactions.e, 'read', return_value={'face to face hug':pose}), \
             patch.object(interactions, 'transform') as transform:
            with self.assertRaisesRegex(ValueError, '鼻眼位置重复分配'):
                interactions.published_pose('hugging', roles, {'width':832,'height':1216})
            transform.assert_not_called()
        self.assertEqual(json.dumps(pose), before)

    def test_close_noses_distinct_eyes_and_occluded_faces_remain_valid(self):
        people = []
        for points in [((400,200),(360,185),(380,185)),
                       ((400.5,200),(420,185),(440,185))]:
            body = [0]*54
            for joint, point in zip((0,14,15), points):
                body[joint*3:joint*3+3] = [*point,1]
            people.append({'pose_keypoints_2d':body})
        before = json.dumps(people)
        interactions.validate_head_ownership(people)
        self.assertEqual(json.dumps(people), before)
        # A genuinely hidden face stays absent; no position is supplied for it.
        for joint in (0,14,15):
            people[0]['pose_keypoints_2d'][joint*3:joint*3+3] = [0,0,0]
        before = json.dumps(people)
        interactions.validate_head_ownership(people)
        self.assertEqual(json.dumps(people), before)

    def test_arm_dilation_keeps_displaced_cuffs_with_owner_without_changing_hand_or_pose(self):
        roles = [{'name':'左','region':[0,0,.5,1]}, {'name':'右','region':[.5,0,1,1]}]
        people = []
        for neck in (50, 150):
            body = [0]*54
            body[3:6] = [neck,25,1]
            body[24:27] = [neck,175,1]
            people.append({'pose_keypoints_2d':body})
        people[0]['pose_keypoints_2d'][6:15] = [60,60,1, 80,100,1, 150,125,1]
        people[1]['pose_keypoints_2d'][15:24] = [170,60,1, 170,85,1, 75,95,1]
        hand = [150,125,1]
        for finger in range(5):
            for joint in range(4):
                hand.extend([158+joint*4,115+finger*5,1])
        people[0]['hand_right_keypoints_2d'] = hand
        pose = {'people':people}
        before = json.dumps(pose)
        original = interaction_masks.pair_masks(roles,pose,200,200)
        expanded = interaction_masks.pair_masks(roles,pose,200,200,arm_padding=24)
        for owner,point in ((0,(137,145)),(1,(90,112))):
            self.assertLess(original[owner].getpixel(point),15)
            self.assertGreater(expanded[owner].getpixel(point),240)
        for joint in (4,8,12,16,20):
            self.assertGreater(expanded[0].getpixel(tuple(hand[joint*3:joint*3+2])),240)
        self.assertEqual(json.dumps(pose),before)
        self.assertEqual(ImageChops.add(*expanded).getextrema(),(255,255))
        reverse = interaction_masks.pair_masks(roles[::-1],{'people':people[::-1]},200,200,arm_padding=24)
        self.assertEqual(expanded[0].tobytes(),reverse[1].tobytes())
        with self.assertRaises(Exception):
            interaction_masks.pair_masks(roles,pose,200,200,arm_padding=True)

    def test_visible_fingertips_keep_owner_on_partners_crossing_forearm(self):
        roles = [{'name':'左','region':[0,0,.5,1]},
                 {'name':'右','region':[.5,0,1,1]}]
        people = []
        for neck in (50, 150):
            body = [0]*54
            body[3:6] = [neck,25,1]
            body[24:27] = [neck,175,1]
            people.append({'pose_keypoints_2d':body})
        people[0]['pose_keypoints_2d'][6:15] = [60,60,1, 80,100,1, 130,100,1]
        hand = [130,100,1]
        for finger in range(5):
            for joint in range(4):
                hand.extend([138+joint*4,80+finger*6,1])
        people[0]['hand_right_keypoints_2d'] = hand
        # The second person's forearm runs beneath the first person's thumb
        # and index finger; its own palm is elsewhere, beyond its wrist.
        people[1]['pose_keypoints_2d'][15:24] = [170,60,1, 190,80,1, 115,80,1]
        pose = {'people':people}
        before = json.dumps(pose)
        masks = interaction_masks.pair_masks(roles, pose, 200, 200)
        for joint in (4,8,12,16,20):
            point = tuple(hand[joint*3:joint*3+2])
            self.assertGreater(masks[0].getpixel(point), 240)
            self.assertLess(masks[1].getpixel(point), 15)
        self.assertGreater(masks[1].getpixel((185,80)), 240)
        self.assertEqual(ImageChops.add(*masks).getextrema(), (255,255))
        self.assertEqual(json.dumps(pose), before)
        reverse = interaction_masks.pair_masks(roles[::-1], {'people':people[::-1]}, 200, 200)
        self.assertEqual(masks[0].tobytes(), reverse[1].tobytes())

    def test_body_only_crossed_hand_keeps_owner_beyond_wrist(self):
        roles = [{'name':'左','region':[0,0,.5,1]},
                 {'name':'右','region':[.5,0,1,1]}]
        people = []
        for neck in (50, 150):
            body = [0]*54
            body[3:6] = [neck, 25, 1]
            body[24:27] = [neck, 175, 1]
            people.append({'pose_keypoints_2d':body, 'hand_right_keypoints_2d':[0]*63})
        body = people[0]['pose_keypoints_2d']
        body[6:15] = [60,80,1, 80,100,1, 130,100,1]
        before = json.dumps(people)
        masks = interaction_masks.pair_masks(roles, {'people':people}, 200, 200)
        self.assertGreater(masks[0].getpixel((150,100)), 240)
        self.assertLess(masks[1].getpixel((150,100)), 15)
        self.assertLess(masks[0].getpixel((190,100)), 15)
        self.assertEqual(ImageChops.add(*masks).getextrema(), (255,255))
        self.assertEqual(json.dumps(people), before)
        # No direction is inferred through an occluded elbow.
        body[9:12] = [0,0,0]
        unknown = interaction_masks.pair_masks(roles, {'people':people}, 200, 200)
        self.assertEqual(unknown[0].getpixel((150,100)), 0)

    def test_visible_crossing_hand_keeps_owner_with_occluded_elbow(self):
        roles = [{'name':'左','region':[0,0,.5,1]},
                 {'name':'右','region':[.5,0,1,1]}]
        people = []
        for neck in (50, 150):
            body = [0]*54
            body[3:6] = [neck, 25, 1]
            body[24:27] = [neck, 175, 1]
            people.append({'pose_keypoints_2d':body})
        body = people[0]['pose_keypoints_2d']
        body[6:9] = [65, 60, 1]
        body[12:15] = [170, 80, 1]
        # Only the elbow is occluded. The independently visible hand remains
        # on the partner's side of the old rectangular ownership boundary.
        hand = [170,80,1]
        for finger in range(5):
            for joint in range(4):
                hand.extend([175+joint*5, 70+finger*5, 1])
        people[0]['hand_right_keypoints_2d'] = hand
        masks = interaction_masks.pair_masks(roles, {'people':people}, 200, 200)
        self.assertGreater(masks[0].getpixel((182,80)), 240)
        self.assertLess(masks[1].getpixel((182,80)), 15)
        self.assertEqual(masks[0].getpixel((130,65)), 0)
        self.assertEqual(ImageChops.add(*masks).getextrema(), (255,255))

    def test_half_body_hug_frames_torso_without_squeezing_whole_skeleton_into_shot(self):
        cfg={'width':1216,'height':832,'regional_pose_margin':.18,'regional_hug_preset':'front hug'}
        roles=[{'name':'左','region':[0,0,.5,1]},{'name':'右','region':[.5,0,1,1]}]
        full=interactions.published_pose('hugging',roles,cfg)
        upper=interactions.published_pose('hugging',roles,cfg,upper_body=True)
        for old,new in zip(full['people'],upper['people']):
            before,after=old['pose_keypoints_2d'],new['pose_keypoints_2d']
            self.assertGreater(after[9*3+1],cfg['height'])
            self.assertGreater(after[12*3+1],cfg['height'])
            self.assertEqual(after[10*3:10*3+3],[0,0,0])
            self.assertEqual(after[13*3:13*3+3],[0,0,0])
            self.assertLessEqual(after[8*3+1],cfg['height'])
            self.assertLessEqual(after[11*3+1],cfg['height'])
            # The camera transform changes size, not either person's proportions.
            scales=[]
            for first,last in ((1,2),(1,5),(2,8),(5,11)):
                distance=lambda body: math.hypot(body[first*3]-body[last*3],body[first*3+1]-body[last*3+1])
                scales.append(distance(after)/distance(before))
            self.assertGreater(scales[0],1.5)
            self.assertAlmostEqual(min(scales),max(scales),places=4)

    def test_shared_contact_leaves_body_to_pair_and_keeps_two_head_identities(self):
        cfg = {'width': 1216, 'height': 832, 'regional_pose_margin': .18, 'regional_hug_preset': 'front hug'}
        roles = [{'name': '左', 'region': [0, 0, .5, 1]}, {'name': '右', 'region': [.5, 0, 1, 1]}]
        pose = interactions.published_pose('hugging', roles, cfg)
        masks = interaction_masks.shared_contact_masks(roles, pose, cfg['width'], cfg['height'])
        for index, person in enumerate(pose['people']):
            body = person['pose_keypoints_2d']
            head = tuple(round(v) for v in body[:2])
            wrist = tuple(round(v) for v in body[12:14])
            if body[14] > 0:
                self.assertLess(sum(mask.getpixel(wrist) for mask in masks), 5)
            self.assertEqual(masks[index].getpixel(head), 255)
            self.assertEqual(masks[1-index].getpixel(head), 0)
        reverse = interaction_masks.shared_contact_masks(roles[::-1], {**pose, 'people':pose['people'][::-1]}, cfg['width'], cfg['height'])
        self.assertEqual(masks[0].tobytes(), reverse[1].tobytes())

    def test_crossed_arm_masks_keep_wrist_owner_and_complement_each_other(self):
        cfg = {'width': 1216, 'height': 832, 'regional_pose_margin': .18,
               'regional_hug_pose': 'data/lora-curation/research/photo-hug-references/reviewed-hug-hands.json'}
        roles = [{'name': '左', 'region': [0, 0, .5, 1]},
                 {'name': '右', 'region': [.5, 0, 1, 1]}]
        pose = interactions.published_pose('hugging', roles, cfg)
        masks = interaction_masks.pair_masks(roles, pose, cfg['width'], cfg['height'])
        total = ImageChops.add(*masks)
        self.assertEqual(total.getextrema(), (255, 255))
        for index, joint in ((0, 4), (1, 7)):
            body = pose['people'][index]['pose_keypoints_2d']
            point = tuple(round(v) for v in body[joint*3:joint*3+2])
            self.assertGreater(masks[index].getpixel(point), 240)
            self.assertLess(masks[1-index].getpixel(point), 15)
        reverse = interaction_masks.pair_masks(roles[::-1], {**pose, 'people': pose['people'][::-1]}, cfg['width'], cfg['height'])
        self.assertEqual(masks[0].tobytes(), reverse[1].tobytes())

    def test_hug_conditioning_and_reference_share_the_same_contact_mask(self):
        cfg = {'width': 1024, 'height': 768, 'steps': 28, 'cfg': 5.5,
               'sampler_name': 'euler_ancestral', 'scheduler': 'normal',
               'regional_interactions': True, 'regional_interaction_pose': True,
               'regional_scene_share': .3, 'regional_contact_masks': True}
        roles = [{'name': '甲', 'prompt': '1girl, blue hair', 'region': [0, 0, .5, 1]},
                 {'name': '乙', 'prompt': '1girl, purple hair', 'region': [.5, 0, 1, 1]}]
        graph = comfyui.graph('test', '2girls, hugging each other, park', 'bad anatomy', 1, cfg)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'models/controlnet').mkdir(parents=True)
            (root/'models/controlnet/pose.safetensors').write_bytes(b'x'*100)
            native_regional.e.save(root/'config/multi-person-control.json',
                                  {'name': 'pose.safetensors', 'size': 100, 'strength': .9, 'end_percent': .6})
            with patch.object(native_regional, 'ROOT', root), patch.object(interaction_masks, 'DATA', root/'data'):
                native_regional.apply(graph, [], cfg, 'bad anatomy', characters=roles)
            self.assertEqual(len(list((root/'data/comfy-input/interaction-masks').glob('*.png'))), 2)
            refs = [{'name': role['name'], 'region': role['region'], 'image': 'official.png',
                     'weight': .3, 'end_at': .65} for role in roles]
            reference_images.apply_references(graph, refs, cfg)
            for cond, adapter in (('314', '111'), ('354', '121')):
                self.assertEqual(graph[cond]['inputs']['mask'], graph[adapter]['inputs']['attn_mask'])
                self.assertEqual(graph[graph[cond]['inputs']['mask'][0]]['class_type'], 'ImageToMask')
            self.assertFalse({n['class_type'] for n in graph.values()} & {'ImageCrop', 'ImageCompositeMasked'})

    def test_delayed_hug_reference_leaves_initial_pose_steps_free(self):
        cfg = {'width': 1024, 'height': 768, 'regional_hug_reference_start': .3}
        refs = [{'name': '甲', 'image': 'official.png', 'weight': .3, 'end_at': .65}]
        for action, expected in (('hugging', .3), ('mutual_pointing', 0.)):
            graph = {'5': {'inputs': {'model': ['1', 0]}},
                     '290': {'_meta': {'interaction': action}}}
            reference_images.apply_references(graph, refs, cfg)
            self.assertEqual(graph['111']['inputs']['start_at'], expected)
        for start in (-.1, .65, float('nan')):
            graph = {'5': {'inputs': {'model': ['1', 0]}},
                     '290': {'_meta': {'interaction': 'hugging'}}}
            with self.assertRaises(ValueError):
                reference_images.apply_references(graph, refs, {**cfg, 'regional_hug_reference_start': start})

    def test_explicit_pair_actions_and_negations(self):
        for text, count, expected in [
            ('standing, hugging each other', 2, 'hugging'),
            ('两个人拥抱', 2, 'hugging'),
            ('standing, pointing at each other', 2, 'mutual_pointing'),
            ('两个人互相指着对方', 2, 'mutual_pointing'),
            ('standing, not hugging', 2, None),
            ('不要拥抱，站着', 2, None),
            ('不要互相指着对方，站着', 2, None),
            ('not mutually pointing at each other', 2, None),
            ('两个人抱在一起', 2, 'hugging'),
            ('pointing at a tree', 2, None),
            ('hugging each other', 3, None),
        ]:
            with self.subTest(text=text, count=count):
                self.assertEqual(interactions.kind(text, count), expected)

    def test_back_hug_pair_placement_is_not_repeated_in_single_identity(self):
        text = 'hug from behind, one person standing behind the other, arms wrapped around waist, park, Japanese anime'
        self.assertEqual(interactions.regional_context(text), 'park, Japanese anime')
        self.assertEqual(interactions.regional_context('hugging, on the other side of a park'), 'on the other side of a park')

    def test_pair_relationship_stays_global_without_standing_pose_override(self):
        cfg = {'width': 1024, 'height': 768, 'steps': 28, 'cfg': 5.5,
               'sampler_name': 'euler_ancestral', 'scheduler': 'normal',
               'regional_auto_pose': True, 'regional_interactions': True,
               'regional_scene_share': .3}
        roles = [{'name': '甲', 'prompt': '1girl, blue hair', 'region': [0, 0, .5, 1]},
                 {'name': '乙', 'prompt': '1girl, purple hair', 'region': [.5, 0, 1, 1]}]
        for action in ('hugging each other, arms wrapped around each other',
                       'pointing at each other, each extending one index finger toward the other person'):
            scene = '2girls, full body, standing face to face, ' + action + ', park, Japanese anime'
            graph = comfyui.graph('test', scene, 'bad anatomy', 1, cfg)
            with patch.object(native_regional.regional_lora, 'standing_pose') as pose:
                native_regional.apply(graph, [], cfg, 'bad anatomy', characters=roles)
            pose.assert_not_called()
            self.assertTrue(graph['2']['inputs']['text'].startswith(scene))
            self.assertIn('all feet visible', graph['2']['inputs']['text'])
            self.assertIn('cropped feet', graph['3']['inputs']['text'])
            for key, identity, excluded in [('310', 'blue hair', 'purple hair'),
                                            ('350', 'purple hair', 'blue hair')]:
                prompt = graph[key]['inputs']['text']
                self.assertIn(identity, prompt)
                self.assertNotIn(excluded, prompt)
                self.assertNotIn('each other', prompt)
                self.assertNotIn('hugging', prompt)
                if 'pointing' in action:
                    self.assertIn('pointing right' if key == '310' else 'pointing left', prompt)
                    self.assertIn('one extended index finger', prompt)
                self.assertIn('park', prompt)
                self.assertIn('Japanese anime', prompt)
            for key in ('314', '315', '354', '355'):
                self.assertAlmostEqual(graph[key]['inputs']['strength'], .7)
            types = [n['class_type'] for n in graph.values()]
            self.assertEqual(types.count('KSampler'), 1)
            self.assertFalse(set(types) & {'ImageCrop', 'ImageCompositeMasked', 'ControlNetApplyAdvanced'})
            references = [{'image': 'official.png', 'weight': .3, 'end_at': .65,
                           'white_background': True, 'position': '左'} for _ in roles]
            reference_images.apply_references(graph, references, cfg)
            adapters = [n for n in graph.values() if n['class_type'] == 'IPAdapterAdvanced']
            self.assertEqual(len(adapters), 2)
            self.assertTrue(all('image_negative' in n['inputs'] for n in adapters))

    def test_published_pair_templates_preserve_contacts_and_point_at_partner(self):
        cfg = {'width': 1024, 'height': 768, 'regional_pose_margin': .18}
        roles = [{'name': '右', 'region': [.5, 0, 1, 1]},
                 {'name': '左', 'region': [0, 0, .5, 1]}]
        for action in ('hugging', 'mutual_pointing'):
            pose = interactions.published_pose(action, roles, cfg)
            self.assertEqual(len(pose['people']), 2)
            self.assertGreater(pose['people'][0]['pose_keypoints_2d'][3],
                               pose['people'][1]['pose_keypoints_2d'][3])
            for person in pose['people']:
                self.assertEqual(len(person['pose_keypoints_2d']), 54)
                for key, points in person.items():
                    if key.endswith('keypoints_2d'):
                        self.assertTrue(all(points[i:i+3] == [0, 0, 0]
                                            for i in range(0, len(points), 3) if points[i+2] == 0))
            if action == 'mutual_pointing':
                for index, person in enumerate(pose['people']):
                    hand = person['hand_left_keypoints_2d']
                    target = pose['people'][1-index]['pose_keypoints_2d'][3:5]
                    finger = (hand[24]-hand[15], hand[25]-hand[16])
                    toward = (target[0]-hand[0], target[1]-hand[1])
                    self.assertGreater(sum(a*b for a, b in zip(finger, toward)), 0)
                    cross = finger[0]*toward[1]-finger[1]*toward[0]
                    self.assertAlmostEqual(cross/(math.hypot(*finger)*math.hypot(*toward)), 0, delta=.00001)
                    self.assertEqual(len(hand), 63)

    def test_each_identity_gets_one_pose_and_shared_action_gets_the_pair(self):
        cfg = {'width': 1024, 'height': 768, 'steps': 28, 'cfg': 5.5,
               'sampler_name': 'euler_ancestral', 'scheduler': 'normal',
               'regional_auto_pose': True, 'regional_interactions': True,
               'regional_interaction_pose': True, 'regional_scene_share': .3}
        roles = [{'name': '甲', 'prompt': '1girl, blue hair', 'region': [0, 0, .5, 1]},
                 {'name': '乙', 'prompt': '1girl, purple hair', 'region': [.5, 0, 1, 1]}]
        graph = comfyui.graph('test', '2girls, full body, standing, hugging each other', 'bad anatomy', 1, cfg)
        original_read = native_regional.e.read
        def read(path):
            return {'name': 'pose.safetensors', 'size': 100, 'strength': .9, 'end_percent': .6} if path.name == 'multi-person-control.json' else original_read(path)
        with patch.object(native_regional.e, 'read', side_effect=read), \
             patch.object(Path, 'is_file', return_value=True), \
             patch.object(Path, 'stat', return_value=SimpleNamespace(st_size=100)), \
             patch.object(Path, 'exists', return_value=False):
            native_regional.apply(graph, [], cfg, 'bad anatomy', characters=roles)
        pose = json.loads(graph['280']['inputs']['pose_json'])
        self.assertEqual(len(pose['people']), 2)
        self.assertEqual(graph['283']['inputs']['positive'], ['2', 0])
        self.assertEqual(graph['283']['inputs']['negative'], ['3', 0])
        self.assertEqual(graph['297']['inputs']['positive_DEFAULT'], ['283', 0])
        self.assertEqual(graph['5']['inputs']['positive'], ['297', 0])
        for key in ('320', '360'):
            self.assertEqual(len(json.loads(graph[key]['inputs']['pose_json'])['people']), 1)
        for key in ('283', '321', '361'):
            self.assertEqual(graph[key]['inputs']['strength'], 1.)
            self.assertEqual(graph[key]['inputs']['end_percent'], 1.)
        types = [n['class_type'] for n in graph.values()]
        self.assertEqual(types.count('ControlNetApplyAdvanced'), 3)
        self.assertEqual(types.count('KSampler'), 1)
        self.assertFalse(set(types) & {'ImageCrop', 'ImageCompositeMasked'})

    def test_camera_fit_respects_normal_halfbody_request_and_fullbody_framing(self):
        cfg={**comfyui.config(),'width':1024,'height':768,'steps':28,'cfg':5.5,'sampler_name':'euler_ancestral',
             'scheduler':'normal','regional_interactions':True,'regional_interaction_pose':True,
             'regional_scene_share':.3,'regional_hug_preset':'front hug','regional_contact_masks':False}
        roles=[{'name':'甲','prompt':'1girl, blue hair','region':[0,0,.5,1]},
               {'name':'乙','prompt':'1girl, purple hair','region':[.5,0,1,1]}]
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'models/controlnet').mkdir(parents=True)
            (root/'models/controlnet/pose.safetensors').write_bytes(b'x'*100)
            native_regional.e.save(root/'config/multi-person-control.json',
                {'name':'pose.safetensors','size':100,'strength':.9,'end_percent':.6})
            for shot, override, upper in [('waist up',{},True),('waist up',{'regional_camera_pose_fit':False},False),('full body',{},False)]:
                options={**cfg,**override}
                graph=comfyui.graph('test','2girls, '+shot+', hugging each other','bad anatomy',1,options)
                with patch.object(native_regional,'ROOT',root):
                    native_regional.apply(graph,[],options,'bad anatomy',characters=roles)
                pose=json.loads(graph['280']['inputs']['pose_json'])
                for person in pose['people']:
                    for knee in (9,12):
                        y=person['pose_keypoints_2d'][knee*3+1]
                        self.assertGreater(y,cfg['height']) if upper else self.assertLess(y,cfg['height'])
