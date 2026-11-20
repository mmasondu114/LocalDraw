import copy
import sys
import tempfile
import unittest
from pathlib import Path

from comic_workflow.character_prompt_cleanup import clean_tags, default_trigger, default_visual_prompt, visual_profile
from comic_workflow.clothing_policy import known, resolve_prompt
from comic_workflow import engine as e

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from clean_character_prompts import prepare
from sync_lora_chinese_library import profile


class CharacterPromptCleanupTests(unittest.TestCase):
    def test_marketing_loaders_and_templates_do_not_become_tags(self):
        self.assertEqual(clean_tags('<lora:model:1>, tifa, 蒂法, blue hair, blue hair, Please give my Lora a thumbs up 👍, unknown'), 'tifa, blue hair')
        self.assertFalse(known('来源资料未核实该项；保持既有模型设定，不自动补造人物特征或背景。'))
        self.assertFalse(known('甲；本地LoRA来源档案，类型：人物角色'))
        self.assertEqual(clean_tags('click, unknown_artist_token, (blue hair:1.2), shenhe \\(genshin impact\\)'), 'click, unknown_artist_token, (blue hair:1.2), shenhe \\(genshin impact\\)')

    def test_distinct_costumes_are_not_concatenated(self):
        self.assertEqual(default_trigger(['alice, blue hair, red eyes', 'white dress, boots', 'black bikini, sandals']),
                         'alice, blue hair, red eyes, white dress, boots')
        self.assertEqual(default_trigger(['s1alice, white dress', 's2alice, blue suit']), 's1alice, white dress')
        self.assertEqual(default_trigger(['mar1n, blonde hair | red eyes', 'Shizuku Kuroe, purple hair, cosplay']), 'mar1n, blonde hair, red eyes')
        self.assertEqual(default_trigger(['alice', 'blue hair', 'red eyes']), 'alice, blue hair, red eyes')
        self.assertEqual(default_trigger(['Promts for this LoRA:', 'alice, blue hair']), 'alice, blue hair')

    def test_imported_costume_is_overridden_by_scene(self):
        p = visual_profile('alice, blue hair, green blazer, white capelet, brown loafers, red eyes')
        card = {'name': '甲', 'profile': p}
        base = default_visual_prompt(p)
        self.assertEqual(base, 'alice, blue hair, red eyes')
        self.assertIn('green blazer', resolve_prompt('park', base, card, [card]))
        self.assertEqual(resolve_prompt('wearing a white dress', base, card, [card]), base)

    def test_audit_preserves_source_and_authored_biography_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            card = {'id': 'a', 'name': '甲', 'revision': 1, 'profile': profile('甲', 'character'),
                    'source': {'asset_kind': 'character', 'primary_loras': {'anime': 'anime/a.safetensors'}}, 'image_id': None}
            card['profile']['identity'] = '甲；本地LoRA来源档案，类型：人物角色'
            card['profile']['personality'] = '用户手写的真实剧情资料'
            labels = {'anime/a.safetensors': {'character_id': 'a', 'trigger_words': 'alice, blue hair, Please like',
                      'appearance_prompt': 'alice, blue hair, Please like', 'source_trained_words': ['alice, blue hair', 'Please like']}}
            e.save(root / 'data/lora-labels.json', labels)
            e.save(root / 'data/characters/cards/a/current.json', card)
            originals, before, cards, after, rows = prepare(root)
            self.assertEqual(before, labels)
            self.assertEqual(originals[0], card)
            self.assertEqual(cards[0]['profile']['personality'], '用户手写的真实剧情资料')
            self.assertEqual(after['anime/a.safetensors']['source_trained_words'], labels['anime/a.safetensors']['source_trained_words'])
            self.assertEqual(rows[0]['effective_prompt'], 'alice, blue hair')
            e.save(root / 'data/lora-labels.json', after)
            e.save(root / 'data/characters/cards/a/current.json', cards[0])
            self.assertEqual(prepare(root)[2:4], (cards, after))


if __name__ == '__main__':
    unittest.main()
