"""Check manual placement, corrupt-file recovery, downloads and exact folder selection."""
import hashlib
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from comic_workflow.model_downloads import ModelDownloads


class ModelDownloadTest(unittest.TestCase):
    def test_manual_drop_and_retry(self):
        content = b"sample model" * 300
        class Source(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                assert self.headers["Range"] == f"bytes=0-{len(content)-1}"
                self.send_response(206)
                self.send_header("Content-Range", f"bytes 0-{len(content)-1}/{len(content)}")
                self.end_headers()
                self.wfile.write(content)

        def wait_for(predicate):
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                row = manager.snapshot()["items"][0]
                if predicate(row): return row
                time.sleep(.02)
            self.fail(row)

        with tempfile.TemporaryDirectory() as tmp, ThreadingHTTPServer(("127.0.0.1",0), Source) as server:
            thread = threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            self.addCleanup(server.shutdown)
            root = Path(tmp)
            (root/"config").mkdir()
            entry = dict(id="vae",label="VAE",name="model.bin",use="绘图",folder="vae",size=len(content),
                         sha256=hashlib.sha256(content).hexdigest(),url=f"http://127.0.0.1:{server.server_port}/weights")
            (root/"config/model-downloads.json").write_text(json.dumps([entry]),encoding="utf-8")
            manager = ModelDownloads(root)
            path = manager.path("vae")
            with patch("comic_workflow.model_downloads.os.startfile", create=True) as open_dir:
                manager.open_folder("vae")
                open_dir.assert_called_once_with(str(path.parent))
                self.assertTrue(path.parent.is_dir())
            self.assertRaises(ValueError, manager.open_folder, "../something")
            self.assertFalse(manager.snapshot()["items"][0]["ready"])
            path.write_bytes(b"x"*len(content))
            wait_for(lambda row: row["state"]=="文件校验失败")
            manager.start("vae")
            wait_for(lambda row: row["ready"] and not row["busy"])
            self.assertEqual(path.read_bytes(),content)
            self.assertTrue(ModelDownloads(root).snapshot()["items"][0]["ready"])
            path.unlink()
            self.assertFalse(manager.snapshot()["items"][0]["ready"])
            path.write_bytes(content)
            wait_for(lambda row: row["ready"])
            server.shutdown()
