import contextlib
import hashlib
import http.server
import io
import pathlib
import re
import tempfile
import threading
import time
import unittest

from comic_workflow.downloader import ConsoleProgress, download


class DownloadProgressTests(unittest.TestCase):
    def test_partial_chunk_survives_disconnect_and_switches_source(self):
        data = bytes(range(256)) * 4096
        received_ranges = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(s):
                start, end = map(int, s.headers["Range"][6:].split("-"))
                received_ranges.append((s.path.split("?")[0], start))
                body = data[start:end + 1]
                s.send_response(206)
                s.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                s.send_header("Content-Length", str(len(body)))
                s.end_headers()
                # The original source disconnects after another 256 KiB.
                s.wfile.write(body[:256 * 1024] if s.path.startswith("/primary") else body)
                s.wfile.flush()

            def log_message(*args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            parts = pathlib.Path(folder) / "resume.bin.parts"
            parts.mkdir()
            (parts / "0.partial").write_bytes(data[:256 * 1024])
            endpoint = f"http://127.0.0.1:{server.server_port}"
            result = download(folder, 1, endpoint + "/primary", len(data),
                              hashlib.sha256(data).hexdigest(), "resume.bin", len(data),
                              fallback_urls=[endpoint + "/fallback"])
            self.assertEqual(result.read_bytes(), data)
        self.assertEqual(received_ranges, [("/primary", 256 * 1024), ("/fallback", 512 * 1024)])

    def test_resumed_bytes_not_counted_as_network_speed_and_idle_does_not_advance(self):
        clock = [0]
        total = 10 * 1024**2
        p = ConsoleProgress("下载", total, initial=total // 2, clock=lambda: clock[0])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            clock[0] = 1
            p.show(total // 2, transferred=0)
            clock[0] = 2
            p.show(total // 2 + 1024**2, transferred=1024**2)
            clock[0] = 3
            p.show(total // 2 + 1024**2, transferred=1024**2)
        lines = out.getvalue().splitlines()
        self.assertIn("50.0%", lines[0])
        self.assertIn("0.00 MiB/s", lines[0])
        self.assertIn("60.0%", lines[1])
        self.assertIn("1.00 MiB/s", lines[1])
        self.assertIn("60.0%", lines[2])
        self.assertIn("0.00 MiB/s", lines[2])
        self.assertIn("等待测速", lines[2])

    def test_slow_local_transfer_retry_and_verification_are_visible(self):
        data = b"x" * (3 * 1024**2)
        attempts = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(s):
                attempts.append(s.headers.get("Range"))
                if len(attempts) == 1:
                    s.send_error(503, "local retry fixture")
                    return
                # A delayed response must leave progress visible while waiting.
                time.sleep(1.1)
                s.send_response(206)
                s.send_header("Content-Range", f"bytes 0-{len(data)-1}/{len(data)}")
                s.send_header("Content-Length", str(len(data)))
                s.end_headers()
                for pos in range(0, len(data), 1024**2):
                    s.wfile.write(data[pos:pos + 1024**2])
                    s.wfile.flush()
                    if pos < 2 * 1024**2:
                        time.sleep(1.1)

            def log_message(*args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as folder:
            output = io.StringIO()
            args = (folder, 1, f"http://127.0.0.1:{server.server_port}/small-fixture",
                    len(data), hashlib.sha256(data).hexdigest(), "fixture.bin", len(data))
            with contextlib.redirect_stdout(output):
                result = download(*args)
            self.assertEqual(result.read_bytes(), data)
            text = output.getvalue()
            self.assertIn("自动重试", text)
            self.assertIn("连接 1", text)
            percentages = [float(x) for x in re.findall(r"下载 \[.*?\]\s+([\d.]+)%", text)]
            self.assertTrue(any(0 < x < 100 for x in percentages), text)
            self.assertEqual(percentages[-1], 100)
            self.assertIn("合并并校验", text)
            self.assertIn("SHA-256 校验通过", text)
            # A rerun verifies the existing file visibly and makes no HTTP request.
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                download(*args)
            self.assertEqual(len(attempts), 2)
            self.assertIn("校验已有文件", output.getvalue())
            self.assertIn("跳过此模型", output.getvalue())


if __name__ == "__main__":
    unittest.main()
