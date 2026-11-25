"""Refresh the local backend and apply staged releases at desktop session boundaries."""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from comic_workflow import engine
from comic_workflow.paths import settings
from comic_workflow.startup import start
from desktop_release import STATE, HIDDEN, apply_pending, exclusive, fetch_release, log

LEASE = STATE / "desktop-session.json"
WATCH = """
$p = Get-Process -Name LocalDraw -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -eq $env:LOCALDRAW_WATCH_EXE -and $_.MainWindowHandle -ne 0 } |
    Select-Object -First 1
if ($p) {
    $p.WaitForExit()
    & $env:LOCALDRAW_WATCH_PY $env:LOCALDRAW_WATCH_SCRIPT --closed $env:LOCALDRAW_WATCH_SESSION
}
"""


def background(command, env=None):
    return subprocess.Popen(command, env=env, cwd=ROOT, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=HIDDEN | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def watch_exit(lease):
    if os.name == "nt":
        # Also works with already-installed launcher binaries.
        background(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", WATCH],
                   {**os.environ, "LOCALDRAW_WATCH_EXE":str(ROOT/"LocalDraw.exe"),
                    "LOCALDRAW_WATCH_PY":sys.executable, "LOCALDRAW_WATCH_SCRIPT":__file__,
                    "LOCALDRAW_WATCH_SESSION":lease})


def stop_idle(desktop_open):
    port = settings()["port"]
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.08):
            pass
    except OSError:
        return True
    base = f"http://127.0.0.1:{port}"
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def read(path, body=None, token=None):
        req = urllib.request.Request(base + path)
        if body is not None:
            req.data = json.dumps(body).encode()
            req.add_header("Content-Type", "application/json")
            req.add_header("X-Studio-Token", token)
        with client.open(req, timeout=3) as response:
            return json.load(response)

    try:
        info = read("/api/session")
    except urllib.error.URLError as error:
        if isinstance(error.reason, ConnectionRefusedError):
            return True
        raise
    if Path(info["workspace"]).resolve() != ROOT:
        raise RuntimeError("端口属于其他工作室，未更改该服务")
    if info.get("idle_shutdown"):
        return read("/api/action", {"action":"desktop_idle_shutdown"}, info["token"])["stopping"]
    # One-time transition from servers shipped before graceful shutdown existed.
    from refresh_wardrobe import check_idle, release_studio_workers
    try:
        check_idle(read)
    except RuntimeError:
        return False
    if any(item["busy"] for item in read("/api/model-downloads")["items"]):
        return False
    release_studio_workers(read, info["token"], timeout=6 if desktop_open else 40)
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-File",
                             str(ROOT/"scripts/restart_studio_listener.ps1"), "-Port", str(port)],
                            creationflags=HIDDEN, capture_output=True, timeout=12,
                            env={**os.environ, "LOCALDRAW_REPAIR_SESSION":info["token"]})
    if result.returncode:
        raise RuntimeError("旧后台未通过空闲或进程身份检查，继续使用当前服务")
    return True


def refresh_offline(desktop_open):
    if not stop_idle(desktop_open):
        return False
    # shutdown() acknowledges before cleanup. The writer lock covers actual process teardown.
    with exclusive(ROOT / "data/server.lock"):
        try:
            apply_pending(desktop_open)
        except Exception as error:
            log("更新延后：" + str(error))
            if (STATE/"installing.json").exists():
                raise
    return True


def download(wait=0):
    try:
        with exclusive(STATE/"download.lock", timeout=wait):
            fetch_release()
    except TimeoutError:
        pass
    except Exception as error:
        log("GitHub 更新检查未完成：" + str(error))


def opened(open_browser):
    lease = uuid.uuid4().hex
    with exclusive(STATE/"lifecycle.lock", timeout=30):
        engine.save(LEASE, lease)
        if not open_browser:
            watch_exit(lease)
        background([sys.executable, __file__, "--download"])
        try:
            if not refresh_offline(True):
                log("已有任务，退出后将在任务结束时应用更新")
        except (OSError, RuntimeError) as error:
            log("本地后台刷新延后：" + str(error))
            if (STATE/"installing.json").exists():
                raise
        return start(open_browser=open_browser)


def closed(lease):
    if not LEASE.is_file() or engine.read(LEASE) != lease:
        return
    download(wait=105)
    while True:
        with exclusive(STATE/"lifecycle.lock", timeout=30):
            if engine.read(LEASE) != lease:
                return
            if refresh_offline(False):
                return
        time.sleep(4)


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["--download"]:
            download()
        elif len(sys.argv) == 3 and sys.argv[1] == "--closed":
            closed(sys.argv[2])
    except Exception as error:
        log("退出更新延后：" + str(error))
