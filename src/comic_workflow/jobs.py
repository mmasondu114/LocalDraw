"""Single generation queue with restart-visible job records."""

import threading
import uuid
import time
import copy
from . import engine as e
from .model import GENERATION_LOCK
from .paths import DATA
from .repository import safe_id
from .task_control import local, TaskCancelled


class Jobs:
    def __init__(self, on_pause=None, on_finish=None):
        self.on_pause = on_pause
        self.on_finish = on_finish
        self.root = DATA / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.controls = {}
        self.memory = {}
        for f in self.root.glob("*.json"):
            j = e.read(f)
            if j["status"] in {"running", "pausing", "cancelling", "paused"}:
                resumable = bool(j.get("request")) and j["status"] != "cancelling"
                j.update(
                    status="paused" if resumable else "interrupted",
                    message="服务重启，任务已暂停；点击继续可复用已保存结果" if resumable else "上次应用关闭，任务中断；已确认内容未改变",
                )
                e.save(f, j)

    def save(self, jid, job):
        if job.get("transient"):
            self.memory[jid] = dict(job)
        else:
            e.save(self.root / (jid + ".json"), job)

    def get(self, jid):
        with self.lock:
            return self.memory.get(safe_id(jid)) or e.read(self.root / (jid + ".json"))

    def listing(self, limit=30):
        with self.lock:
            return sorted(
                [e.read(f) for f in self.root.glob("*.json")] + list(self.memory.values()),
                key=lambda j: j["created"],
                reverse=True,
            )[:limit]

    def worker_request(self, jid):
        """The parked worker and quantity editor must share this exact object."""
        with self.lock:
            return self.controls[jid]['job']['request']

    def update_paused_request(self, jid, transform):
        """Persist first, then update the request object held by the parked worker."""
        with self.lock:
            live = self.controls.get(jid)
            job = live['job'] if live else self.get(jid)
            e.need(job['status'] == 'paused', '请等待任务显示“已暂停”后再修改数量')
            changed = transform(copy.deepcopy(job.get('request', {})))
            updated = {**job, 'request': changed, 'eta': None}
            if changed.get('candidate_slots_by_panel') and job.get('progress'):
                updated['progress'] = {**job['progress'],
                    'total': sum(len(s) for s in changed['candidate_slots_by_panel'].values())}
            self.save(jid, updated)
            if live:
                original_request = job['request']
                original_request.clear()
                original_request.update(changed)
                job.update({k: v for k, v in updated.items() if k != 'request'})
            return {'job': jid, 'status': 'paused', 'quantity_change': changed.get('quantity_change'),
                    'quantity_revision': changed.get('quantity_revision', 0)}

    def control(self, jid, command, confirm=False, resume_fn=None):
        with self.lock:
            job = self.get(jid)
            e.need(command in {"pause", "resume", "cancel", "retry"}, "未知任务操作")
            if command == "cancel":
                e.need(confirm is True, "取消任务需要二次确认")
            live = self.controls.get(jid)
            if live:
                e.need(command != "retry", "任务仍在收尾，请稍后重试")
                current = live["job"]
                allowed = {"pause": {"running", "pausing", "paused"},
                           "resume": {"paused", "pausing", "running"},
                           "cancel": {"running", "pausing", "paused", "cancelling"}}
                e.need(current["status"] in allowed[command], "任务已经结束，请刷新状态")
                if command == "pause":
                    live["command"] = "pause"
                    if current["status"] != "paused":
                        current["status"] = "pausing"
                elif command == "resume":
                    live["command"] = None
                    current["status"] = "running"
                    # Previous estimates include the pause; wait for fresh samples.
                    current["eta"] = None
                else:
                    live["command"] = "cancel"
                    current["status"] = "cancelling"
                self.save(jid, current)
                live["condition"].notify_all()
                return {"job": jid, "status": current["status"]}
            if job["status"] == "interrupted" and command == "cancel":
                job.update(status="cancelled", message="任务已结束，已有图片、剧本和选图均已保留", finished_at=time.time())
                self.save(jid, job)
                return {"job": jid, "status": "cancelled"}
            e.need(job["status"] == ("failed" if command == "retry" else "paused"),
                   "任务已经结束或无法继续")
            if command == "pause":
                return {"job": jid, "status": "paused"}
            if command == "cancel":
                job.update(status="cancelled", message="已取消，已有图片和选图均已保留", finished_at=time.time())
                self.save(jid, job)
                return {"job": jid, "status": "cancelled"}
            e.need(job.get("request") and resume_fn, "此任务无法直接恢复，请重新发起")
            request = {**job["request"], "resume_existing": True} if command == "retry" else job["request"]
            new_id = self.submit(job["label"], resume_fn, job["project"], request=request, resume_of=jid, initial_progress=job.get("progress"))
            job.update(status="resumed", resumed_as=new_id,
                       message="已重新生成出错部分，复用已保存页面" if command == "retry" else "已继续，复用已有结果")
            self.save(jid, job)
            return {"job": new_id, "status": "running"}

    def submit(self, label, fn, pid=None, request=None, resume_of=None, initial_progress=None, transient=False):
        with self.lock:
            e.need(not any(j["status"] == "paused" and j["id"] != resume_of for j in self.listing()),
                   "有已暂停的任务，请先继续或取消该任务")
        e.need(
            GENERATION_LOCK.acquire(blocking=False), "已有模型任务进行中，请等待完成"
        )
        jid = uuid.uuid4().hex
        job = {
            "id": jid,
            "transient": transient or bool(pid and pid.startswith("tmp-")),
            "project": pid,
            "label": label,
            "status": "running",
            "message": "准备中",
            "created": e.stamp(),
            "started_at": time.time(),
        }
        if request is not None:
            job["request"] = request
        if initial_progress is not None:
            job["progress"] = initial_progress
        condition = threading.Condition(self.lock)
        control = {"job": job, "command": None, "condition": condition}
        with self.lock:
            self.controls[jid] = control

        def checkpoint():
            while True:
                with condition:
                    if control["command"] == "cancel":
                        raise TaskCancelled()
                    if control["command"] != "pause":
                        return
                paused_at = time.monotonic()
                restore = None
                try:
                    # The work unit is durable now. Keep slow model operations
                    # outside the jobs lock so status and cancel stay responsive.
                    if self.on_pause:
                        restore = self.on_pause()
                    warning = None
                except Exception as exc:
                    warning = str(exc)
                with condition:
                    job.pop("pause_warning", None)
                    if warning:
                        job["pause_warning"] = warning
                        job["message"] = "已暂停；模型释放未完成：" + warning
                    elif self.on_pause:
                        job["message"] = "已暂停，模型已释放；继续时自动加载"
                    while control["command"] == "pause":
                        job["status"] = "paused"
                        job["eta"] = None
                        self.save(jid, job)
                        condition.wait()
                    cancelled = control["command"] == "cancel"
                if not cancelled and restore:
                    restore(update)
                local.paused_seconds = getattr(local, "paused_seconds", 0) + time.monotonic() - paused_at

        def update(message):
            with self.lock:
                if isinstance(message, dict):
                    job.update(
                        {
                            k: v
                            for k, v in message.items()
                            if k
                            in [
                                "message",
                                "progress",
                                "detail_progress",
                                "runtime",
                                "eta",
                            ]
                        }
                    )
                else:
                    job["message"] = message
                self.save(jid, job)

        update("准备中")

        def run():
            local.checkpoint = checkpoint
            local.project = pid
            try:
                checkpoint()
                try:
                    result = fn(update, jid)
                finally:
                    if self.on_finish:
                        cleanup = self.on_finish(job)
                        if cleanup is not None:
                            job['context_cleanup'] = cleanup
                checkpoint()
                with self.lock:
                    job.update(status="done", result=result, finished_at=time.time())
                drawing = (
                    (result or {}).get("drawing", result or {})
                    if isinstance(result, dict)
                    else {}
                )
                unresolved = drawing.get("unresolved", [])
                update(
                    drawing.get("completion_message") or (f"已处理完，{len(unresolved)} 格仍有未通过或待重试候选"
                    if unresolved
                    else "完成")
                )
            except TaskCancelled:
                with self.lock:
                    job.update(status="cancelled", finished_at=time.time())
                update("已取消，已有图片、剧本和选图均已保留")
            except Exception as exc:
                with self.lock:
                    job["status"] = "failed"
                    job["finished_at"] = time.time()
                update(str(exc))
            finally:
                local.checkpoint = None
                local.project = None
                with self.lock:
                    self.controls.pop(jid, None)
                GENERATION_LOCK.release()

        threading.Thread(target=run, daemon=True).start()
        return jid
