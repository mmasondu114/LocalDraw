import json
import os
import signal
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch

from comic_workflow import startup


class StartupTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for name, value in (("ROOT", self.root), ("DATA", self.root / "data")):
            p = patch.object(startup, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(startup, "settings", return_value={"port": 8765})
        p.start()
        self.addCleanup(p.stop)

    def test_existing_server_is_reused(self):
        with patch.object(startup, "ready", return_value=True), \
             patch.object(startup.subprocess, "Popen") as spawn, \
             patch.object(startup.webbrowser, "open") as browser:
            startup.start()
            spawn.assert_not_called()
            browser.assert_called_once_with("http://127.0.0.1:8765")

    def test_waits_for_readiness_before_opening_browser(self):
        process = Mock()
        process.poll.return_value = None
        with patch.object(startup, "ready", side_effect=[False, False, True]), \
             patch.object(startup.subprocess, "Popen", return_value=process), \
             patch.object(startup.time, "sleep") as sleep, \
             patch.object(startup.webbrowser, "open") as browser:
            sleep.side_effect = lambda _: browser.assert_not_called()
            startup.start()
            sleep.assert_called_once()
            browser.assert_called_once()

    def test_crash_does_not_open_browser_and_reports_log(self):
        process = Mock()
        process.poll.return_value = 1
        with patch.object(startup, "ready", return_value=False), \
             patch.object(startup.subprocess, "Popen", return_value=process), \
             patch.object(startup.webbrowser, "open") as browser:
            with self.assertRaisesRegex(RuntimeError, "启动失败.*studio-start-.*log"):
                startup.start()
            browser.assert_not_called()

    def test_timeout_does_not_open_browser(self):
        process = Mock()
        process.poll.return_value = None
        with patch.object(startup, "ready", return_value=False), \
             patch.object(startup.subprocess, "Popen", return_value=process), \
             patch.object(startup.webbrowser, "open") as browser:
            with self.assertRaisesRegex(RuntimeError, "尚未就绪"):
                startup.start(timeout=0)
            browser.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows console regression")
    def test_desktop_worker_has_no_console(self):
        source = Path(__file__).resolve().parents[1]
        scripts = self.root / ".venv/Scripts"
        scripts.mkdir(parents=True)
        (self.root / "config").mkdir()
        shutil.copy2(source / "LocalDraw.exe", self.root)
        shutil.copy2(source / "LocalDraw.exe.config", self.root)
        shutil.copy2(source / ".venv/Scripts/pythonw.exe", scripts)
        shutil.copy2(source / ".venv/pyvenv.cfg", self.root / ".venv")
        (self.root / "config/settings.json").write_text('{"port":8765}')
        (self.root / "start_studio.py").write_text(
            "import ctypes, json, sys\nfrom pathlib import Path\n"
            "ctypes.windll.kernel32.GetConsoleWindow.restype = ctypes.c_void_p\n"
            "Path('worker.json').write_text(json.dumps({"
            "'console': ctypes.windll.kernel32.GetConsoleWindow() or 0,"
            "'interpreter': sys.executable}))\n"
            "print('desktop worker logging works', flush=True)\n",
            encoding="utf-8",
        )
        env = {**os.environ, "COMIC_STUDIO_ROOT": str(self.root)}
        result = subprocess.run([str(self.root / "LocalDraw.exe"), "--no-browser"],
                                cwd=self.root, env=env, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((self.root / "worker.json").read_text())
        self.assertEqual(report["console"], 0)
        self.assertEqual(Path(report["interpreter"]).name.lower(), "pythonw.exe")
        log = next((self.root / "data/logs").glob("desktop-*.log"))
        self.assertIn("desktop worker logging works", log.read_text())

    @unittest.skipUnless(os.name == "nt", "Windows console regression")
    def test_real_background_python_has_no_console(self):
        report = self.root / "console.json"
        (self.root / "launch.py").write_text(
            "import ctypes, json, sys\nfrom pathlib import Path\n"
            "ctypes.windll.kernel32.GetConsoleWindow.restype = ctypes.c_void_p\n"
            "print('background logging works', flush=True)\n"
            f"Path({str(report)!r}).write_text(json.dumps({{"
            "'console': ctypes.windll.kernel32.GetConsoleWindow() or 0,"
            "'interpreter': sys.executable}))\n",
            encoding="utf-8",
        )
        processes = []
        popen = subprocess.Popen

        def launch(*args, **kwargs):
            process = popen(*args, **kwargs)
            processes.append(process)
            return process

        try:
            with patch.object(startup, "ready", side_effect=lambda _: report.exists()), \
                 patch.object(startup.subprocess, "Popen", side_effect=launch):
                startup.start(open_browser=False, timeout=10)
            self.assertEqual(processes[0].wait(timeout=10), 0)
            result = json.loads(report.read_text())
            self.assertEqual(result["console"], 0)
            self.assertEqual(Path(result["interpreter"]).name.lower(), "pythonw.exe")
            log = next((self.root / "data/logs").glob("studio-start-*.log"))
            self.assertIn("background logging works", log.read_text())
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)


@unittest.skipUnless(os.name == "nt", "Windows EXE launcher acceptance")
class ExeStartupTests(unittest.TestCase):
    def test_exe_can_exit_and_second_launch_reuses_server(self):
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="studio startup ") as directory:
            root = Path(directory)
            (root / "config").mkdir()
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            (root / "config/settings.json").write_text(json.dumps({"port": port}))
            (root / "config/template.json").write_bytes(
                (source / "config/template.json").read_bytes()
            )
            pid_file = root / "test-server.pid"
            (root / "launch.py").write_text(
                "import os, sys\nfrom pathlib import Path\n"
                f"sys.path.insert(0, {str(source / 'src')!r})\n"
                f"Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
                "from comic_workflow.server import main\nmain()\n",
                encoding="utf-8",
            )
            env = {**os.environ, "COMIC_STUDIO_ROOT": str(root),
                   "PYTHONIOENCODING": "utf-8"}
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

            def bootstrap():
                with opener.open(f"http://127.0.0.1:{port}/api/bootstrap", timeout=3) as r:
                    return json.load(r)

            try:
                for attempt in range(2):
                    result = subprocess.run(
                        [str(source / "LocalDraw.exe"), "--no-browser"],
                        cwd=source, env=env, capture_output=True, timeout=45,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    current = bootstrap()  # The GUI launcher has already exited.
                    if attempt == 0:
                        token = current["token"]
                        server_pid = pid_file.read_text()
                    else:
                        self.assertEqual(current["token"], token)
                        self.assertEqual(pid_file.read_text(), server_pid)
                self.assertEqual(current["projects"], [])
            finally:
                if pid_file.exists():
                    os.kill(int(pid_file.read_text()), signal.SIGTERM)
                    for _ in range(30):
                        try:
                            bootstrap()
                        except OSError:
                            break
                        time.sleep(0.1)
                    # Windows' venv redirector releases inherited log handles
                    # shortly after the serving child exits.
                    for log in (root / "data/logs").glob("studio-start-*.log"):
                        for retry in range(30):
                            try:
                                log.unlink()
                                break
                            except PermissionError:
                                if retry == 29:
                                    raise
                                time.sleep(0.1)


if __name__ == "__main__":
    unittest.main()
