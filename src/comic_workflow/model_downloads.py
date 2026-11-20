"""Download and locate only the assets listed in the shipped manifest."""
import os
import threading

from . import downloader, engine
from .installed_assets import models
from .paths import ROOT


class ModelDownloads:
    def __init__(self, root=ROOT):
        self.folder = models(root).resolve()
        self.assets = {r["id"]: r for r in engine.read(root / "config/model-downloads.json")}
        self.cache_path = root / "data/model-download-checks.json"
        try:
            self.checked = engine.read(self.cache_path)
        except (OSError, ValueError):
            self.checked = {}
        self.lock = threading.RLock()
        self.active = {}
        self.errors = {}

    def path(self, key):
        engine.need(isinstance(key, str) and key in self.assets, "未知模型")
        item = self.assets[key]
        return self.folder / item["folder"] / item["name"]

    def open_folder(self, key):
        directory = self.path(key).parent
        directory.mkdir(parents=True, exist_ok=True)
        os.startfile(str(directory))
        return {"opened": str(directory)}

    def signature(self, path, asset):
        try:
            s = path.stat()
        except FileNotFoundError:
            return None
        return [str(path), s.st_size, s.st_mtime_ns, s.st_ctime_ns, asset["sha256"]]

    def snapshot(self):
        rows = []
        with self.lock:
            for key, asset in self.assets.items():
                path = self.path(key)
                sig = self.signature(path, asset)
                record = self.checked.get(key, {})
                complete = sig is not None and sig == record.get("file") and record.get("valid") is True
                state, done = ("已就绪", asset["size"]) if complete else ("待下载", 0)
                if sig and not complete:
                    if sig[1] != asset["size"]:
                        state = "文件大小不符"
                    elif sig == record.get("file"):
                        state = "文件校验失败"
                    elif key not in self.active:
                        self.start(key, verify=True)
                if not sig and path.with_name(path.name + ".parts").is_dir():
                    state = "可续传"
                state, done = self.active.get(key, (state, done))
                rows.append(dict(id=key, label=asset["label"], use=asset["use"], name=asset["name"],
                                 path=str(path), size=asset["size"], done=done, state=state,
                                 ready=complete, busy=key in self.active, error=self.errors.get(key, "") if not complete else ""))
        return {"items": rows}

    def start(self, key, verify=False):
        path = self.path(key)
        asset = self.assets[key]
        with self.lock:
            if key in self.active:
                return {"started": False}
            self.errors.pop(key, None)
            self.active[key] = ("正在校验" if verify else "正在连接", 0)

        def progress(state, done):
            with self.lock:
                self.active[key] = (state, done)

        def run():
            try:
                if verify:
                    before = self.signature(path, asset)
                    sha = downloader.filehash(path, lambda done, total: progress("正在校验", done))
                    valid = sha == asset["sha256"]
                    if before != self.signature(path, asset):
                        return
                else:
                    downloader.download(path.parent, 4, asset["url"], asset["size"], asset["sha256"], path.name,
                                        fallback_urls=[asset["url"].replace("https://huggingface.co/", "https://hf-mirror.com/")],
                                        timeout=30, on_progress=progress)
                    valid = True
                    before = self.signature(path, asset)
                with self.lock:
                    self.checked[key] = {"file": before, "valid": valid}
                    engine.save(self.cache_path, self.checked)
            except Exception as exc:
                with self.lock:
                    self.errors[key] = str(exc)
            finally:
                with self.lock:
                    self.active.pop(key, None)
        threading.Thread(target=run, daemon=True, name="model-" + key).start()
        return {"started": True}
