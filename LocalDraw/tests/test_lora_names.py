import unittest
from comic_workflow.lora_names import card_search_identity, display_name, matches_query, normalize_search


class LoraNamesTests(unittest.TestCase):
    def test_filename_suffix_never_becomes_searchable_in_any_language(self):
        file="原神/雷电将军_random_pony_artist123.safetensors"
        roster={("原神","雷电将军"): {"pinyin":"lei dian jiang jun", "english":"Raiden Shogun", "aliases":["Raiden Ei","Baal"]}}
        self.assertEqual(display_name(file), "雷电将军")
        for query in ["雷电", "leidianjiangjun", "lei dian", "Raiden", "BAAL"]:
            self.assertTrue(matches_query(file,query,roster), query)
        for query in ["random", "pony", "artist123", "safetensors", "原神"]:
            self.assertFalse(matches_query(file,query,roster), query)

    def test_windows_separator_and_unicode_english_aliases(self):
        roster={("明日方舟","玛恩纳"): {"pinyin":"ma en na","english":"Młynar","aliases":["Mlynar"]}}
        self.assertTrue(matches_query("明日方舟\\玛恩纳_opaque.safetensors","mlynar",roster))
        self.assertFalse(matches_query("明日方舟\\玛恩纳_opaque.safetensors","opaque",roster))

    def test_card_aliases_use_the_game_and_avoid_ambiguous_names(self):
        roster = {
            ("原神", "空"): {"english": "Aether", "aliases": ["Male Traveler"], "pinyin": "kong"},
            ("明日方舟", "空"): {"english": "Sora", "pinyin": "kong"},
        }
        aether = card_search_identity({"name": "空", "source": {"game": "原神"}}, roster)
        sora = card_search_identity({"name": "空", "source": {"game": "明日方舟"}}, roster)
        unknown = card_search_identity({"name": "空"}, roster)
        self.assertEqual(aether["english_aliases"], ["Aether", "Male Traveler"])
        self.assertEqual(sora["english_aliases"], ["Sora"])
        self.assertEqual(unknown["english_aliases"], [])

    def test_original_custom_and_endfield_cards_are_searchable(self):
        doctor = card_search_identity({"name": "博士", "source": {"mode": "image"}}, {})
        self.assertIn("Doctor", doctor["search_terms"])
        endfield = card_search_identity({"name": "陈千语", "source": {"game": "明日方舟：终末地"}}, {})
        self.assertIn("Chen Qianyu", endfield["search_terms"])
        custom = card_search_identity({"name": "林晴", "profile": {"clothing": "red jacket"}}, {})
        self.assertEqual(normalize_search(custom["pinyin"]), "linqing")
        self.assertNotIn("red jacket", custom["search_terms"])

    def test_model_prefix_in_subdirectory_exposes_real_name_for_search(self):
        file = "krea2/原神/角色/krea2_mitya.safetensors"
        self.assertEqual(display_name(file), "mitya")
        self.assertTrue(matches_query(file, "mitya", {}))
        self.assertEqual(display_name("krea2/原神/角色/krea2_mitya_v2.safetensors"), "mitya")
        self.assertEqual(display_name("krea2/Krea2_HMNSFW_AIO.safetensors"), "Krea2_HMNSFW_AIO")
        roster = {("原神", "雷电将军"): {"pinyin": "lei dian jiang jun", "english": "Raiden Shogun"}}
        self.assertTrue(matches_query("krea2/原神/角色/雷电将军_版本3383815.safetensors", "raiden", roster))

    def test_spaced_and_multi_segment_model_prefixes_extract_character_names(self):
        cases = [
            ("krea2/原神/角色/Krea 2_钟离_mmasondu_1000步.safetensors", "钟离"),
            ("krea2/原神/角色/Krea 2_枫原万叶_mmasondu_1000步.safetensors", "枫原万叶"),
            ("krea2/无畏契约/角色/Krea 2_黑梦_mmasondu_1000步.safetensors", "黑梦"),
            ("krea2/无畏契约/角色/Krea 2_捷风_mmasondu_1000步.safetensors", "捷风"),
            ("krea2/明日方舟/角色/Krea2_可露希尔_mmasondu_1000步.safetensors", "可露希尔"),
            ("krea2/原神/角色/Krea_2_芙宁娜_1000步.safetensors", "芙宁娜"),
            ("krea2/原神/角色/Krea 2 Raw_温迪_1000步.safetensors", "温迪"),
            ("krea2/画风/Krea2Rella_c1-st8000.safetensors", "Krea2Rella"),
            ("krea2/画风/Detailer-KREA2.safetensors", "Detailer-KREA2"),
        ]
        for file, expected in cases:
            with self.subTest(file=file):
                self.assertEqual(display_name(file), expected)
        self.assertTrue(matches_query("krea2/无畏契约/角色/Krea 2_黑梦_mmasondu_1000步.safetensors", "fade", {}))
        self.assertTrue(matches_query("krea2/无畏契约/角色/Krea 2_黑梦_mmasondu_1000步.safetensors", "heimeng", {}))
        self.assertTrue(matches_query("krea2/原神/角色/Krea 2_米提亚_mmasondu_1000步.safetensors", "mitya", {}))
        self.assertTrue(matches_query("krea2/明日方舟/角色/Krea2_可露希尔_mmasondu_1000步.safetensors", "closure", {}))
        self.assertFalse(matches_query("krea2/无畏契约/角色/Krea 2_黑梦_mmasondu_1000步.safetensors", "mmasondu", {}))
