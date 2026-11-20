import copy, hashlib, http.server, pathlib, sys, tempfile, threading, unittest

root = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from comic_workflow.engine import read, validate_project, merged, digest
from comic_workflow.downloader import download


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.p = read(root / "config/example.json")

    def test_complete_example(self):
        warnings = validate_project(self.p, True)
        self.assertTrue(all("道具未入镜" in x for x in warnings))

    def test_missing_panel(self):
        self.p["pages"]["P001"]["panels"].pop()
        with self.assertRaises(ValueError):
            validate_project(self.p)

    def test_duplicate_panel(self):
        self.p["pages"]["P001"]["panels"][1]["id"] = "P001-K01"
        with self.assertRaises(ValueError):
            validate_project(self.p)

    def test_missing_speaker(self):
        self.p["pages"]["P001"]["lettering"][0]["speaker_id"] = "C999"
        with self.assertRaises(ValueError):
            validate_project(self.p)

    def test_missing_bubble_panel(self):
        self.p["pages"]["P001"]["lettering"][0]["panel_id"] = "P999-K01"
        with self.assertRaises(ValueError):
            validate_project(self.p)

    def test_state_mismatch(self):
        self.p["pages"]["P002"]["state_in"]["time"] = "白天"
        with self.assertRaises(ValueError):
            validate_project(self.p)

    def test_patch_preserves_everything_else(self):
        target = "P002-K03"
        page = self.p["pages"]["P002"]
        panel = copy.deepcopy(page["panels"][2])
        texts = copy.deepcopy([x for x in page["lettering"] if x["panel_id"] == target])
        texts[0]["text"] = "外头还下着。"
        q = merged(self.p, "panel", target, {"panel": panel, "lettering": texts})
        self.assertEqual(q["pages"]["P001"], self.p["pages"]["P001"])
        self.assertEqual(q["pages"]["P002"]["panels"], page["panels"])
        self.assertEqual(q["pages"]["P002"]["lettering"][:-1], page["lettering"][:-1])
        self.assertNotEqual(digest(q), digest(self.p))

    def test_patch_cannot_change_state(self):
        panel = copy.deepcopy(self.p["pages"]["P002"]["panels"][2])
        panel["state_after"]["time"] = "白天"
        with self.assertRaises(ValueError):
            merged(self.p, "panel", "P002-K03", {"panel": panel, "lettering": []})

    def test_plan_wrong_page_count(self):
        self.p["plan"]["pages"].pop()
        with self.assertRaises(ValueError):
            validate_project(self.p)

    def test_empty_story_not_complete(self):
        self.p["pages"] = {}
        with self.assertRaises(ValueError):
            validate_project(self.p, True)

    def test_upstream_is_protected(self):
        with self.assertRaises(ValueError):
            merged(self.p, "outline", "", self.p["outline"])

    def test_downloader_resume_hash(self):
        data = bytes(range(256)) * 600
        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(s):
                start, end = map(int, s.headers["Range"][6:].split("-"))
                seen.append(start)
                body = data[start : end + 1]
                s.send_response(206)
                s.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                s.send_header("Content-Length", str(len(body)))
                s.end_headers()
                s.wfile.write(body)

            def log_message(*args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as d:
                parts = pathlib.Path(d) / "test.bin.parts"
                parts.mkdir()
                (parts / "0").write_bytes(data[:4096])
                (parts / "1").write_bytes(b"incomplete")
                out = download(
                    d,
                    4,
                    f"http://127.0.0.1:{server.server_port}/file",
                    len(data),
                    hashlib.sha256(data).hexdigest(),
                    "test.bin",
                    4096,
                )
                self.assertEqual(out.read_bytes(), data)
                self.assertNotIn(0, seen)
                self.assertFalse(parts.exists())
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
