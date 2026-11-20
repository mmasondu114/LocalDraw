"""Detach the local server and open the browser only after it is responsive."""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

from .paths import ROOT, DATA, settings


def ready(url):
    # Loopback must not depend on the user's VPN or HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url + "/api/session", timeout=1) as response:
            data = json.load(response)
        return (
            isinstance(data, dict)
            and isinstance(data.get("token"), str)
            and bool(data["token"])
        )
    except (OSError, ValueError):
        return False


def start(*, open_browser=True, timeout=30):
    url = f"http://127.0.0.1:{settings()['port']}"
    if not ready(url):
        logs = DATA / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        log_path = logs / f"studio-start-{stamp}-{os.getpid()}.log"
        options = {}
        executable = sys.executable
        if os.name == "nt":
            # The venv console redirector can spawn another console even when
            # its parent is detached. Use the GUI interpreter for both layers.
            windowless = Path(executable).with_name("pythonw.exe")
            if windowless.is_file():
                executable = str(windowless)
            options["creationflags"] = (
                subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            options["start_new_session"] = True
        with log_path.open("ab") as log:
            process = subprocess.Popen(
                [executable, str(ROOT / "launch.py"), "--no-browser"],
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                **options,
            )
        deadline = time.monotonic() + timeout
        while not ready(url):
            if process.poll() is not None:
                raise RuntimeError(f"工作室启动失败，请查看日志：{log_path}")
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"工作室尚未就绪，未打开网页。稍后可重新双击启动入口。日志：{log_path}"
                )
            time.sleep(0.25)
    print(f"工作室已就绪：{url}\n服务在后台运行，可以关闭启动窗口。", flush=True)
    if open_browser:
        webbrowser.open(url)
    return url


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    try:
        start(open_browser=not args.no_browser)
        return 0
    except Exception as exc:
        print(f"启动失败：{exc}", file=sys.stderr, flush=True)
        return 1
