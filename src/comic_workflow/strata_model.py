"""Owned Strata server with independent requests and resident model weights."""

import os
import subprocess
import time

from . import engine as e
from .paths import DATA, ROOT, local_path
from .processes import attach_exit_job, close_job, terminate_tree


class StrataManager:
    def __init__(self, cfg):
        self.cfg = dict(cfg)
        self.process = self.job = self.log = None
        self.isolated_context = False
        self.message = "Qwen3.8 Flash Next 提示词模型未启动"

    def status(self):
        from .model import request_json, endpoint, GENERATION_LOCK
        available = all(local_path(f["path"]).is_file() and
                        local_path(f["path"]).stat().st_size == f["size"]
                        for f in self.cfg["model_files"])
        try:
            health = request_json(endpoint(self.cfg) + "/health", timeout=1)
            ready = (health.get("service") == "strata" and health.get("loaded") is True
                     and health.get("model") == self.cfg["model_name"]
                     and health.get("max_context") == self.cfg["context_size"])
        except Exception:
            ready = False
        return {"file_size_ok": available, "ready": ready,
                "owned": self.process is not None and self.process.poll() is None,
                "message": self.message, "generating": GENERATION_LOCK.locked(),
                "model_path": self.cfg["model_files"][0]["path"],
                "model_name": self.cfg["model_name"], "context_size": self.cfg["context_size"],
                "vision_ready": False}

    def start(self, update):
        from .model import request_json, endpoint
        state = self.status()
        if state["ready"] and state["owned"]:
            request_json(endpoint(self.cfg) + "/v1/vram", {"reserve_mib": None}, timeout=60)
            return
        e.need(not state["owned"], "Strata 尚在加载，请等待完成")
        try:
            request_json(endpoint(self.cfg) + "/health", timeout=1)
        except Exception:
            pass
        else:
            raise ValueError("Strata 已在独立窗口运行，请先关闭该窗口，再由工作室启动提示词模型")
        e.need(state["file_size_ok"], "Flash Next 模型分片缺失或大小不正确")
        if os.name == "nt":
            import ctypes
            class Memory(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                    (name, ctypes.c_ulonglong) for name in
                    ("total", "available", "page_total", "page_available", "virtual_total", "virtual_available", "extended")]
            memory = Memory()
            memory.length = ctypes.sizeof(memory)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
                free = memory.available / 1024**3
                required = self.cfg.get("min_free_ram_gib", 65)
                e.need(free >= required, f"可用内存 {free:.1f} GiB，启动 {self.cfg['context_size'] // 1024}K 提示词模型需要约 {required} GiB；请先关闭其他大模型")
        profile = local_path(self.cfg["strata_profile"])
        run = e.read(profile)
        for path in (run["exe"], self.cfg["strata_python"], self.cfg["strata_cli"]):
            e.need(local_path(path).is_file(), "Strata 运行文件缺失：" + str(path))
        # Strata has no erase endpoint. Disable all cross-request checkpoints:
        # every request reads its own complete prompt, without unloading weights.
        for key in ("args", "args_written"):
            args = list(run.get(key, run["args"]))
            for flag in ("--prompt-cache", "--conversation-cache-mib"):
                if flag in args:
                    args[args.index(flag) + 1] = "0"
                else:
                    args += [flag, "0"]
            run[key] = args
        run["vram_elastic"] = True
        profile = DATA / "strata" / f"studio-{self.cfg['context_size']}.json"
        e.save(profile, run)
        args = [str(local_path(self.cfg["strata_python"])), str(local_path(self.cfg["strata_cli"])),
                "serve", "--profile", str(profile), "--offline", "--host", "127.0.0.1",
                "--port", str(self.cfg["model_port"]), "--parallel", "1", "--shutdown-timeout", "5"]
        folder = DATA / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        self.log = (folder / "strata-prompt-server.log").open("a", encoding="utf-8")
        e.save(folder / "strata-prompt-command.json", args)
        acceleration = "MTP 加速" if run.get("mtp") else "标准解码"
        self.message = f"正在加载 Qwen3.8 Flash Next（{acceleration}）"
        update(self.message)
        try:
            self.process = subprocess.Popen(args, cwd=ROOT, stdin=subprocess.DEVNULL,
                stdout=self.log, stderr=subprocess.STDOUT,
                env={**os.environ, "STRATA_RESIDENT_HEADROOM_GIB": "5", "PYTHONIOENCODING": "utf-8"},
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.job = attach_exit_job(self.process)
            self.isolated_context = True
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                e.need(self.process.poll() is None, "Strata 启动失败，请查看 data/logs/strata-prompt-server.log")
                if self.status()["ready"]:
                    # Pause at a completed load boundary: resume may replace the
                    # manager, so do not continue polling the old process afterward.
                    from .task_control import checkpoint
                    checkpoint()
                    self.message = f"Qwen3.8 Flash Next 已就绪 · {self.cfg['context_size'] // 1024}K · {acceleration}"
                    update(self.message)
                    return
                time.sleep(1)
            raise ValueError("Strata 加载超时，已停止本次启动")
        except BaseException:
            self.stop()
            raise

    def clear_context(self):
        # No HTTP erase is supported by this Strata version. No conversation
        # checkpoints are retained/reused in our startup mode; allocated KV
        # working buffers stay with the loaded engine (not a secure memory wipe).
        if self.process is None or self.process.poll() is not None:
            return {"status": "cleared", "method": "already_unloaded"}
        if self.isolated_context:
            return {"status": "cleared", "method": "independent_requests", "weights_loaded": True}
        return {"status": "unsupported", "reason": "当前服务未启用独立上下文模式；模型保持加载"}

    def reserve_for_drawing(self):
        from .model import request_json, endpoint
        if self.process is not None and self.process.poll() is None:
            result = request_json(endpoint(self.cfg) + "/v1/vram",
                                  {"reserve_mib": 12288}, timeout=60)
            e.need(result.get("status") == "ok", "FlashNext 尚未让出绘图显存，已保留模型；请稍后重试")

    def stop(self):
        from .model import request_json, endpoint
        if self.process is not None and self.process.poll() is None:
            # Let Strata release its pinned RAM and CUDA buffers before ending the wrapper.
            try:
                request_json(endpoint(self.cfg) + "/unload?wait=5", {}, timeout=30)
            except Exception:
                pass
        terminate_tree(self.process)
        close_job(self.job)
        self.process = self.job = None
        self.isolated_context = False
        if self.log:
            self.log.close()
            self.log = None
        self.message = "Qwen3.8 Flash Next 已卸载，可继续绘图"
