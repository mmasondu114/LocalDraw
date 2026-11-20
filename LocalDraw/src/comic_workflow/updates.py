"""Check the private release feed only when the user asks."""
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
import webbrowser

VERSION = "0.1.7"
REPO = "mmasondu114/LocalDraw"
RELEASES = f"https://github.com/{REPO}/releases/latest"


def check(token=""):
    token = (token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
    if not token and (cli := shutil.which("gh")):
        try:
            login = subprocess.run([cli, "auth", "token", "--hostname", "github.com"], capture_output=True,
                                   text=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if login.returncode == 0:
                token = login.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    if not token:
        return {"current": VERSION, "auth_required": True}
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                 headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                                          "User-Agent": "LocalDraw/" + VERSION})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            tag = json.load(response)["tag_name"]
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 404):
            raise ValueError("无法读取私有发行版。请检查仓库读取权限、令牌有效期，以及是否已发布正式版本。") from None
        raise ValueError(f"GitHub 暂时无法查询（HTTP {exc.code}）。") from None
    except (OSError, ValueError, KeyError):
        raise ValueError("未能读取 GitHub 的版本信息，请稍后重试。") from None
    match = re.fullmatch(r"v?(\d+\.\d+\.\d+)", tag)
    if not match:
        raise ValueError("发行标签需要使用 v主版本.次版本.修订号 格式。")
    latest = match[1]
    return {"current": VERSION, "latest": latest,
            "available": tuple(map(int, latest.split("."))) > tuple(map(int, VERSION.split(".")))}


def open_releases():
    if not webbrowser.open(RELEASES):
        raise ValueError("请在浏览器打开 " + RELEASES)
    return {}
