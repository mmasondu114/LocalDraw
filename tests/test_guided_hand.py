import copy
import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from comic_workflow import guided_hand, comfyui, engine as e
from comic_workflow.character_binding import identity_hash


class GuidedHandTests(unittest.TestCase):
    def setUp(self):
        self.hand={'owner':'C001','side':'right','glove':'black','view':'back','pose':'relaxed',
                   'wrist':[.4,.4],'palm':[.45,.44]}
        self.rect=[.3,.3,.6,.6]

    def test_rejects_unknown_owner_and_outside_or_nonfinite_anchors(self):
        for update in ({'owner':'C002'},{'owner':[]},{'palm':[.8,.8]},
                       {'palm':[float('nan'),.4]},{'palm':[True,.4]},{'palm':self.hand['wrist']}):
            with self.subTest(update=update),self.assertRaises(ValueError):
                guided_hand.validate_request({**self.hand,**update},self.rect,{'C001'})

    def test_does_not_silently_apply_relaxed_back_reference_to_fist_or_palm(self):
        for update in ({'pose':'fist'},{'view':'palm'},{'side':'unknown'}):
            with self.subTest(update=update),self.assertRaises(ValueError):
                guided_hand.validate_request({**self.hand,**update},self.rect,{'C001'})

    def test_hand_outline_preserves_cuff_inside_rect_and_rejects_bad_edges(self):
        outline=[[.42,.35],[.57,.35],[.57,.55],[.42,.55]]
        hand=guided_hand.validate_request({**self.hand,'outline':outline},self.rect,{'C001'})
        exact=guided_hand.edit_mask((100,100),self.rect,hand)
        noise=guided_hand.edit_mask((100,100),self.rect,hand,rounded=True)
        self.assertEqual(exact.tobytes(),noise.tobytes())
        self.assertEqual(exact.getpixel((38,40)),(0,0,0))  # cuff stays protected inside outer box
        self.assertEqual(exact.getpixel((48,40)),(255,255,255))
        for bad in ([],[[.4,.4]]*3,[[.4,.4],[.45,.45],[.5,.5]],
                    [[.4,.4],[.7,.4],[.5,.5]],[[float('nan'),.4],[.5,.4],[.5,.5]]):
            with self.subTest(outline=bad),self.assertRaises(ValueError):
                guided_hand.validate_request({**self.hand,'outline':bad},self.rect,{'C001'})

    def test_depth_placement_preserves_uniform_geometry_and_mirrors_handedness(self):
        depth=Image.new('L',(40,40),0)
        for x in range(10,30):
            for y in range(8,30):depth.putpixel((x,y),180)
        joints=[[16.,8.]]*21
        joints[9]=[16.,16.]
        results=[]
        for side in ('right','left'):
            fitted,record=guided_hand.fit_depth(depth,joints,(400,400),
                {**self.hand,'side':side,'wrist':[.25,.25],'palm':[.25,.30]})
            self.assertEqual(record['wrist'],[100.,100.])
            self.assertEqual(record['palm'],[100.,120.])
            self.assertAlmostEqual(record['scale'],2.5)
            self.assertAlmostEqual(record['rotation'],0.)
            results.append(record['reference_wrist'][0])
            self.assertIsNotNone(fitted.getbbox())
        self.assertEqual(results,[16.,23.])
        self.assertEqual(joints[0],[16.,8.])

    def test_rejects_implausible_hand_scale(self):
        joints=[[8.,8.]]*21;joints[9]=[8.,16.]
        for palm in ([.251,.251],[.9,.9]):
            with self.subTest(palm=palm),self.assertRaises(ValueError):
                guided_hand.fit_depth(Image.new('L',(40,40)),joints,(400,400),
                    {**self.hand,'wrist':[.25,.25],'palm':palm})

    def test_pose_uses_identical_mesh_transform_and_only_assigned_hand(self):
        cfg=guided_hand.settings()
        with Image.open(guided_hand.ROOT/cfg['depth_file']) as depth:
            for side in ('left','right'):
                hand={**self.hand,'side':side,'wrist':[.4,.4],'palm':[.45,.44]}
                _,placement=guided_hand.fit_depth(depth,cfg['projected_joints'],(400,400),hand)
                pose=guided_hand.pose_from_depth(cfg['projected_joints'],(400,400),placement,depth.width)
                person=pose['people'][0]
                points=person['hand_'+side+'_keypoints_2d']
                self.assertEqual(len(points),63)
                self.assertEqual(points[:3],[160.,160.,1.])
                for axis in (0,1):
                    self.assertAlmostEqual(points[27+axis],hand['palm'][axis]*400)
                # A uniform transform preserves reference distances; mirroring
                # changes chirality without changing hand size or finger count.
                a,b=cfg['projected_joints'][4],cfg['projected_joints'][20]
                expected=math.dist(a,b)*placement['scale']
                self.assertAlmostEqual(math.dist(points[12:14],points[60:62]),expected)
                self.assertEqual(person['hand_'+('right' if side=='left' else 'left')+'_keypoints_2d'],[])
                self.assertFalse(any(person['pose_keypoints_2d']))

    def test_optional_pose_control_stacks_with_depth_in_both_passes(self):
        cfg=copy.deepcopy(guided_hand.settings())
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            files=[('models/loras/'+cfg['hand_lora']['file'],b'lora'),
                   ('models/controlnet/depth.safetensors',b'depth'),
                   ('models/controlnet/pose.safetensors',b'pose')]
            hashes=[]
            for name,content in files:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
                hashes.append(hashlib.sha256(content).hexdigest())
            cfg['hand_lora']['sha256']=hashes[0]
            depth_file=root/cfg['depth_file'];depth_file.parent.mkdir(parents=True,exist_ok=True)
            Image.new('L',(730,730)).save(depth_file)
            control={'name':'depth.safetensors','sha256':hashes[1]}
            pose_control={'name':'pose.safetensors','sha256':hashes[2]}
            def read(path):
                return pose_control if path.name=='multi-person-control.json' else control
            repair={'hand_guidance':self.hand,'original':'original.png','mask':'mask.png',
                'noise_mask':'noise.png','depth':'depth.png','placement':{'size':[400,400],
                    'placement':{'side':'right','wrist':[160.,160.],'rotation':0.,'scale':.1}}}
            with patch.object(guided_hand,'ROOT',root),patch.object(guided_hand,'settings',return_value=cfg),\
                 patch.object(guided_hand.e,'read',side_effect=read),\
                 patch.object(guided_hand.subprocess,'check_output',return_value=cfg['impact_revision']):
                off=guided_hand.workflow('waiIllustriousSDXL_v170.safetensors',123,repair)
                cfg['pose_control']=True
                on=guided_hand.workflow('waiIllustriousSDXL_v170.safetensors',123,repair)
                cfg['pose_only']=True
                pose_only=guided_hand.workflow('waiIllustriousSDXL_v170.safetensors',123,repair)
                left_repair=copy.deepcopy(repair)
                left_repair['hand_guidance']['side']='left'
                left_repair['placement']['placement']['side']='left'
                left=guided_hand.workflow('waiIllustriousSDXL_v170.safetensors',123,left_repair)
                cfg['backend']='fooocus_depth'
                with patch('comic_workflow.fooocus_guided_hand.workflow') as fooocus:
                    for selected in (repair,left_repair):
                        guided_hand.workflow('waiIllustriousSDXL_v170.safetensors',123,selected)
                        text=fooocus.call_args.args[5]
                        side=selected['hand_guidance']['side']
                        self.assertIn(side+' hand',text)
                        self.assertNotIn(('left' if side=='right' else 'right')+' hand',text)
            self.assertNotIn('26',off)
            self.assertEqual(on['26']['inputs']['segs'],['23',0])
            for key in ('70','80'):
                self.assertEqual(off[key]['inputs']['segs'],['23',0])
                self.assertEqual(on[key]['inputs']['segs'],['26',0])
            self.assertEqual(on['25']['inputs']['render_body'],False)
            self.assertEqual(len(json.loads(on['25']['inputs']['pose_json'])['people'][0]['hand_right_keypoints_2d']),63)
            self.assertEqual(on['90']['inputs']['mask'],['12',0])
            self.assertEqual(pose_only['26']['inputs']['segs'],['13',0])
            for key in ('20','21','22','23'):
                self.assertNotIn(key,pose_only)
            for key in ('70','80'):
                self.assertEqual(pose_only[key]['inputs']['segs'],['26',0])
            self.assertEqual(pose_only['32'],on['32'])
            self.assertEqual(pose_only['90'],on['90'])
            self.assertIn('right hand',pose_only['34']['inputs']['text'])
            self.assertNotIn('left hand',pose_only['34']['inputs']['text'])
            self.assertIn('left hand',left['34']['inputs']['text'])
            self.assertNotIn('right hand',left['34']['inputs']['text'])
            self.assertEqual(left['32'],pose_only['32'])
            left_pose=json.loads(left['25']['inputs']['pose_json'])['people'][0]
            self.assertEqual(len(left_pose['hand_left_keypoints_2d']),63)
            self.assertEqual(left_pose['hand_right_keypoints_2d'],[])

    def test_prepare_rejects_template_that_would_clip_outside_user_box(self):
        cfg=guided_hand.settings()
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);Image.new('RGB',(1824,1248)).save(folder/'original.png')
            hand={**self.hand,'wrist':[1018/1824,593/1248],'palm':[1049/1824,610/1248]}
            with self.assertRaisesRegex(ValueError,'修复框不足'):
                guided_hand.prepare(folder,hand,[1010/1824,580/1248,1040/1824,625/1248])
        self.assertTrue(cfg['gpu_only'])

    def test_prepare_rejects_drawn_mask_that_cuts_off_reference_fingers(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);Image.new('RGB',(400,400)).save(folder/'original.png')
            rect=[.1,.1,.9,.9]
            hand={**self.hand,'outline':[[.38,.38],[.48,.38],[.48,.46],[.38,.46]]}
            with self.assertRaisesRegex(ValueError,'没有包住五指参考'):
                guided_hand.prepare(folder,hand,rect)
            hand['outline']=[[.12,.12],[.88,.12],[.88,.88],[.12,.88]]
            record=guided_hand.prepare(folder,hand,rect)
            self.assertTrue(record['all_finger_joints_inside_mask'])

    def test_hand_only_prompt_does_not_reintroduce_sleeve_or_character_portraits(self):
        positive=guided_hand.prompt('black')
        self.assertIn('plain black glove',positive)
        for forbidden in ('sleeve','cuff','1girl','ganyu','keqing','2girls'):
            self.assertNotIn(forbidden,positive)

    def test_source_render_style_retains_weights_without_character_action_or_hand_duplicates(self):
        style='原神/动画截图风格_original.safetensors'
        items=[{'file':style.replace('/','\\'),'weight':.35,'clip_weight':.2},
               {'file':style,'weight':.8},
               {'file':'原神/甘雨_original.safetensors','weight':.7,'character_id':'C001'},
               {'file':'原神/原神全角色_original.safetensors','weight':.7},
               {'file':'原神/拥抱动作_original.safetensors','weight':.6},
               {'file':'原神/手部修复_original.safetensors','weight':.3},
               {'file':'明日方舟/彩色线条_original.safetensors','weight':.2,'target_character_ids':['C002']}]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'models/loras'/style;path.parent.mkdir(parents=True);path.touch()
            with patch.object(guided_hand,'ROOT',root):
                self.assertEqual(guided_hand.source_style_loras({'source_loras':items},{}),[])
                self.assertEqual(guided_hand.source_style_loras({'source_loras':items},{'retain_source_style_loras':True}),
                                 [{'file':style,'weight':.35,'clip_weight':.2}])
                for value in (True,float('nan'),3):
                    with self.subTest(value=value),self.assertRaisesRegex(ValueError,'权重无效'):
                        guided_hand.source_style_loras({'source_loras':[{'file':style,'weight':value}]},
                                                      {'retain_source_style_loras':True})

    def test_real_batch_uses_hand_graph_full_filename_and_skips_saved_art_preset(self):
        panel={'id':'P001-K01','characters':[{'character_id':'C001'}],'aspect_ratio':'3:2'}
        project={'brief':{'character_refs':[]},'canon':{'characters':[{'id':'C001','name':'甲'}]},
                 'pages':{'P001':{'panels':[panel]}}}
        project['drawing_prompts']={panel['id']:{'panel_hash':e.digest(panel),
            'identity_hash':identity_hash(project,panel),'payload':{'positive':'two portraits hugging',
                'negative':'bad','character_anchors':[],'composition_variants':[]}}}
        hand_file='原神/手部修复_original.safetensors'
        hand_graph={'1':{'class_type':'CheckpointLoaderSimple','inputs':{'ckpt_name':'test'}},
                    '32':{'class_type':'CreateHookLora','inputs':{'lora_name':hand_file}},
                    '34':{'class_type':'CLIPTextEncode','inputs':{'text':'one hand'}},
                    '35':{'class_type':'CLIPTextEncode','inputs':{'text':'bad hands'}},
                    '7':{'class_type':'SaveImage','inputs':{'images':['90',0]}}}
        cfg={'width':1216,'height':832,'steps':28,'cfg':5,'sampler_name':'euler_ancestral',
             'scheduler':'normal','checkpoint':'test','endpoint':'http://127.0.0.1:8188'}
        with tempfile.TemporaryDirectory() as tmp:
            repo=Mock();repo.get.return_value=project;repo.folder.return_value=Path(tmp)
            captured=[]
            def offline(path,body=None):
                if path=='/prompt':captured.append(body['prompt'])
                raise ValueError('capture complete')
            with patch.object(comfyui,'config',return_value=cfg),patch.object(comfyui,'api',side_effect=offline),\
                 patch.object(comfyui,'discover',return_value={'ready':True,'checkpoints':['test'],
                     'loras':[hand_file.replace('/','\\')]}),patch.object(comfyui,'ComfyProgress'),\
                 patch('comic_workflow.drawing_settings.load',return_value={'art_preset':'saved'}),\
                 patch('comic_workflow.art_presets.get',side_effect=AssertionError('preset must not alter hand patch')),\
                 patch.object(guided_hand,'workflow',return_value=copy.deepcopy(hand_graph)),\
                 patch.object(guided_hand,'settings',return_value={'hand_lora':{'file':hand_file}}),\
                 patch('comic_workflow.framing.apply_camera',side_effect=lambda p,n,_:(p,n)):
                with self.assertRaisesRegex(ValueError,'capture complete'):
                    comfyui.run_batch(repo,'project',panel['id'],Mock(),'test',1,loras=[],render_options={
                        'reference':False,'local_repair':{'original':'original.png','mask':'mask.png',
                            'instruction':'repair','hand_guidance':self.hand,'placement':{}}})
            self.assertEqual(len(captured),1)
            graph=captured[0]
            self.assertEqual(graph['32']['inputs']['lora_name'],hand_file.replace('/','\\'))
            self.assertEqual(graph['34']['inputs']['text'],'one hand')
            self.assertEqual([n['class_type'] for n in graph.values()].count('CreateHookLora'),1)
            self.assertNotIn('300',graph)


if __name__=='__main__':
    unittest.main()
