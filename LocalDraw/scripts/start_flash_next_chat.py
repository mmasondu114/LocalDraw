"""Open the installed Strata chat UI for Qwen3.8 Flash Next."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from comic_workflow.processes import attach_exit_job, close_job, terminate_tree

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def read_health(url):
    try:
        with OPENER.open(url + "/health", timeout=2) as response:
            return json.load(response)
    except (OSError, ValueError):
        return {}


def main():
    parser = argparse.ArgumentParser(description="打开 Qwen3.8 Flash Next 本地聊天框")
    parser.add_argument("--check", action="store_true", help="检查文件和配置，不加载模型")
    args = parser.parse_args()
    cfg = json.loads((ROOT / "config/settings.json").read_text(encoding="utf-8-sig"))["prompt_model"]
    profile = ROOT / cfg["strata_profile"]
    run = json.loads(profile.read_text(encoding="utf-8-sig"))
    for value in (cfg["strata_python"], cfg["strata_cli"], run["exe"]):
        if not (ROOT / value).is_file():
            raise RuntimeError(f"运行文件缺失：{value}")
    for item in cfg["model_files"]:
        path = ROOT / item["path"]
        if not path.is_file() or path.stat().st_size != item["size"]:
            raise RuntimeError(f"模型分片缺失或大小不符：{path}")
    if run.get("model_name") != cfg["model_name"] or "flash-next" not in cfg["model_name"]:
        raise RuntimeError("配置没有指向 Qwen3.8 Flash Next，请检查 prompt_model 配置。")
    url = f'http://127.0.0.1:{cfg["model_port"]}'
    command = [str(ROOT / cfg["strata_python"]), str(ROOT / cfg["strata_cli"]),
               "serve", "--profile", str(profile), "--offline", "--host", "127.0.0.1",
               "--port", str(cfg["model_port"]), "--parallel", "1", "--shutdown-timeout", "5"]
    env = {**os.environ, "STRATA_RESIDENT_HEADROOM_GIB": "5", "PYTHONIOENCODING": "utf-8"}
    if args.check:
        subprocess.run(command + ["--plan"], cwd=ROOT, env=env, check=True)
        print(f"\n启动文件检查通过：{cfg['model_name']}\n聊天地址：{url}\n未加载模型。")
        return 0

    def ready():
        health = read_health(url)
        return (health.get("service") == "strata" and health.get("loaded") is True
                and health.get("model") == cfg["model_name"]
                and health.get("max_context") == cfg["context_size"])

    if ready():
        webbrowser.open(url)
        print(f"已打开正在运行的 Flash Next 聊天网页：{url}")
        return 0
    with socket.socket() as sock:
        sock.settimeout(1)
        if sock.connect_ex(("127.0.0.1", cfg["model_port"])) == 0:
            raise RuntimeError("8094 端口正在使用，或模型尚在加载。请等待原窗口完成加载后再双击。")
    if os.name == "nt":
        class Memory(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in
                ("total", "available", "page_total", "page_available", "virtual_total", "virtual_available", "extended")]
        memory = Memory()
        memory.length = ctypes.sizeof(memory)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            free = memory.available / 1024**3
            if free < cfg.get("min_free_ram_gib", 65):
                raise RuntimeError(f"空闲内存仅 {free:.1f} GiB，请先关闭其他大模型；此配置需要至少 65 GiB。")
    gpu = subprocess.run(["nvidia-smi", "--id=0", "--query-gpu=memory.free",
                          "--format=csv,noheader,nounits"], capture_output=True, text=True,
                         timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if gpu.returncode:
        raise RuntimeError("无法读取 NVIDIA 显卡状态，请检查驱动。")
    free_vram_mib = int(gpu.stdout.strip().splitlines()[0])
    print(f"当前空闲显存 {free_vram_mib / 1024:.1f} GiB；由 Strata 自动分配专家缓存并保留配置的显存余量。", flush=True)
    log_path = ROOT / "data/logs/flash-next-chat.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    process = job = None
    print("正在加载 Qwen3.8 Flash Next（128K / MTP），就绪后自动打开聊天网页。", flush=True)
    print(f"日志：{log_path}\n请保留本窗口；关闭窗口或按 Ctrl+C 会停止本次启动的模型。", flush=True)
    try:
        with log_path.open("a", encoding="utf-8") as log:
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            job = attach_exit_job(process)
            deadline = time.monotonic() + 600
            while not ready():
                if process.poll() is not None:
                    raise RuntimeError(f"模型启动失败，详情见：{log_path}")
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"模型加载超时，详情见：{log_path}")
                time.sleep(1)
            webbrowser.open(url)
            print(f"聊天框已就绪：{url}", flush=True)
            while process.poll() is None:
                time.sleep(0.5)
            if process.returncode:
                raise RuntimeError(f"模型服务已退出，详情见：{log_path}")
    except KeyboardInterrupt:
        print("\n正在关闭本次启动的 Flash Next…", flush=True)
    finally:
        terminate_tree(process)
        close_job(job)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"\n{exc}", file=sys.stderr)
        raise SystemExit(1)
