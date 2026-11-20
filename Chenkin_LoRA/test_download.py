"""Offline checks: filtering, concurrent HTTP downloads, forced interruption, resume, hashes."""
import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import re
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import download as app


class DownloaderTests(unittest.TestCase):
    def test_catalog(self):
        catalog = json.loads((app.ROOT / 'catalog.json').read_text(encoding='utf-8'))
        entries = app.make_manifest(catalog)
        self.assertEqual(len(entries), 147)
        self.assertEqual(len({e['relative_path'] for e in entries}), 147)
        self.assertEqual(sum(e['category'] == 'genshin' for e in entries), 128)
        self.assertEqual(sum(e['category'] == 'arknights' for e in entries), 19)
        self.assertEqual(len(app.make_manifest(catalog, 'all')), 159)
        self.assertEqual(len(app.make_manifest(catalog, 'sdxl')), 107)
        self.assertTrue(all(e['base_model'] in app.SDXL_FAMILY for e in entries))
        self.assertTrue(all(re.fullmatch('[0-9a-f]{64}', e['sha256']) for e in entries))
        self.assertEqual(app.categories(dict(type='LORA', creator={'username': 'Other'}, name='genshin')), [])
        self.assertEqual(app.categories(dict(type='Checkpoint', creator={'username': 'Chenkin'}, name='genshin')), [])
        self.assertNotIn('/', app.safe_name('../../test.safetensors'))
        key = 'secret+key/value'
        self.assertNotIn('secret', app.redact(app.with_token('https://civitai.com/api/download/models/1?format=SafeTensor', key), key))

    def test_forced_stop_resume_and_integrity(self):
        payload = bytes(range(256)) * (64 * 1024)  # 16 MiB per file.
        expected = hashlib.sha256(payload).hexdigest()
        starts, active = [], [0, 0]
        state_lock = threading.Lock()
        delay = [0.08]

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                bounds = re.match(r'bytes=(\d+)-(\d*)', self.headers.get('Range', ''))
                start = int(bounds[1]) if bounds else 0
                end = int(bounds[2]) if bounds and bounds[2] else len(payload) - 1
                end = min(end, len(payload) - 1)
                with state_lock:
                    starts.append(start)
                    active[0] += 1
                    active[1] = max(active)
                self.send_response(206 if bounds else 200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Accept-Ranges', 'bytes')
                self.send_header('Content-Length', str(end - start + 1))
                if bounds:
                    self.send_header('Content-Range', f'bytes {start}-{end}/{len(payload)}')
                self.end_headers()
                try:
                    for offset in range(start, end + 1, 65536):
                        self.wfile.write(payload[offset:min(offset + 65536, end + 1)])
                        time.sleep(delay[0])
                except OSError:
                    pass
                finally:
                    with state_lock:
                        active[0] -= 1

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        real_popen = app.subprocess.Popen
        timers = []

        def interrupted_popen(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            timer = threading.Timer(6.5, child.terminate)
            timer.start()
            timers.append(timer)
            return child

        try:
            with tempfile.TemporaryDirectory(prefix='chenkin-resume-test-') as tmp:
                root = Path(tmp)
                for cat in app.CATEGORIES:
                    (root / cat).mkdir()
                entries = [dict(relative_path=f'{cat}/same-name.safetensors', sha256=expected,
                                bytes=len(payload), url=f'http://127.0.0.1:{server.server_port}/{cat}')
                           for cat in app.CATEGORIES]
                args = SimpleNamespace(jobs=2, connections=2)
                captured = io.StringIO()
                with patch.object(app, 'ROOT', root), contextlib.redirect_stdout(captured):
                    with patch.object(app.subprocess, 'Popen', interrupted_popen):
                        app.download(entries, '', args, None)
                    for timer in timers:
                        timer.join()
                    # Assert actual periodic save succeeded BEFORE recovery helpers run.
                    self.assertNotIn('[ERROR]', captured.getvalue())
                    self.assertTrue(all(Path(str(root / e['relative_path']) + '.aria2').exists() for e in entries))
                    self.assertFalse(list(root.glob('*/*.aria2__temp')))
                    self.assertEqual(app.pending_files(entries), entries)
                    self.assertGreaterEqual(active[1], 2)
                    before = len(starts)
                    delay[0] = 0
                    self.assertEqual(app.download(entries, '', args, None), 0)
                    self.assertNotIn('[ERROR]', captured.getvalue())
                    self.assertTrue(any(offset > 0 for offset in starts[before:]))
                    self.assertTrue(all(app.digest(root / e['relative_path']) == expected for e in entries))
                    self.assertEqual(app.pending_files(entries), [])
                    # A corrupt completed file is preserved, never reported as complete.
                    damaged = root / entries[0]['relative_path']
                    damaged.write_bytes(b'bad data')
                    self.assertEqual(app.pending_files(entries), [entries[0]])
                    self.assertTrue(list((root / '_damaged').iterdir()))
        finally:
            for timer in timers:
                timer.cancel()
            server.shutdown()
            server.server_close()

    def test_civitai_redirect_keeps_key_out_of_downloader(self):
        entry = dict(url='https://civitai.com/api/download/models/1', relative_path='genshin/model.safetensors')
        destination = 'https://b2.civitai.com/file/model.safetensors?temporary=signature'
        error = app.urllib.error.HTTPError(entry['url'], 307, 'Temporary redirect', {'Location': destination}, None)
        with patch.object(app.urllib.request, 'build_opener') as build:
            build.return_value.open.side_effect = error
            resolved = app.resolve_download(entry, 'private-test-key', None)
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.get_header('Authorization'), 'Bearer private-test-key')
            self.assertEqual(resolved['url'], destination)
            self.assertEqual(entry['url'], 'https://civitai.com/api/download/models/1')
            self.assertNotIn('private-test-key', app.aria_input([resolved | {'sha256': 'a' * 64}], ''))
            build.return_value.open.side_effect = app.urllib.error.HTTPError(entry['url'], 403, 'Forbidden', {}, None)
            with self.assertRaisesRegex(RuntimeError, 'HTTP 403'):
                app.resolve_download(entry, 'private-test-key', None)

    def test_network_retry_downloads_only_unfinished_files(self):
        with tempfile.TemporaryDirectory(prefix='chenkin-retry-test-') as tmp:
            root = Path(tmp)
            entries = [dict(relative_path=f'{i}.safetensors', sha256=hashlib.sha256(bytes([i])).hexdigest()) for i in range(2)]
            calls = []
            def transfer(pending, *args):
                calls.append(list(pending))
                i = len(calls) - 1
                (root / entries[i]['relative_path']).write_bytes(bytes([i]))
                return 1 if i == 0 else 0
            with patch.object(app, 'ROOT', root), patch.object(app, 'download', side_effect=transfer), patch.object(app.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
                result, failed = app.download_with_retries(entries, entries, '', None, None)
            self.assertEqual((result, failed), (0, []))
            self.assertEqual(calls, [entries, [entries[1]]])


if __name__ == '__main__':
    unittest.main(verbosity=2)
