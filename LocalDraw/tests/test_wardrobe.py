import io
import json
import re
import unittest
from types import SimpleNamespace

from comic_workflow.server import Application, make_handler


class WardrobeTests(unittest.TestCase):
    def test_offline_search_readings_cover_the_catalog(self):
        from pypinyin import lazy_pinyin
        from comic_workflow.wardrobe import catalog

        html = self.request("GET", "/")[1].decode("utf-8")
        readings = json.loads(re.search(r'id="wardrobe-pinyin">(.*?)</script>', html).group(1))
        names = {row[k] for row in catalog()["characters"] for k in ("zh", "work")}
        for name in names:
            if re.search(r"[\u3400-\u9fff]", name):
                self.assertEqual(readings.get(name), " ".join(lazy_pinyin(name)), name)

    def request(self, method, path, payload=None):
        app = Application.__new__(Application)
        app.token = "test"
        handler_type = make_handler(app)
        handler = handler_type.__new__(handler_type)
        raw = json.dumps(payload).encode()
        handler.server = SimpleNamespace(server_port=18765)
        handler.headers = {"Host": "localhost:18765", "X-Studio-Token": "test", "Content-Length": str(len(raw))}
        handler.path, handler.rfile = path, io.BytesIO(raw)
        response = []
        handler.send = lambda *args: response.extend(args)
        getattr(handler, "do_" + method)()
        return response

    def test_no_wardrobe_role_leaves_identity_to_studio(self):
        from comic_workflow.wardrobe import preview

        base = {"seed": 8}
        outfit = preview({**base, "scope": "outfit", "style": "不限"})
        self.assertEqual(preview(base), outfit)
        scene = preview({**base, "scope": "scene"})
        self.assertNotEqual(scene["text"], outfit["text"])
        for result in (scene, outfit):
            tags = result["text"].split(", ")
            self.assertTrue(result["text"])
            self.assertNotIn("solo", tags)
            self.assertNotIn("1girl", tags)
            self.assertNotIn("amiya (arknights)", tags)

    def test_desktop_assets_catalog_and_role_identity(self):
        self.assertIn(b"/prompt-json.js", self.request("GET", "/")[1])
        self.assertIn(b"function wardrobeButton", self.request("GET", "/prompt-json.js")[1])
        status, catalog = self.request("GET", "/api/wardrobe")
        self.assertEqual(status, 200)
        tag = next(c["tag"] for c in catalog["characters"] if c["zh"] == "阿米娅")
        req = dict(action="wardrobe_preview", character=tag, scope="character", style="学院", seed=8)
        status, result = self.request("POST", "/api/action", req)
        self.assertEqual(status, 200)
        self.assertIn("rabbit ears", result["text"])
        self.assertIn("blue eyes", result["text"])
        self.assertEqual(self.request("POST", "/api/action", req)[1], result)
        req["scope"] = "outfit"
        status, outfit = self.request("POST", "/api/action", req)
        self.assertEqual(status, 200)
        self.assertNotIn("amiya", outfit["text"])
        self.assertNotIn("solo", outfit["text"])
        self.assertTrue(outfit["text"])
        self.assertEqual(self.request("POST", "/api/action", {**req, "seed": -1})[0], 400)
