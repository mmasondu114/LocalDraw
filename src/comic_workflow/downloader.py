"""Six-way, resumable download using Python standard library; no pip required."""

import argparse, concurrent.futures, hashlib, pathlib, shutil, sys, time, urllib.request
import threading
import urllib.parse

NAME = "Qwen3.6-27B-Q4_K_M.gguf"
URL = "https://huggingface.co/unsloth/Qwen3.6-27B-GGUF/resolve/main/" + NAME
SIZE = 16817244384
SHA = "5ed60d0af4650a854b1755bd392f9aef4872643dc25a254bc68043fa638392a0"
CHUNK = 16 * 1024 * 1024
DEFAULT = pathlib.Path(__file__).resolve().parents[2] / "models"


def duration(seconds):
    if seconds is None:
        return "等待测速"
    seconds = max(0, int(seconds))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours}小时{minutes:02}分" if hours else f"{minutes}分{seconds:02}秒"


class ConsoleProgress:
    """Byte-based progress. Rate excludes resumed bytes and uses the last sample."""

    def __init__(self, label, total, initial=0, clock=time.monotonic):
        self.label, self.total, self.initial = label, total, initial
        self.clock = clock
        self.last_time = clock()
        self.last_bytes = 0

    def show(self, done, total=None, *, transferred=None, note="", force=False):
        total = self.total if total is None else total
        now = self.clock()
        elapsed = now - self.last_time
        if not force and elapsed < 1:
            return
        transferred = max(0, done - self.initial) if transferred is None else transferred
        speed = max(0, transferred - self.last_bytes) / elapsed if elapsed > 0 else 0
        fraction = min(1, max(0, done / total)) if total else 1
        bars = int(fraction * 20)
        eta = 0 if done >= total else (total - done) / speed if speed > 0 else None
        print(
            f"{self.label} [{'#' * bars}{'-' * (20 - bars)}] {fraction * 100:5.1f}% | "
            f"{done / 1024**3:.3f}/{total / 1024**3:.3f} GiB | "
            f"{speed / 1024**2:.2f} MiB/s | 剩余约 {duration(eta)}"
            + (f" | {note}" if note else ""),
            flush=True,
        )
        self.last_time, self.last_bytes = now, transferred


def filehash(p, progress=None):
    h = hashlib.sha256()
    done, last = 0, 0
    total = p.stat().st_size
    with p.open("rb") as f:
        while block := f.read(8 * 1024 * 1024):
            h.update(block)
            done += len(block)
            now = time.monotonic()
            if progress and (now - last >= 0.25 or done == total):
                progress(done, total)
                last = now
    return h.hexdigest()


def _download(folder, workers, url=URL, size=SIZE, sha=SHA, name=NAME, chunk=CHUNK,
              *, fallback_urls=(), timeout=60, opener=None):
    folder = pathlib.Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    parts = folder / (name + ".parts")
    parts.mkdir(exist_ok=True)
    print(f"\n模型：{name}\n保存到：{target}", flush=True)
    if target.exists() and target.stat().st_size == size:
        verify = ConsoleProgress("校验已有文件", size)
        verify.show(0, force=True)
        actual_sha = filehash(target, verify.show)
        verify.show(size, force=True)
        if actual_sha == sha:
            print("已完整下载，SHA-256 校验通过，跳过此模型。", flush=True)
            return target
        print("已有同名文件未通过校验；将使用下载分块重新获取。", flush=True)
    count = (size + chunk - 1) // chunk

    def good(i):
        p = parts / str(i)
        return p.exists() and p.stat().st_size == min(chunk, size - i * chunk)

    missing = [i for i in range(count) if not good(i)]
    def partial_size(i):
        tmp = parts / (str(i) + ".partial")
        n = tmp.stat().st_size if tmp.exists() else 0
        return n if 0 <= n <= min(chunk, size - i * chunk) else 0

    resumed = {i: partial_size(i) for i in missing}
    remaining = sum(min(chunk, size - i * chunk) for i in missing)
    # Parts plus assembly coexist until final SHA verification.
    required = remaining + size + 1024**3
    if shutil.disk_usage(folder).free < required:
        raise RuntimeError(
            f"Need approximately {required / 1024**3:.1f} GiB free for parts and assembly."
        )
    print(
        f"并行连接：{workers} 路 | 断点续传：已有 {count - len(missing)}/{count} 个完整分块\n"
        "进度按实际字节更新，速度使用 MiB/s；连接等待或重试不会虚增进度。",
        flush=True,
    )
    mutex = threading.Lock()
    stopped = threading.Event()
    active = dict(resumed)
    states = {}
    notices = []
    completed_bytes = size - remaining
    transferred = 0
    last_activity = time.monotonic()
    progress = ConsoleProgress("下载", size, initial=completed_bytes)
    sources = [url, *fallback_urls]
    open_url = opener.open if opener is not None else urllib.request.urlopen

    def show_download(force=False):
        with mutex:
            done_bytes = completed_bytes + sum(active.values())
            network_bytes = transferred
            idle = time.monotonic() - last_activity
            connecting = sum(state == "connecting" for state in states.values())
            reading = sum(state == "reading" for state in states.values())
            retrying = sum(state == "retrying" for state in states.values())
            messages = list(notices)
            notices.clear()
        for message in messages:
            print(message, flush=True)
        note = f"接收 {reading} / 连接 {connecting} / 重试 {retrying}"
        if idle >= 3 and done_bytes < size:
            note += f"；{idle:.0f}秒无新数据，等待服务器响应"
        progress.show(done_bytes, transferred=network_bytes, note=note, force=force)

    def get(i):
        nonlocal completed_bytes, transferred, last_activity
        start = i * chunk
        end = min(size, start + chunk) - 1
        p = parts / str(i)
        tmp = parts / (str(i) + ".partial")
        for attempt in range(10):
            if stopped.is_set():
                return
            offset = partial_size(i)
            with mutex:
                active[i] = offset
                states[i] = "connecting"
            try:
                source = sources[attempt % len(sources)]
                if offset < end - start + 1:
                    sep = "&" if "?" in source else "?"
                    req = urllib.request.Request(
                        source + sep + f"part={i}&offset={offset}",
                        headers={
                            "Range": f"bytes={start + offset}-{end}",
                            "User-Agent": "ComicWorkflow/1.0",
                        },
                    )
                    with open_url(req, timeout=timeout) as r:
                        expected = f"bytes {start + offset}-{end}/{size}"
                        if r.status != 206 or r.headers.get("Content-Range") != expected:
                            raise RuntimeError(
                                f"服务器未返回正确分段：HTTP {r.status}, Range={r.headers.get('Content-Range')}"
                            )
                        with mutex:
                            states[i] = "reading"
                        with tmp.open("ab" if offset else "wb") as f:
                            received = offset
                            while block := r.read(256 * 1024):
                                if stopped.is_set():
                                    return
                                received += len(block)
                                if received > end - start + 1:
                                    raise RuntimeError("Oversized response")
                                f.write(block)
                                with mutex:
                                    active[i] = received
                                    transferred += len(block)
                                    last_activity = time.monotonic()
                if tmp.stat().st_size != end - start + 1:
                    raise RuntimeError("Incomplete part")
                tmp.replace(p)
                with mutex:
                    completed_bytes += end - start + 1
                    active.pop(i, None)
                    states.pop(i, None)
                return
            except Exception as e:
                with mutex:
                    active[i] = partial_size(i)
                    states[i] = "retrying"
                    host = urllib.parse.urlsplit(source).hostname
                    next_host = urllib.parse.urlsplit(sources[(attempt + 1) % len(sources)]).hostname
                    notices.append(f"分块 {i + 1}/{count} 第 {attempt + 1} 次失败 [{host}]：{e}" +
                                   (f"；自动重试：{next_host}，从已保存字节续传" if attempt < 9 else "；已达到重试上限"))
                if attempt == 9:
                    raise RuntimeError(f"Part {i} failed: {e}") from e
                stopped.wait(min(1 + attempt, 5))

    show_download(force=True)
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {pool.submit(get, i): i for i in missing}
        pending = set(futures)
        while pending:
            finished, pending = concurrent.futures.wait(
                pending, timeout=1, return_when=concurrent.futures.FIRST_COMPLETED
            )
            show_download()
            for future in finished:
                future.result()
        show_download(force=True)
    except BaseException:
        stopped.set()
        print("正在结束连接，已完成的分块会保留……", flush=True)
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    assembling = folder / (name + ".assembling")
    h = hashlib.sha256()
    print("下载数据接收完毕。正在合并文件并核对 SHA-256，校验通过后才算完成。", flush=True)
    assembly = ConsoleProgress("合并并校验", size)
    assembled = 0
    assembly.show(0, force=True)
    with assembling.open("wb") as out:
        for i in range(count):
            with (parts / str(i)).open("rb") as f:
                while block := f.read(8 * 1024 * 1024):
                    out.write(block)
                    h.update(block)
                    assembled += len(block)
                    assembly.show(assembled)
    assembly.show(assembled, force=True)
    if assembling.stat().st_size != size or h.hexdigest() != sha:
        raise RuntimeError(
            "SHA-256 mismatch. No model marked ready. Retain logs and check source/parts before retrying."
        )
    assembling.replace(target)
    for i in range(count):
        (parts / str(i)).unlink()
    for p in parts.glob("*.partial"):
        p.unlink()
    parts.rmdir()
    print("完成：SHA-256 校验通过。模型已就绪：" + str(target), flush=True)
    return target


def download(folder, workers, url=URL, size=SIZE, sha=SHA, name=NAME, chunk=CHUNK,
             *, fallback_urls=(), timeout=60, opener=None):
    """An OS lock prevents double-clicking twice from corrupting shared part files."""
    folder = pathlib.Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / (name + ".download.lock")).open("a+b") as lock:
        lock.seek(0)
        lock.write(b"0")
        lock.flush()
        lock.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError(
                "This model is already downloading in another window."
            ) from exc
        return _download(folder, workers, url, size, sha, name, chunk,
                         fallback_urls=fallback_urls, timeout=timeout, opener=opener)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--directory", type=pathlib.Path, default=DEFAULT)
    p.add_argument("--connections", type=int, default=6)
    p.add_argument("--verify-only", action="store_true")
    args = p.parse_args()
    if not 1 <= args.connections <= 16:
        p.error("connections must be 1..16")
    if args.verify_only:
        target = args.directory / NAME
        if (
            not target.exists()
            or target.stat().st_size != SIZE
            or filehash(target) != SHA
        ):
            raise RuntimeError("Model missing, incomplete, or SHA-256 mismatch")
        print("SHA-256 verified.")
        return
    download(args.directory, args.connections)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped. Completed parts are retained.")
        sys.exit(130)
    except Exception as e:
        print("\nERROR: " + str(e) + "\nRun the BAT again to resume.", file=sys.stderr)
        sys.exit(1)
