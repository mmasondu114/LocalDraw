import copy
import json
import shutil
import subprocess
import unittest

from comic_workflow.clothing_policy import RULES, resolve_prompt
from comic_workflow.paths import ROOT


class ClothingPolicyTests(unittest.TestCase):
    def setUp(self):
        self.cards = [
            {"name": "甲", "profile": {"identity": "alice (game)", "hair": "blue hair",
                "clothing": "black jacket, shorts, boots", "accessories": "black gloves, earrings"}},
            {"name": "乙", "profile": {"identity": "bob (game)", "hair": "red hair",
                "clothing": "blue coat, pants", "accessories": "unknown"}},
        ]
        self.base = "alice (game), (blue hair:1.2), earrings"
        self.old = self.base + ", black jacket, shorts, boots, black gloves"

    def cases(self):
        fallback = self.base + ", black jacket, shorts, boots, black gloves"
        return [
            ("standing in a park", self.base, 0, fallback),
            ("standing on top of a mountain", self.base, 0, fallback),
            ("wearing a white dress", self.old, 0, self.base),
            ("穿白色连衣裙", self.old, 0, self.base),
            ("不穿外套", self.old, 0, self.base),
            ("nude", self.old, 0, self.base),
            ("white_dress", self.old, 0, self.base),
            ("(white dress:1.3)", self.old, 0, self.base),
            ("甲穿白裙，乙在公园", self.old, 0, self.base),
            ("甲穿白裙，乙在公园", "bob (game), red hair", 1, "bob (game), red hair, blue coat, pants"),
            ("Alice in a white dress, Bob in a suit", self.old, 0, self.base),
            ("Alice in a white dress, Bob in a suit", "bob (game), blue coat, pants", 1, "bob (game)"),
            ("甲和乙穿校服", "bob (game), blue coat, pants", 1, "bob (game)"),
            ("Alice and Bob wearing uniforms", self.old, 0, self.base),
            ("park", self.old + ", red dress", 0, self.base + ", red dress"),
            ("park", self.base + ", red dress", 0, self.base + ", red dress"),
            ("white dress", self.old + ", red dress", 0, self.base),
            ("park", self.old, 0, self.old),
        ]

    def test_outfits_override_defaults_without_mutating_cards(self):
        before = copy.deepcopy(self.cards)
        for text, prompt, index, expected in self.cases():
            with self.subTest(text=text, index=index):
                self.assertEqual(resolve_prompt(text, prompt, self.cards[index], self.cards), expected)
        self.assertEqual(self.cards, before)

    @unittest.skipUnless(shutil.which("node"), "Node required for browser/server parity")
    def test_browser_preview_matches_server_for_every_case(self):
        script = """
const fs = require('fs'); global.window = {};
eval(fs.readFileSync('web/clothing.js', 'utf8'));
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const policy = window.createClothingPolicy(input.rules);
process.stdout.write(JSON.stringify(input.cases.map(([text, prompt, i]) =>
  policy.resolve(text, prompt, input.cards[i], input.cards))));
"""
        result = subprocess.run([shutil.which("node"), "-e", script], cwd=ROOT,
            input=json.dumps({"rules": RULES, "cards": self.cards, "cases": self.cases()}),
            text=True, encoding="utf-8", capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), [case[3] for case in self.cases()])
