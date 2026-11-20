"""User-operated, sequential model downloads with concurrent resumable chunks."""

import argparse
import contextlib
import datetime
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from comic_workflow.comparison_models import catalog
from comic_workflow.downloader import download


def run(argv=None, fetch=download):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="Only list files; no download")
    parser.add_argument("--model", help="Optional catalog ID; default: all three")
    parser.add_argument("--connections", type=int, default=12)
    parser.add_argument("--proxy", help="Local HTTP proxy, e.g. http://127.0.0.1:7890")
    parser.add_argument("--ask-proxy", action="store_true")
    parser.add_argument("--direct", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.connections <= 16:
        parser.error("connections must be between 1 and 16")
    if args.ask_proxy and not args.list:
        print("请在 VPN 软件中查看 HTTP 或 Mixed（混合）代理端口，不能填写 SOCKS 专用端口。", flush=True)
        port = input("输入代理端口数字（直接回车取消）：").strip()
        if not port:
            print("已取消，未开始下载。", flush=True)
            return 130
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            parser.error("代理端口须为 1–65535 的数字")
        args.proxy = "http://127.0.0.1:" + port
    opener = None
    if args.proxy:
        proxy = urllib.parse.urlsplit(args.proxy)
        if proxy.scheme != "http" or proxy.hostname not in ("127.0.0.1", "localhost") or not proxy.port or proxy.username or proxy.password:
            parser.error("仅支持本机 HTTP 代理：http://127.0.0.1:端口")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": args.proxy, "https": args.proxy}))
        print(f"网络：显式使用本机 HTTP 代理 {proxy.hostname}:{proxy.port}", flush=True)
    elif args.direct:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        print("网络：直连（不读取系统代理；VPN 的 TUN / 全局隧道仍可能生效）", flush=True)
    else:
        proxy_found = any(key in urllib.request.getproxies() for key in ("http", "https"))
        print("网络：" + ("跟随检测到的系统 / 环境代理" if proxy_found else "未检测到 HTTP 代理；若 VPN 只提供本机代理端口，请使用 11-download-models-with-proxy.bat"), flush=True)
    cfg = catalog()
    models = [m for m in cfg["models"] if not args.model or args.model == m["id"]]
    if not models:
        parser.error("Unknown model ID")
    total = sum(m["size"] for m in models)
    print(f"权重合计：{total / 1e9:.2f} GB / {total / 1024**3:.2f} GiB", flush=True)
    print(f"每个模型 {args.connections} 路下载；连接等待 60 秒。原站失败自动切换镜像；完整块和未完成块均可续传。", flush=True)
    failed = []
    for index, m in enumerate(models, 1):
        print(f"\n========== 模型 {index}/{len(models)}：{m['label']} ==========", flush=True)
        print(f"文件：{m['name']} | 大小：{m['size'] / 1024**3:.2f} GiB", flush=True)
        if args.list:
            continue
        url = f"https://huggingface.co/{m['repo']}/resolve/{m['revision']}/{m['name']}"
        try:
            fetch(root / cfg["directory"], args.connections, url, m["size"], m["sha256"], m["name"],
                  fallback_urls=[url.replace("https://huggingface.co/", "https://hf-mirror.com/", 1)],
                  timeout=60, opener=opener)
        except KeyboardInterrupt:
            print("已停止，下载分块保留。重开同一个 BAT 可继续。", flush=True)
            return 130
        except Exception as exc:
            failed.append(m["label"])
            print(f"下载失败：{exc}。继续处理下一个模型。", flush=True)
    if failed:
        print("以下模型未完成，重开 BAT 可续传：" + ", ".join(failed), flush=True)
        return 1
    if not args.list:
        print("所有指定模型下载完成，并已通过 SHA-256 校验。刷新绘图网页即可试画。", flush=True)
    return 0


class LogTee:
    def __init__(self, console, log):
        self.console, self.log = console, log

    def write(self, text):
        self.console.write(text)
        self.log.write(text)
        self.log.flush()
        return len(text)

    def flush(self):
        self.console.flush()
        self.log.flush()


def main(argv=None, fetch=download):
    folder = root / "data/logs"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    logfile = folder / f"download-models-{stamp}-{os.getpid()}.log"
    with logfile.open("a", encoding="utf-8") as log:
        with contextlib.redirect_stdout(LogTee(sys.stdout, log)), contextlib.redirect_stderr(LogTee(sys.stderr, log)):
            print("本次下载日志：" + str(logfile), flush=True)
            try:
                return run(argv, fetch)
            except KeyboardInterrupt:
                print("已停止，分块保留。", flush=True)
                return 130


if __name__ == "__main__":
    raise SystemExit(main())
