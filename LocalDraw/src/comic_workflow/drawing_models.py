"""Manually prewarm the same checkpoint loader used by normal drawings."""
import time

from . import comfyui, engine as e, prompt_models
from .installed_assets import models
from .paths import ROOT
from .task_control import checkpoint as check_task


def installed():
    directory = models(ROOT) / "checkpoints"
    return prompt_models.installed() + sorted(str(p.relative_to(directory)) for p in directory.rglob("*.safetensors"))


def snapshot(app):
    with app.jobs.lock:
        active = next(iter(app.jobs.controls.values()), None)
        busy = active["job"]["message"] if active else None
    return {"models": [{"value": name, "label": prompt_models.MODELS[name][0] if name in prompt_models.MODELS else name}
                       for name in installed()],
            "loaded": app.comfy.preloaded, "default": comfyui.config()["checkpoint"],
            "busy": busy}


def require_idle(manager):
    if manager.process is not None and manager.process.poll() is None:
        queue = comfyui.api("/queue")
        e.need(not queue.get("queue_running") and not queue.get("queue_pending"), "请等绘图队列完成后再操作")


def unload(app):
    require_idle(app.comfy)
    app.comfy.shutdown()


def preload(app, name, update, jid):
    e.need(name in installed(), "请选择已安装的绘图模型")
    manager = app.comfy
    require_idle(manager)
    if manager.preloaded == name:
        return
    if manager.process is not None:
        manager.shutdown()
    app.model.stop()
    update("正在启动绘图服务")
    try:
        # Dynamic VRAM stages file-backed weights until the first forward pass.
        # Manual preload uses Comfy's budgeted loader to transfer them immediately.
        manager.start({"preload_weights": True})
        e.need(manager.process is not None and manager.process.poll() is None, "请选择本工作室启动的绘图服务")
        update("正在加载 " + name)
        graph = {"1": prompt_models.base_loader(name),
                 "warm": {"class_type": "LocalDrawWarmModel", "inputs": {"model": ["1", 0]}}}
        while not manager.preloaded:
            submitted = comfyui.api("/prompt", {"prompt": graph, "client_id": jid})
            wait_for_load(submitted["prompt_id"])
            manager.process.preloaded_checkpoint = name
            check_task()  # Pausing frees weights. Warm them again when resumed.
    except BaseException:
        manager.shutdown()
        raise


def wait_for_load(prompt_id):
    deadline = time.monotonic() + comfyui.config()["timeout_seconds"]
    while time.monotonic() < deadline:
        record = comfyui.api("/history/" + prompt_id).get(prompt_id)
        if record:
            errors = [data.get("exception_message") for kind, data in record["status"]["messages"]
                      if kind == "execution_error"]
            e.need(record["status"]["status_str"] == "success" and
                   record["outputs"].get("warm", {}).get("loaded") == [True],
                   next((error for error in errors if error), "绘图模型加载失败，请检查绘图日志"))
            return
        time.sleep(0.5)
    raise ValueError("绘图模型加载超时")
