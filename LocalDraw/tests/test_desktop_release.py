import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import desktop_release as release
import desktop_update as desktop


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "LocalDraw"
        self.state = self.root / "data/desktop-updates"
        self.state.mkdir(parents=True)
        for attr, value in (("ROOT", self.root), ("STATE", self.state), ("VERSION", "1.0.0")):
            obj = patch.object(release, attr, value)
            obj.start()
            self.addCleanup(obj.stop)
        self.files = {"src/comic_workflow/updates.py":b'VERSION = "1.1.0"\n',
                      "web/app.js":b"new ui", "launch.py":b"# new server", "LocalDraw.exe":b"new launcher"}

    def staged(self, extra=None):
        buf = io.BytesIO()
        with ZipFile(buf, "w") as archive:
            for name, data in {**self.files, **(extra or {})}.items():
                archive.writestr(name, data)
        archive = buf.getvalue()
        asset = dict(name="LocalDraw-1.1.0-Update.zip", size=len(archive), id=123,
                     digest="sha256:" + hashlib.sha256(archive).hexdigest())
        def github(args, timeout, stdout=None):
            if stdout:
                stdout.write(archive)
            else:
                return json.dumps(dict(tag_name="v1.1.0", assets=[asset], draft=False, prerelease=False))
        with patch.object(release, "gh", side_effect=github):
            release.fetch_release()

    def test_download_verify_defer_launcher_and_install_after_exit(self):
        self.staged({"config/settings.json":b"release preferences"})
        (self.root/"config").mkdir()
        (self.root/"config/settings.json").write_bytes(b"local preferences")
        release.apply_pending(True)
        self.assertFalse((self.root/"web/app.js").exists())
        release.apply_pending(False)
        self.assertEqual((self.root/"web/app.js").read_bytes(), b"new ui")
        self.assertEqual((self.root/"config/settings.json").read_bytes(), b"local preferences")
        self.assertFalse((self.state/"pending.json").exists())

    def test_damaged_zip_or_escape_leaves_installation_untouched(self):
        self.staged({"web/../../escape":b"x"})
        with self.assertRaises(ValueError):
            release.apply_pending(False)
        archive = self.state/"LocalDraw-1.1.0-Update.zip"
        archive.write_bytes(archive.read_bytes()+b"x")
        with self.assertRaises(ValueError):
            release.apply_pending(False)
        self.assertFalse((self.root/"launch.py").exists())

    def test_keep_local_patch_if_release_did_not_change_file(self):
        self.staged()
        (self.root/"web").mkdir()
        (self.root/"web/app.js").write_bytes(b"locally patched ui")
        with patch.object(release, "baseline", return_value={"web/app.js":hashlib.sha256(b"new ui").hexdigest()}):
            release.apply_pending(False)
        self.assertEqual((self.root/"web/app.js").read_bytes(), b"locally patched ui")

    def test_partial_failure_rolls_back_and_retry_can_finish(self):
        self.staged()
        (self.root/"web").mkdir()
        (self.root/"web/app.js").write_bytes(b"old")
        real = os.replace
        def fail(source, dest):
            if Path(dest) == self.root/"launch.py":
                raise PermissionError("busy")
            return real(source, dest)
        with patch.object(os, "replace", side_effect=fail), self.assertRaises(PermissionError):
            release.apply_pending(False)
        self.assertEqual((self.root/"web/app.js").read_bytes(), b"old")
        self.assertFalse((self.root/"src/comic_workflow/updates.py").exists())
        release.apply_pending(False)
        self.assertEqual((self.root/"web/app.js").read_bytes(), b"new ui")

    def test_closing_old_window_cannot_stop_new_session_and_busy_server_defers(self):
        lease = self.state/"lease.json"
        lease.write_text('"new"')
        with patch.object(desktop, "LEASE", lease), patch.object(desktop, "refresh_offline") as refresh:
            desktop.closed("old")
            refresh.assert_not_called()
        with patch.object(desktop, "stop_idle", return_value=False), patch.object(desktop, "apply_pending") as apply:
            self.assertFalse(desktop.refresh_offline(True))
            apply.assert_not_called()

    def test_open_and_exit_hooks_apply_staged_files_before_next_launch(self):
        self.staged()
        events = []
        lease = self.state/"lease.json"
        with (patch.multiple(desktop, ROOT=self.root, STATE=self.state, LEASE=lease),
              patch.object(desktop, "watch_exit", side_effect=lambda ident:events.append("watch")),
              patch.object(desktop, "background"), patch.object(desktop, "download"),
              patch.object(desktop, "stop_idle", return_value=True),
              patch.object(desktop, "start", side_effect=lambda **kwargs:events.append("launch"))):
            desktop.opened(False)
            self.assertEqual(events, ["watch", "launch"])
            self.assertTrue((self.state/"pending.json").is_file())
            desktop.closed(json.loads(lease.read_text(encoding="utf-8")))
            self.assertEqual((self.root/"LocalDraw.exe").read_bytes(), b"new launcher")
            self.assertFalse((self.state/"pending.json").exists())

    def test_released_code_merges_independent_local_edits(self):
        base = b"const a = 1;\n" + b"// unchanged\n"*8 + b"const b = 1;\n"
        local = base.replace(b"a = 1", b"a = 3")
        official = base.replace(b"b = 1", b"b = 2")
        self.staged({"web/app.js":official})
        with ZipFile(self.state/"LocalDraw-1.0.0-Update.zip", "w") as archive:
            archive.writestr("web/app.js", base)
        (self.root/"web").mkdir()
        (self.root/"web/app.js").write_bytes(local)
        release.apply_pending(False)
        self.assertEqual((self.root/"web/app.js").read_bytes(), local.replace(b"b = 1", b"b = 2"))

    def test_idle_shutdown_waits_for_requests_jobs_queue_and_downloads(self):
        from comic_workflow.server import make_handler
        stopped, entered, finish = threading.Event(), threading.Event(), threading.Event()
        def action(request):
            entered.set()
            finish.wait(5)
            return {}
        app = SimpleNamespace(token="session", action=action,
            night_queue=SimpleNamespace(lock=threading.RLock(), state={"running":False}),
            jobs=SimpleNamespace(lock=threading.RLock(), controls={}, listing=lambda limit:[]),
            model_downloads=SimpleNamespace(lock=threading.RLock(), active={}))
        handler = make_handler(app)
        def send(action):
            request = object.__new__(handler)
            data = json.dumps({"action":action}).encode()
            request.path = "/api/action"
            request.headers = {"Host":"localhost:1234", "X-Studio-Token":"session", "Content-Length":len(data)}
            request.rfile = io.BytesIO(data)
            request.server = SimpleNamespace(server_port=1234, shutdown=stopped.set)
            result = []
            request.send = lambda status, body: result.append((status, body))
            request.do_POST()
            return result[0]
        close = "desktop_idle_shutdown"
        writer = threading.Thread(target=send, args=("save_workspace",))
        writer.start()
        self.assertTrue(entered.wait(3))
        try:
            self.assertEqual(send(close), (200, {"stopping":False}))
        finally:
            finish.set()
            writer.join()
        for state, key in [(app.night_queue.state, "running"), (app.jobs.controls, "job"),
                           (app.model_downloads.active, "model")]:
            state[key] = True
            self.assertEqual(send(close), (200, {"stopping":False}))
            state.pop(key)
            app.night_queue.state["running"] = False
        self.assertFalse(stopped.is_set())
        self.assertEqual(send(close), (200, {"stopping":True}))
        self.assertTrue(stopped.wait(2))
        self.assertEqual(send("draw_quick_sample")[0], 400)
