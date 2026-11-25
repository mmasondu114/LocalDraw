"""Durable, explicitly started overnight drawing queue."""
import copy
import threading
import time
import uuid

from . import engine as e
from .model import GENERATION_LOCK
from .prompt_models import MODELS


class NightQueue:
    def __init__(self, app):
        self.app = app
        self.path = app.repo.root.parent / "night-queue.json"
        self.lock = threading.RLock()
        self.state = e.read(self.path) if self.path.exists() else {
            "items": [], "running": False, "stop_on_error": True}
        # A restart never silently restarts GPU work or repeats an interrupted job.
        self.state["running"] = False
        for item in self.state["items"]:
            if item["status"] == "running":
                item.update(status="interrupted", message="工作室重启，已有结果保留，请核对后重新加入任务")
                try:
                    job = app.jobs.get(item["job"])
                except (KeyError, FileNotFoundError, ValueError):
                    continue
                if job["status"] in {"running", "paused", "pausing", "cancelling", "resumed"}:
                    item.update(status="running", message="已有任务已恢复记录，请在工作台继续或结束")
                elif job["status"] in {"done", "failed", "cancelled", "interrupted"}:
                    self.settle(item, job)
        self.stop_event = threading.Event()
        self.worker = None
        if any(i["status"] == "running" for i in self.state["items"]):
            # Observe an explicitly resumed job without starting waiting items.
            self.worker = threading.Thread(target=self.loop, daemon=True)
            self.worker.start()

    @staticmethod
    def settle(item, job):
        result = job.get("result") or {}
        errors = result.get("errors") if isinstance(result, dict) else None
        item.update(status="partial" if job["status"] == "done" and errors else job["status"],
                    message=job.get("message", ""), finished=e.stamp())

    def save(self):
        e.save(self.path, self.state)

    def snapshot(self):
        with self.lock:
            result = copy.deepcopy(self.state)
            result["can_rename"] = True
            result["can_recover_interrupted"] = True
            for item in result["items"]:
                item["request"] = {key: value for key, value in item["request"].items()
                                   if key in {"action", "checkpoint", "targets", "candidate_count"}}
                model = item["request"].get("checkpoint")
                if model:
                    item["request"]["checkpoint_label"] = MODELS.get(model, (model,))[0]
                if item.get("job") and item["status"] == "running":
                    try:
                        job = self.app.jobs.get(item["job"])
                    except (FileNotFoundError, ValueError):
                        item.update(job_status="interrupted", message="任务记录暂时无法读取，已有作品保留，请核对工作台")
                        continue
                    item.update(message=job.get("message", ""), progress=job.get("progress"),
                                job_status=job["status"], eta=job.get("eta"))
            return result

    def add(self, pid, label, request):
        with self.lock:
            self.ensure_capacity()
            item = {"id": uuid.uuid4().hex, "project": pid, "label": label,
                    "request": copy.deepcopy(request), "status": "waiting", "created": e.stamp()}
            self.state["items"].append(item)
            try:
                self.save()
            except Exception:
                self.state["items"].remove(item)
                raise
            return {"queued": item["id"], "project": pid}

    def ensure_capacity(self):
        e.need(sum(i["status"] == "waiting" for i in self.state["items"]) < 100,
               "等待列表最多 100 项，请先完成或移除一些任务")

    @staticmethod
    def custom_label(value):
        if value is None:
            return None
        e.need(isinstance(value, str), "任务名称须为文字")
        label = value.strip()
        e.need(len(label) <= 80 and not any(ord(c) < 32 for c in label),
               "任务名称最多 80 个字符，请使用单行文字")
        return label or None

    def preview(self, item_id):
        with self.lock:
            item = next((i for i in self.state["items"] if i["id"] == item_id), None)
            e.need(item, "这项任务已移出列表")
            item = copy.deepcopy(item)
        request = item["request"]
        if request["action"] == "prompt_batch":
            from .prompt_board import sample_input
            project = sample_input(self.app.repo, item["project"], request)
            spec = project["prompt_input"]
            payload = project["drawing_prompts"]["P001-K01"]["payload"]
            rows = [{"panel_id": project.get("board_target", "单幅出图"), "positive": payload["positive"],
                     "negative": payload["negative"], "loras": spec.get("loras", []),
                     "options": {key: spec.get(key) for key in ("aspect_ratio", "consistency_lora", "seed")}}]
            checkpoint, count = spec["checkpoint"], spec["count"]
        else:
            from .prompt_exchange import effective_negative, effective_positive, load_import
            bundle = load_import(self.app.repo, item["project"], request["bundle"])
            rows = [{"panel_id": row["panel_id"], "positive": effective_positive(row, request),
                     "negative": effective_negative(row, request),
                     "loras": request.get("loras_by_panel", {}).get(row["panel_id"], []),
                     "has_reference": row["panel_id"] in request.get("references_by_panel", {})}
                    for row in bundle["panels"] if row["panel_id"] in request["targets"]]
            checkpoint, count = request["checkpoint"], request["candidate_count"]
        # Reference images and internal request data stay out of the response.
        return {"label": item["label"], "checkpoint": checkpoint, "checkpoint_label": MODELS.get(checkpoint, (checkpoint,))[0],
                "count": count, "panels": rows}

    def control(self, request):
        with self.lock:
            previous = dict(self.state)
            previous_items = [(item, dict(item)) for item in self.state["items"]]
            try:
                return self.apply_control(request)
            except Exception:
                # Preserve request objects shared with an active worker.
                for item, values in previous_items:
                    item.clear()
                    item.update(values)
                self.state.clear()
                self.state.update(previous)
                self.state["items"] = [item for item, _ in previous_items]
                raise

    def apply_control(self, request):
        with self.lock:
            action = request["action"]
            if action == "start_queue":
                start_at = request.get("start_at")
                e.need(start_at is None or type(start_at) in {int, float} and
                       time.time() - 60 <= start_at <= time.time() + 30 * 86400,
                       "请选择未来 30 天内的开始时间")
                self.state.pop("error", None)
                self.state["running"] = True
                self.state["start_at"] = start_at
                self.state["stop_on_error"] = request.get("stop_on_error", True) is not False
                if not self.worker or not self.worker.is_alive():
                    self.worker = threading.Thread(target=self.loop, daemon=True)
                    self.worker.start()
            elif action == "pause_queue":
                self.state["running"] = False
            elif action == "clear_queue_history":
                self.state["items"] = [i for i in self.state["items"] if i["status"] not in {"done", "cancelled"}]
            elif action == "rename_queue_item":
                label = self.custom_label(request.get("task_name"))
                e.need(label, "请填写任务名称")
                item = next((i for i in self.state["items"] if i["id"] == request.get("item")), None)
                e.need(item, "这项任务已移出列表")
                item["label"] = label
            elif action == "retry_queue_item":
                self.ensure_capacity()
                item = next((i for i in self.state["items"] if i["id"] == request.get("item")), None)
                e.need(item and item["status"] in {"failed", "partial", "cancelled", "interrupted"},
                       "只有未完成的任务可以重新排队")
                if item.get("job"):
                    e.need(item["job"] not in self.app.jobs.controls,
                           "此任务仍在工作台执行，请先处理原任务")
                    try:
                        job = self.app.jobs.get(item["job"])
                    except FileNotFoundError:
                        # Queue settings and run_key remain durable even when
                        # the older job record is missing. Drawing recovery
                        # checks ambiguous submissions before producing more.
                        job = None
                    e.need(not job or job["status"] not in {"running", "pausing", "cancelling", "paused", "resumed"},
                           "此任务已暂停或已继续，请先在工作台处理原任务")
                item.update(status="waiting", message="重新排队，复用此任务已保存的图片")
                item.pop("finished", None)
            elif action == "remove_queue_item":
                item = next((i for i in self.state["items"] if i["id"] == request.get("item")), None)
                e.need(item and item["status"] != "running", "运行中任务请使用任务的结束按钮")
                self.state["items"].remove(item)
            elif action == "reorder_queue":
                ids = request.get("items")
                waiting = [i for i in self.state["items"] if i["status"] == "waiting"]
                e.need(isinstance(ids, list) and len(ids) == len(waiting)
                       and all(isinstance(i, str) for i in ids)
                       and set(ids) == {i["id"] for i in waiting}, "等待列表已变化，请刷新")
                ordered = iter(sorted(waiting, key=lambda i: ids.index(i["id"])))
                self.state["items"] = [next(ordered) if i["status"] == "waiting" else i for i in self.state["items"]]
            self.save()
            return self.snapshot()

    def run_item(self, item, update, jid):
        self.app.repo.get(item["project"])
        request = item["request"]
        if request["action"] == "draw_imported":
            from .prompt_exchange import draw_imported
            return draw_imported(self.app, item["project"], request, update, jid)
        if request["action"] == "prompt_batch":
            from .quick_sample import run
            return run(self.app, item["project"], request, update, jid)
        raise ValueError("不支持的排队任务")

    def tick(self):
        with self.lock:
            active = next((i for i in self.state["items"] if i["status"] == "running"), None)
            if active:
                job = self.app.jobs.get(active["job"])
                while job["status"] == "resumed":
                    active["job"] = job["resumed_as"]
                    job = self.app.jobs.get(active["job"])
                if active["job"] in self.app.jobs.controls:
                    return
                if job["status"] in {"done", "failed", "cancelled", "interrupted"}:
                    self.settle(active, job)
                    if active["status"] != "done" and self.state["stop_on_error"]:
                        self.state["running"] = False
                    self.save()
                else:
                    return
            if not self.state["running"]:
                return
            if self.state.get("start_at") and time.time() < self.state["start_at"]:
                return
            pending = next((i for i in self.state["items"] if i["status"] == "waiting"), None)
            if pending is None:
                self.state["running"] = False
                self.save()
                return
            # Ordinary work and paused work retain priority; never overlap GPU jobs.
            if GENERATION_LOCK.locked() or self.app.jobs.controls or any(
                j["status"] == "paused" for j in self.app.jobs.listing(limit=None)):
                return
            pending.update(status="running", message="准备开始")
            try:
                self.save()
            except Exception:
                pending.update(status="waiting", message="尚未保存启动记录，未开始生成")
                self.state["running"] = False
                raise
            try:
                pending["job"] = self.app.jobs.submit(pending["label"],
                    lambda update, jid: self.run_item(pending, update, jid), pending["project"],
                    request=pending["request"])
            except Exception as exc:
                if str(exc) in {"已有模型任务进行中，请等待完成", "有已暂停的任务，请先继续或取消该任务"}:
                    pending.update(status="waiting", message="等待工作台当前任务结束")
                else:
                    pending.update(status="failed", message=str(exc))
                    if self.state["stop_on_error"]:
                        self.state["running"] = False
            self.save()

    def loop(self):
        while not self.stop_event.wait(1):
            try:
                self.tick()
            except Exception as exc:
                with self.lock:
                    self.state.update(running=False, error=str(exc))
                    self.save()

    def close(self):
        self.stop_event.set()
