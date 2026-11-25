"""Activate the installed Wardrobe API on an idle Windows desktop backend."""

import json
import os
import subprocess
import sys
import time
from datetime import datetime
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from comic_workflow.startup import start

PROBE = dict(action="wardrobe_preview", character="amiya_(arknights)", scope="character",
             level="sfw", style="学院", seed=8)
BUSY = {"queued", "running", "pausing", "paused", "cancelling"}

def report(message):
    print(message, flush=True)
    try:
        path = ROOT / "data/logs/wardrobe-repair.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as log:
            log.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {message}\n")
    except OSError:
        pass


def missing_catalog(read):
    try:
        data = read("/api/wardrobe")
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 404) and json.load(exc).get("error") == "页面不存在":
            return True
        raise
    if not data.get("characters"):
        raise ValueError("后台返回了空角色库。")
    return False


def check_idle(read):
    status = read("/api/status")
    if status["queue"]["running"] or any(j["status"] in BUSY for j in status["jobs"]):
        raise RuntimeError("工作室还有运行或暂停的任务。请先完成任务，再重新打开修复入口。")
    return status


def release_studio_workers(read, token, *, timeout=40):
    status = check_idle(read)
    if status["model"].get("owned"):
        read("/api/action", {"action": "stop_model"}, token)
    try:
        job = read("/api/action", {"action": "unload_drawing_model"}, token)["job"]
    except urllib.error.HTTPError as exc:
        # Older releases have no unload action. The process check still rejects live children.
        if exc.code == 400 and json.load(exc).get("error") == "未知操作":
            return
        raise
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = read("/api/job?id=" + urllib.parse.quote(job))
        if state["status"] == "done":
            return
        if state["status"] not in ("running", "queued"):
            raise RuntimeError(state.get("message") or "未能释放工作室的绘图服务。")
        time.sleep(0.3)
    raise RuntimeError("工作室仍在释放绘图服务，请稍后再试。")


def refresh():
    port = json.loads((ROOT / "config/settings.json").read_text("utf-8-sig"))["port"]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = f"http://127.0.0.1:{int(port)}"

    def read(path, body=None, token=None):
        request = urllib.request.Request(base + path)
        if body is not None:
            request.data = json.dumps(body).encode()
            request.add_header("Content-Type", "application/json")
            request.add_header("X-Studio-Token", token)
        with opener.open(request, timeout=15) as response:
            return json.load(response)

    def session():
        info = read("/api/session")
        if Path(info["workspace"]).resolve() != ROOT:
            raise RuntimeError("该端口属于另一个工作室，已停止修复。")
        return info["token"]

    try:
        token = session()
    except urllib.error.URLError as exc:
        if not isinstance(exc.reason, ConnectionRefusedError):
            raise
        start(open_browser=False)
        token = session()
    if missing_catalog(read):
        # Validate the replacement before stopping an otherwise working server.
        from comic_workflow.wardrobe import preview
        if "rabbit ears" not in preview(PROBE)["text"]:
            raise RuntimeError("本机 V4 引擎自检失败。")
        check_idle(read)
        print("请先保存需要保留的临时预览图。继续后将释放工作室加载的模型并重启其空闲后台。", flush=True)
        if input("按 Enter 继续，或输入 q 退出：").strip():
            return
        if session() != token:
            raise RuntimeError("后台实例已改变，请重新打开修复入口。")
        release_studio_workers(read, token)
        check_idle(read)
        report("工作室空闲，正在更新后台。")
        script = ROOT / "scripts" / "restart_studio_listener.ps1"
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(script),
                        "-Port", str(port)], check=True, env={**os.environ, "LOCALDRAW_REPAIR_SESSION": token})
        start(open_browser=False)
        token = session()
    if missing_catalog(read):
        raise RuntimeError("新后台仍缺少 V4 词库接口。")
    result = read("/api/action", PROBE, token)
    if result.get("seed") != 8 or "rabbit ears" not in result.get("text", ""):
        raise RuntimeError("V4 角色预览验证失败。")
    report("V4 词库和阿米娅预览均已验证。关闭报错面板，重新打开「Anima 衣装台」即可。")
    os.startfile(ROOT / "LocalDraw.exe")


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["--check"]:
            from comic_workflow.wardrobe import catalog, preview
            result = preview(PROBE)
            if "rabbit ears" not in result["text"]:
                raise RuntimeError("本地 V4 预览自检失败。")
            print(f"本地 V4 引擎就绪：{len(catalog()['characters'])} 个角色，阿米娅预览成功。")
        elif sys.argv[1:]:
            raise ValueError("支持的检查参数为 --check。")
        else:
            refresh()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        report("修复未完成：" + str(exc))
        sys.exit(1)
