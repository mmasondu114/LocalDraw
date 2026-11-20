import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch, Mock

from comic_workflow import creative, drawing_tools


class InteractionReviewTests(unittest.TestCase):
    def review(self, anatomy=None, **kwargs):
        response={'verdict':'pass','checks':[{'requirement':'两人拥抱','observation':'两个角色身体接触','verdict':'pass'}],
                  'reason':'人数和动作符合','repair':''}
        if anatomy is not None:
            response['interaction_anatomy']=anatomy
        panel={'characters':['甘雨','刻晴'],'drawing_description':'两个人拥抱'}
        with patch.object(creative,'structured',return_value=(response,{})) as generate:
            result=creative.review_image(kwargs.get('panel',panel),kwargs.get('prompt','2girls, hugging'),
                                         'unused.png','',[],'unused',lambda _:None)
        return result,generate.call_args.args

    def anatomy(self):
        return {key:{'verdict':'pass','observation':'可见连接正常，遮挡方向与身体关系一致'}
                for key in ('arm_ownership','wrist_connection','hand_shape','contact_action')}

    def test_body_contact_cannot_override_rejected_fingers(self):
        anatomy=self.anatomy();anatomy['hand_shape']={'verdict':'reject','observation':'手指融合为团状'}
        result,args=self.review(anatomy)
        self.assertEqual(result['verdict'],'reject')
        self.assertIn('interaction_anatomy',args[2]['required'])

    def test_missing_wrist_evidence_cannot_pass(self):
        anatomy=self.anatomy();del anatomy['wrist_connection']
        result,_=self.review(anatomy)
        self.assertEqual(result['verdict'],'uncertain')
        result,_=self.review()
        self.assertEqual(result['verdict'],'uncertain')

    def test_unclear_thumb_downgrades_contact_pass(self):
        anatomy=self.anatomy();anatomy['hand_shape']={'verdict':'uncertain','observation':'拇指与手掌无法辨清'}
        result,_=self.review(anatomy)
        self.assertEqual(result['verdict'],'uncertain')

    def test_verified_connections_can_pass_without_all_fingers_exposed(self):
        anatomy=self.anatomy()
        anatomy['hand_shape']['observation']='近侧手指结构正常；另一只手被躯干遮挡，进入遮挡的前臂连续，没有额外手掌'
        result,args=self.review(anatomy)
        self.assertEqual(result['verdict'],'pass')
        self.assertIn('合理遮挡不要求五指全部露出',args[0])

    def test_pointing_added_to_candidate_still_checks_anatomy(self):
        result,args=self.review(panel={'characters':['琴','莫娜'],'drawing_description':'两人面对面'},
                                prompt='2girls, pointing at each other')
        self.assertEqual(result['verdict'],'uncertain')
        self.assertIn('interaction_anatomy',args[2]['required'])

    def test_noninteractive_single_person_keeps_existing_review_schema(self):
        result,args=self.review(panel={'characters':['甘雨'],'drawing_description':'一人站立'},prompt='1girl standing')
        self.assertEqual(result['verdict'],'pass')
        self.assertNotIn('interaction_anatomy',args[2]['properties'])

    def test_named_hand_owner_reject_cannot_be_overridden_by_normal_fingers(self):
        response={'verdict':'pass','checks':[{'requirement':'拥抱','observation':'符合','verdict':'pass'}],
                  'reason':'动作符合','repair':'','interaction_anatomy':self.anatomy(),
                  'hand_repair_target':{'verdict':'reject','observation':'要求修甘雨，手实际连接刻晴前臂'}}
        target={'character_name':'甘雨','owner':'C001','side':'right','glove':'black'}
        with patch.object(creative,'structured',return_value=(response,{})) as generate:
            result=creative.review_image({'characters':['甘雨','刻晴'],'drawing_description':'两人拥抱'},
                'hugging','unused.png','',[],'unused',lambda _:None,repair_target=target)
        self.assertEqual(result['verdict'],'reject')
        self.assertEqual(generate.call_args.args[1]['requested_hand_repair'],target)
        self.assertIn('hand_repair_target',generate.call_args.args[2]['required'])

    def test_missing_named_hand_owner_evidence_cannot_pass(self):
        response={'verdict':'pass','checks':[{'requirement':'拥抱','observation':'符合','verdict':'pass'}],
                  'reason':'动作符合','repair':'','interaction_anatomy':self.anatomy()}
        with patch.object(creative,'structured',return_value=(response,{})):
            result=creative.review_image({'characters':['甘雨','刻晴'],'drawing_description':'两人拥抱'},
                'hugging','unused.png','',[],'unused',lambda _:None,
                repair_target={'character_name':'甘雨','owner':'C001','side':'right','glove':'black'})
        self.assertEqual(result['verdict'],'uncertain')

    def test_hand_review_receives_candidate_detail_without_counting_another_person(self):
        from PIL import Image
        target={'character_name':'甘雨','owner':'C001','side':'right','glove':'black','rect':[.6,.5,.7,.6]}
        response={'verdict':'uncertain','checks':[],'reason':'待核对','repair':'',
                  'hand_repair_target':{'verdict':'uncertain','observation':'连接被遮挡'}}
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'candidate.png'
            picture=Image.new('RGB',(1000,800),'blue')
            picture.paste('black',(600,400,700,480));picture.save(source)
            with patch.object(creative,'structured',return_value=(response,{})) as generate:
                creative.review_image({'characters':['甘雨'],'drawing_description':'放松搭手'},
                    'hand resting',''+str(source),'',[],Path(folder)/'review',lambda _:None,repair_target=target)
            images=generate.call_args.args[5]
            self.assertEqual(images[0][1],str(source))
            self.assertEqual(len(images),2)
            self.assertIn('同一候选画格',images[1][0])
            self.assertIn('TARGET 1',images[1][0])
            with Image.open(images[1][1]) as crop:
                self.assertEqual(max(crop.size),640)
                self.assertEqual(crop.getpixel((crop.width//2,crop.height//2)),(0,0,0))
                self.assertIn((239,69,60),[rgb for _,rgb in crop.getcolors(maxcolors=crop.width*crop.height)])
            self.assertIn('不重复计数',generate.call_args.args[0])

    def test_owned_local_repair_routes_user_requirement_and_selected_region_to_review(self):
        repo=Mock()
        repo.get.return_value={'canon':{'characters':[{'id':'C001','name':'甘雨'}]}}
        repo.folder.return_value=Path('unused')
        app=Mock(repo=repo)
        batch={'id':'batch','images':[{'id':'image','positive':'hugging','file':'candidate.png'}],
               'render_options':{'local_repair':{'owner':'C001','rect':[.6,.5,.7,.6],
                   'instruction':'natural black glove','requested_instruction':'修复甘雨右手的粘连手指'}}}
        with patch.object(drawing_tools,'source_text',return_value='two people hugging'),\
             patch.object(drawing_tools,'panel_characters',return_value=[]),\
             patch.object(drawing_tools,'record_review') as save,\
             patch.object(creative,'review_image',return_value={'verdict':'reject'}) as review:
            drawing_tools.review_batch(app,'project',{'characters':['C001','C002']},batch,'job',lambda _:None)
        target=review.call_args.kwargs['repair_target']
        self.assertEqual(target,{'kind':'local','owner':'C001','character_name':'甘雨',
            'rect':[.6,.5,.7,.6],'instruction':'修复甘雨右手的粘连手指'})
        self.assertEqual(save.call_args.args[-1]['verdict'],'reject')

    def test_owned_local_detail_failure_overrides_whole_image_pass(self):
        from PIL import Image
        target={'kind':'local','character_name':'甘雨','owner':'C001',
                'instruction':'修复右手粘连手指','rect':[.6,.5,.7,.6]}
        for verdict in ('reject','uncertain',None):
            response={'verdict':'pass','checks':[],'reason':'全图人数正确','repair':''}
            if verdict:
                response['local_repair_target']={'verdict':verdict,'observation':'红框内手指未能辨清'}
            with self.subTest(verdict=verdict),tempfile.TemporaryDirectory() as folder:
                source=Path(folder)/'candidate.png'
                picture=Image.new('RGB',(1000,800),'blue')
                picture.paste('black',(600,400,700,480));picture.save(source)
                with patch.object(creative,'structured',return_value=(response,{})) as generate:
                    result=creative.review_image({'characters':['甘雨']},'hand resting',str(source),
                        '',[],Path(folder)/'review',lambda _:None,repair_target=target)
                args=generate.call_args.args
                self.assertEqual(result['verdict'],'reject' if verdict=='reject' else 'uncertain')
                self.assertEqual(args[1]['requested_local_repair'],target)
                self.assertNotIn('requested_hand_repair',args[1])
                self.assertIn('local_repair_target',args[2]['required'])
                self.assertNotIn('hand_repair_target',args[2]['properties'])
                self.assertEqual(len(args[5]),2)
                with Image.open(args[5][1][1]) as crop:
                    self.assertEqual(max(crop.size),640)
                    self.assertEqual(crop.getpixel((crop.width//2,crop.height//2)),(0,0,0))


if __name__=='__main__':
    unittest.main()
