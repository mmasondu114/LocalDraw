import unittest
from unittest.mock import patch
from comic_workflow import comfyui, lora_binding, regional_prompt, regional_lora


class RegionalLoraTests(unittest.TestCase):
    # Complete standing skeleton published in the author's OpenPose example.
    body=[241,77,1,241,120,1,191,118,1,177,183,1,163,252,1,
          298,118,1,317,182,1,332,245,1,225,241,1,213,359,1,
          215,454,1,270,240,1,282,360,1,286,456,1,
          232,59,1,253,60,1,225,70,1,260,72,1]

    def test_joint_backend_keeps_character_without_lora_and_full_original_paths(self):
        import json
        cfg={'width':1216,'height':832,'steps':28,'cfg':5,'sampler_name':'euler_ancestral',
             'scheduler':'normal','regional_lora_hooks':True,'regional_lora_backend':'bv_sdxl'}
        roles=[{'name':'甘雨','prompt':'1girl, blue hair','region':[0,0,.5,1]},
               {'name':'客人','prompt':'1boy, black hair','region':[.5,0,1,1]}]
        original='原神/甘雨_download_filename_with_underscores.safetensors'
        passes=[{'name':'甘雨','positive':roles[0]['prompt'],'region':roles[0]['region'],
                 'loras':[{'file':original,'weight':.7,'clip_weight':.7}]}]
        style=[{'file':'原神/风格_source.safetensors','weight':.35,'clip_weight':.35}]
        graph=comfyui.graph('test','2people, park','bad anatomy',1,cfg,style)
        cast=regional_prompt.apply(graph,{'regional_characters':True,'text':'park','characters':roles},cfg,passes)
        lora_binding.apply_passes(graph,passes,style,cfg,1,'bad anatomy',False,cast)
        doc=json.loads(graph['270']['inputs']['regional_json'])
        loras=json.loads(graph['270']['inputs']['lora_v3_config_json'])
        self.assertEqual([r['name'] for r in doc['regions']],['甘雨','客人'])
        self.assertIn('black hair',doc['regions'][1]['prompts']['positive_source'])
        self.assertEqual(loras['entries'][0]['targets'],[{'scope':'global'}])
        self.assertNotIn('8',graph)
        self.assertEqual(loras['entries'][1]['source']['lora_name'],original)
        self.assertEqual(loras['entries'][1]['targets'][0]['region_id'],doc['regions'][0]['id'])
        self.assertEqual(sum(n['class_type']=='KSampler' for n in graph.values()),1)
        self.assertFalse(any(n['class_type'] in ('ImageCrop','ImageCompositeMasked') for n in graph.values()))

    def test_automatic_standing_pose_does_not_override_action_scene(self):
        from comic_workflow import bv_regional
        cfg={'width':1216,'height':832,'steps':28,'cfg':5,'sampler_name':'euler_ancestral',
             'scheduler':'normal','regional_auto_pose':True,'regional_pose_margin':.18}
        roles=[{'name':'甲','positive':'1girl, blue hair','region':[0,0,.5,1],'loras':[]},
               {'name':'乙','positive':'1girl, red hair','region':[.5,0,1,1],'loras':[]}]
        for scene,expected in [('2girls, full body, standing, park',True),
                               ('2girls, full body, standing, one girl kneeling',False),
                               ('2girls, upper body, standing',False)]:
            graph=comfyui.graph('test',scene,'bad anatomy',1,cfg)
            with patch.object(bv_regional.regional_lora,'apply_pose',
                              side_effect=lambda g,*args:g.update({'280':{'inputs':{}}})) as pose:
                bv_regional.apply(graph,roles,cfg,'bad anatomy')
            self.assertEqual(pose.called,expected)

    def test_standing_template_rejects_compressed_lower_legs(self):
        points=self.body.copy()
        for knee,ankle in ((9,10),(12,13)):
            points[ankle*3:ankle*3+2]=[points[knee*3],points[knee*3+1]+15]
        with patch.object(regional_lora.e,'read',return_value={'people':[{'pose_keypoints_2d':points}]}):
            with self.assertRaisesRegex(ValueError,'腿部比例'):
                regional_lora.standing_pose([{'region':[0,0,1,1]}],{'width':1216,'height':832})

    def test_complete_bodies_fit_their_regions_and_keep_leg_proportions(self):
        roles=[{'region':[i/4,0,(i+1)/4,1]} for i in range(4)]
        with patch.object(regional_lora.e,'read',return_value={'people':[{'pose_keypoints_2d':self.body}]}):
            pose=regional_lora.standing_pose(roles,{'width':1216,'height':832,'regional_pose_margin':.18})
        for i,person in enumerate(pose['people']):
            points=person['pose_keypoints_2d']
            self.assertTrue(all(i*304<=points[j]<=(i+1)*304 for j in range(0,54,3)))
            self.assertTrue(all(149<=points[j]<=683 for j in range(1,54,3)))
            import math
            thigh=math.dist(points[24:26],points[27:29])
            shin=math.dist(points[27:29],points[30:32])
            self.assertAlmostEqual(shin/thigh,.8,delta=.02)

    def test_full_body_layout_rejects_template_without_ankles(self):
        points=[100,100,1]*18
        points[10*3+2]=points[13*3+2]=0
        with patch.object(regional_lora.e,'read',return_value={'people':[{'pose_keypoints_2d':points}]}):
            with self.assertRaisesRegex(ValueError,'脚踝'):
                regional_lora.standing_pose([{'region':[0,0,1,1]}],{'width':1216,'height':832})

    def test_multi_role_loras_share_one_latent_sampler_and_image(self):
        cfg={'width':1216,'height':832,'steps':28,'cfg':5,
             'sampler_name':'dpmpp_2m','scheduler':'karras','regional_lora_hooks':True}
        roles=[{'name':name,'prompt':tags,'region':region} for name,tags,region in [
            ('甘雨','1girl, blue hair',[0,0,.5,1]),
            ('刻晴','1girl, purple hair',[.5,0,1,1])]]
        passes=[{'name':r['name'],'positive':r['prompt'],'region':r['region'],'loras':[
            {'file':'原神/'+r['name']+'_original_filename.safetensors',
             'weight':.55,'clip_weight':.55}]} for r in roles]
        graph=comfyui.graph('test','2girls, park, daylight','bad anatomy',1,cfg)
        regional_prompt.apply(graph,{'regional_characters':True,'text':'2girls, park, daylight','characters':roles},cfg,passes)
        lora_binding.apply_passes(graph,passes,[],cfg,1,'bad anatomy')
        types=[n['class_type'] for n in graph.values()]
        self.assertEqual(types.count('KSampler'),1)
        self.assertEqual(types.count('EmptyLatentImage'),1)
        self.assertEqual(types.count('VAEDecode'),1)
        self.assertNotIn('ImageCrop',types)
        self.assertNotIn('ImageCompositeMasked',types)
        self.assertEqual(graph['7']['inputs']['images'],['6',0])
        self.assertNotIn('prev_hooks',graph['340']['inputs'])
        self.assertEqual(graph['300']['inputs']['lora_name'],passes[0]['loras'][0]['file'])
        self.assertEqual(graph['340']['inputs']['lora_name'],passes[1]['loras'][0]['file'])
        self.assertNotIn('purple hair',graph['310']['inputs']['text'])
        self.assertNotIn('blue hair',graph['350']['inputs']['text'])
        self.assertIn('park',graph['310']['inputs']['text'])
        self.assertNotIn('2girls',graph['310']['inputs']['text'])
        self.assertEqual(graph['308']['inputs']['hooks'],['300',0])
        self.assertEqual(graph['348']['inputs']['hooks'],['340',0])
        self.assertEqual(graph['356']['inputs']['pipeline_in'],['316',0])
        self.assertEqual(graph['297']['class_type'],'ConditioningPipelineCombine')
        self.assertEqual(graph['297']['inputs']['global_negative'],['3',0])


if __name__=='__main__':
    unittest.main()
