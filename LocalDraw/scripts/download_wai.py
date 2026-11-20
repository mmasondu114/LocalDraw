"""Download WAI v17 from a verified domestic mirror, resuming saved parts."""

import argparse
import contextlib
import datetime
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from comic_workflow.downloader import download
from download_comparison_models import LogTee


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connections", type=int, default=16)
    parser.add_argument("--proxy", help="Optional local HTTP proxy")
    parser.add_argument("--list", action="store_true", help="List the pinned file without downloading")
    args = parser.parse_args(argv)
    if not 1 <= args.connections <= 16:
        parser.error("connections must be between 1 and 16")
    cfg = json.loads((root / "config/wai-model.json").read_text(encoding="utf-8"))
    proxies = {}
    if args.proxy:
        proxy = urllib.parse.urlsplit(args.proxy)
        if (proxy.scheme != "http" or proxy.hostname not in ("127.0.0.1", "localhost")
                or not proxy.port or proxy.username or proxy.password):
            parser.error("代理须为本机 HTTP 地址，如 http://127.0.0.1:7897")
        proxies = {"http": args.proxy, "https": args.proxy}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
    print("模型：" + cfg["label"], flush=True)
    print(f"大小：{cfg['size'] / 1e9:.2f} GB / {cfg['size'] / 1024**3:.2f} GiB", flush=True)
    print("优先来源：魔搭 ModelScope 国内托管，已核对官方 SHA-256。", flush=True)
    print("网络：" + ("使用指定本机代理" if args.proxy else "直连国内源；不读取系统 HTTP 代理"), flush=True)
    print("关闭窗口或网络中断后，再打开同一个 BAT 即可续传。", flush=True)
    print("已完成和未完成的下载分块都会保留；校验通过后才显示模型已就绪。", flush=True)
    if args.list:
        print("保存到：" + str(root / cfg["directory"] / cfg["name"]), flush=True)
        return 0
    download(
        root / cfg["directory"], args.connections, cfg["url"], cfg["size"],
        cfg["sha256"], cfg["name"], chunk=cfg["chunk_size"],
        fallback_urls=cfg["fallback_urls"], timeout=45, opener=opener,
    )
    print("下载完成并已通过 SHA-256 校验。刷新工作室，在绘图模型中选择 " + cfg["name"], flush=True)
    return 0


def main(argv=None):
    folder = root / "data/logs"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    logfile = folder / ("download-wai-" + stamp + ".log")
    with logfile.open("a", encoding="utf-8") as log:
        with contextlib.redirect_stdout(LogTee(sys.stdout, log)), contextlib.redirect_stderr(LogTee(sys.stderr, log)):
            print("下载日志：" + str(logfile), flush=True)
            try:
                return run(argv)
            except KeyboardInterrupt:
                print("已停止，下载进度已保留；再次打开 BAT 可继续。", flush=True)
                return 130
            except Exception as exc:
                print("未完成：" + str(exc), flush=True)
                print("已下载数据保留。再次打开 BAT 可继续；若提示正在下载，请先关闭另一下载窗口。", flush=True)
                return 1


if __name__ == "__main__":
    raise SystemExit(main())
