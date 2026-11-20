import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from curate_character_loras import matches


class CharacterIdentityTests(unittest.TestCase):
    def setUp(self):
        self.touch={'id':'touch','name':'Touch','english':'Touch','aliases':[],'game':'明日方舟'}
        self.amiya={'id':'amiya','name':'阿米娅','english':'Amiya','aliases':[],'game':'明日方舟'}
        self.roster=[self.touch,self.amiya]

    def test_amiya_outfit_is_not_the_operator_touch(self):
        model={'name':'amiya / 阿米娅 for touch the stars (Arknights)','tags':['arknights']}
        version={'name':'v1.0 for IL','trainedWords':[r'amiya_\(guard\)_\(touch_the_stars\)_\(arknights\), amiya \(arknights\)']}
        self.assertFalse(matches(model,self.touch,self.roster,version))
        self.assertTrue(matches(model,self.amiya,self.roster,version))

    def test_a_genuine_touch_identity_remains_searchable(self):
        model={'name':'Touch (Arknights)','tags':['arknights']}
        self.assertTrue(matches(model,self.touch,self.roster,{'name':'v1','trainedWords':['touch (arknights)']}))

    def test_endfield_chen_qianyu_is_not_chen(self):
        chen={'id':'chen','name':'陈','english':"Ch'en",'aliases':['Chen'],'game':'明日方舟'}
        self.assertFalse(matches({'name':'Chen Qianyu - Arknights: Endfield - Illustrious','tags':['arknights']},
                                 chen,[chen],{'name':'V1','trainedWords':['Chen Qianyu']}))

    def test_the_original_chen_still_matches(self):
        chen={'id':'chen','name':'陈','english':"Ch'en",'aliases':['Chen'],'game':'明日方舟'}
        self.assertTrue(matches({'name':"Ch'en / 陈晖洁 [6 outfits] (Arknights)",'tags':['arknights']},
                                chen,[chen],{'name':'v1','trainedWords':[r'chen \(arknights\)']}))


if __name__=='__main__':unittest.main()
