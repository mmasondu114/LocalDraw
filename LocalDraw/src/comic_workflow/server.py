"""Loopback-only HTTP application. Browser edits require same-origin session token."""

import atexit
import json
import mimetypes
import secrets
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import engine as e
from .jobs import Jobs
from .model import ModelManager, generate
from .paths import ROOT, DATA, settings
from .repository import Repository, safe_id
from . import comfyui, layout, sudowrite
from . import creative
from . import drawing_settings
from . import art_presets, prompt_models
from .characters import CharacterLibrary, DETAILS
from .lora_inspection import inspect_local


class Application:
    def __init__(self, repo=None):
        self.repo = repo or Repository()
        from .model_downloads import ModelDownloads
        self.model_downloads = ModelDownloads()
        self.jobs = Jobs(on_pause=self.release_paused_models, on_finish=self.clear_finished_context)
        from .storyboard_tools import restore_jobs
        restore_jobs(self.repo, self.jobs)
        self.model = ModelManager()
        self.pending_idle_worker = None
        self.idle_release_timer = None
        self.comfy = comfyui.ComfyManager()
        self.token = secrets.token_urlsafe(32)
        self.characters = CharacterLibrary(self.repo.root.parent / "characters")
        from .night_queue import NightQueue
        self.night_queue = NightQueue(self)

    def clear_finished_context(self, job):
        # The current writer still owns GENERATION_LOCK until cleanup returns.
        if job["label"] in {"启动语言模型", "启动 FlashNext", "启动分析模型"}:
            self.pending_idle_worker = None
            return None
        if not self.model.owns_live_worker():
            return {"status": "already_unloaded"}
        with self.night_queue.lock, self.jobs.lock:
            newer_jobs = any(other != job.get("id") for other in self.jobs.controls)
            waiting = any(item["status"] in {"waiting", "running"} and
                          (item["status"] == "waiting" or item.get("job") != job.get("id"))
                          for item in self.night_queue.state["items"])
            self.pending_idle_worker = self.model.owned_worker()
            if newer_jobs or waiting:
                return {"status": "deferred", "reason": "工作室还有任务"}
            result = self.model.unload_if_idle()
            if result["status"] == "deferred":
                self.schedule_idle_retry(self.pending_idle_worker)
            else:
                self.pending_idle_worker = None
            return result

    def schedule_idle_retry(self, worker):
        # The server may still be draining a cancelled stream. Retry only a
        # release that was already requested, and only for the same process.
        if self.idle_release_timer:
            self.idle_release_timer.cancel()
        def retry():
            self.release_idle_queue_model(worker)
        timer = threading.Timer(5, retry)
        timer.daemon = True
        self.idle_release_timer = timer
        timer.start()

    def release_idle_queue_model(self, expected=None):
        from .model import GENERATION_LOCK
        worker = self.pending_idle_worker if expected is None else expected
        if worker is None or not GENERATION_LOCK.acquire(blocking=False):
            return
        try:
            if (self.pending_idle_worker is worker and self.model.owned_worker() is worker and
                    self.model.owns_live_worker()):
                self.clear_finished_context({"label": "队列已更新"})
        finally:
            GENERATION_LOCK.release()

    def release_drawing_cache(self):
        if comfyui.discover()["ready"]:
            queue = comfyui.api("/queue")
            e.need(not queue.get("queue_running") and not queue.get("queue_pending"),
                   "绘图进行中，请等待完成")
            if self.comfy.process is not None and self.comfy.process.poll() is None:
                comfyui.api("/free", {"unload_models": True, "free_memory": True})

    def release_paused_models(self):
        """Unload at a durable pause boundary; keep ComfyUI alive for resume."""
        self.release_drawing_cache()
        if self.model.status()["owned"]:
            vision = self.model.vision
            self.model.stop()
            return lambda update: self.model.start(vision=vision, update=update)

    def trash_state(self):
        jobs = self.jobs.listing(limit=None)
        result = self.repo.trash()
        active = {"running", "pausing", "paused", "cancelling"}
        for item in result:
            related = [job for job in jobs if job.get("project") == item["id"]]
            item["jobs"] = [job for job in related if job["status"] in active] or related[:1]
            with self.repo.task_access(item["id"]):
                path = self.repo.folder(item["id"]) / "images/index.json"
                batches = e.read(path).get("batches", []) if path.exists() else []
                item["drawing_states"] = sorted({b.get("status", "unknown") for b in batches
                                                 if b.get("status") in {"running", "queued", "submitted", "uncertain"}})
        return result

    def release_prompt(self, pid):
        if pid not in self.repo.transient_ids:
            return
        if any(c["job"].get("project") == pid for c in self.jobs.controls.values()):
            timer = threading.Timer(2, self.release_prompt, args=(pid,))
            timer.daemon = True
            timer.start()
            return
        from .repository import LOCK
        with LOCK:
            if pid in self.repo.transient_ids:
                self.repo.release_transient(pid)
        with self.jobs.lock:
            for jid, job in list(self.jobs.memory.items()):
                if job.get("project") == pid:
                    self.jobs.memory.pop(jid, None)

    def prepare_language(self, vision=False, update=lambda message: None):
        from .paths import use_vision_model
        use_vision_model(vision)
        self.release_drawing_cache()
        self.model.start(vision=vision, update=update)

    def action(self, a):
        action = a["action"]
        pid = a.get("project")
        if action in {"check_update", "open_releases"}:
            from . import updates
            return updates.check(a.get("token", "")) if action == "check_update" else updates.open_releases()
        if action == "download_model_asset":
            return self.model_downloads.start(a.get("asset"))
        if action == "open_model_folder":
            return self.model_downloads.open_folder(a.get("asset"))
        if action in {"analysis_model", "set_analysis_model"}:
            from .analysis_models import choices, select_model
            return choices() if action == "analysis_model" else select_model(a.get("model"), self.model)
        if action in {"start_queue", "pause_queue", "remove_queue_item", "reorder_queue", "retry_queue_item", "clear_queue_history", "rename_queue_item"}:
            result = self.night_queue.control(a)
            if action in {"remove_queue_item", "clear_queue_history"}:
                self.release_idle_queue_model()
            return result
        if action == "queue_prompt":
            from .quick_sample import create
            with self.night_queue.lock:
                self.night_queue.ensure_capacity()
                label = self.night_queue.custom_label(a.get("task_name"))
                result = create(self.repo, a, persistent=True)
                queued_pid = result["project"]
                project = self.repo.get(queued_pid)
                return self.night_queue.add(queued_pid, label or project["brief"]["title"],
                    {"action": "prompt_batch", "base_hash": e.digest(project), "run_key": queued_pid,
                     "checkpoint": project["prompt_input"]["checkpoint"], "candidate_count": project["prompt_input"]["count"]})
        if action in {"queue_drawing", "queue_imported"}:
            if action == "queue_drawing":
                from .manual_drawing import prepare_request
            else:
                from .prompt_exchange import prepare_request
            with self.night_queue.lock:
                self.night_queue.ensure_capacity()
                label = self.night_queue.custom_label(a.get("task_name"))
                request = prepare_request(self.repo, pid, a)
                return self.night_queue.add(pid, label or self.repo.get(pid)["brief"]["title"] + " · 出图", request)
        if action == "set_remaining_candidate_count":
            from .drawing_quantity import change_remaining_count
            saved = self.jobs.get(a['job'])
            e.need(pid == saved.get('project'), '任务不属于当前作品')
            return self.jobs.update_paused_request(a['job'], lambda request:
                change_remaining_count(request, comfyui.drawing_state(self.repo, pid)['batches'],
                                       a.get('candidate_count'), a.get('quantity_mode', 'remaining')))
        if action in {"preview_direct_story", "save_direct_story", "preview_direct_prompts", "save_direct_prompts"}:
            from . import direct_imports
            return direct_imports.handle(self.repo, self.characters, a)
        if action in {"preview_story_import", "save_story_import"}:
            from . import story_import
            if action == "preview_story_import":
                return story_import.preview(a.get("document"), self.characters)
            return story_import.save_import(self.repo, self.characters, a)
        if action == "copy_prompt_images":
            from .image_clipboard import copy_images
            return copy_images(self.repo, pid, a.get("images"))
        if action == "clear_prompt_images":
            from .quick_sample import clear_images
            return clear_images(self, pid)
        if action == "clear_generated_images":
            from .drawing_reset import clear_images
            return clear_images(self, pid, a.get("base_hash"))
        if action in {"pause_job", "resume_job", "cancel_job", "retry_job"}:
            saved = self.jobs.get(a["job"])
            if action == "retry_job":
                e.need(saved["status"] == "failed", "只能重新生成已出错的任务")
                e.need(saved.get("request", {}).get("action") == "storyboard_all",
                       "此按钮仅用于重新生成出错的分镜任务")
                e.need(saved["request"].get("bundle_id"), "找不到分镜断点，已有结果保留")
                saved["request"] = {**saved["request"], "resume_existing": True}
            if action in {"resume_job", "retry_job"} and saved.get("request", {}).get("action") == "storyboard_all":
                from .storyboard_tools import load_checkpoint
                with self.repo.task_access(saved["project"]):
                    load_checkpoint(self.repo, saved["project"], saved["request"])
            def resume(update, jid):
                request = self.jobs.worker_request(jid)
                if request.get("action") == "understand_and_redraw":
                    from .problem_panels import understand_and_redraw
                    return understand_and_redraw(self, saved["project"], request['document'], update, jid)
                if request.get("action") == "understand_problems":
                    from .problem_panels import understand
                    return understand(self, saved["project"], request['document'], update, jid)
                if request.get("action") == "fill_local_prompts":
                    from .local_prompts import generate_draft
                    return generate_draft(self, saved["project"], request, update, jid)
                if request.get("action") == "storyboard_all":
                    from .storyboard_tools import generate_all
                    return generate_all(self, saved["project"], request, update, jid)
                if request.get("action") == "draw_imported":
                    from .prompt_exchange import draw_imported
                    return draw_imported(self, saved["project"], request, update, jid)
                if request.get("action") == "prompt_batch":
                    from .quick_sample import run
                    return run(self, saved["project"], request, update, jid)
                raise ValueError("旧版自动审核补画任务已停用，请重新核对提示词后发起出图；已有图片保留")
            return self.jobs.control(a["job"], action.removesuffix("_job"),
                                     confirm=a.get("confirm", False), resume_fn=resume)
        if action == "translate_project_english":
            from .project_translation import translate
            request = {"action": action, "base_hash": e.digest(self.repo.get(pid))}
            return {"job": self.jobs.submit("翻译英文剧本与分镜",
                lambda update, jid: translate(self, pid, request, update, jid), pid, request=request)}
        if action in {"save_problem", "export_problems", "import_problems", "understand_problems", "understand_and_redraw", "redraw_problems"}:
            from . import problem_panels
            if action == "save_problem":
                return problem_panels.save(self.repo, pid, a)
            if action == "export_problems":
                return problem_panels.export(self.repo, pid)
            if action == "import_problems":
                return problem_panels.import_result(self.repo, pid, a.get('document'))
            if action in {"understand_problems", "understand_and_redraw"}:
                document = problem_panels.export(self.repo, pid)
                request = {'action': action, 'document': document}
                if action == "understand_and_redraw":
                    return {'job': self.jobs.submit('按问题说明统一重画',
                        lambda update, jid: problem_panels.understand_and_redraw(self, pid, document, update, jid),
                        pid, request=request)}
                return {'job': self.jobs.submit('理解问题格子 · FlashNext',
                    lambda update, jid: problem_panels.understand(self, pid, document, update, jid), pid, request=request)}
            from .prompt_exchange import draw_imported
            request = problem_panels.prepare_drawing(self.repo, pid)
            return {'job': self.jobs.submit('重画全部问题格子',
                lambda update, jid: draw_imported(self, pid, request, update, jid), pid, request=request)}
        if action == "fill_local_prompts":
            from . import local_prompts
            # Validate before queuing, without creating files or starting a model.
            document = local_prompts.export_storyboard(self.repo, pid)
            request = {"action": action, "draft_id": uuid.uuid4().hex,
                       "storyboard_hash": document["storyboard_hash"]}
            return {"job": self.jobs.submit("本地 Qwen 填写提示词",
                    lambda update, jid: local_prompts.generate_draft(self, pid, request, update, jid),
                    pid, request=request)}
        if action in {"preview_prompt_import", "save_prompt_import", "draw_imported"}:
            from . import prompt_exchange
            if action == "preview_prompt_import":
                return prompt_exchange.preview(self.repo, pid, a.get("document"))
            if action == "save_prompt_import":
                return prompt_exchange.save_import(self.repo, pid, a)
            request = prompt_exchange.prepare_request(self.repo, pid, a)
            return {"job": self.jobs.submit("按导入提示词逐格绘制",
                    lambda update, jid: prompt_exchange.draw_imported(self, pid, request, update, jid), pid, request=request)}
        if action == "release_prompt":
            self.release_prompt(pid)
            return {"released": True}
        if action == "create_quick_sample":
            from .quick_sample import create

            return create(self.repo, a)
        if action == "prompt_batch":
            from .quick_sample import run

            return {"job": self.jobs.submit("提示词出图", lambda update, jid: run(self, pid, a, update, jid), pid)}
        if action == "prompt_translate":
            from .quick_sample import translate

            return {"job": self.jobs.submit("转换英文绘图提示词", lambda update, jid: translate(self, a, update, jid), transient=True)}
        if action == "new_workspace":
            from .repository import LOCK

            with LOCK:
                path = self.repo.folder("new") / "workspace.json"
                previous = e.read(path) if path.exists() else {}
                local = a.get("previous_state") or {}
                e.need(isinstance(local, dict) and len(json.dumps(a)) < 3000000, "草稿过大")
                if local.get("updated", 0) >= previous.get("updated", 0):
                    previous = local
                result = a.get("draft_result")
                if previous or result:
                    e.save(self.repo.root.parent / "input-drafts" / (e.stamp() + ".json"),
                           {"state": previous, "result": result})
                state = {"stage": "script", "target": "", "views": {
                    "script:": {"fields": {"story-text": "", "story-preferences": "",
                                           "story-mode": "structure"}, "scroll": 0}},
                    "updated": max(int(time.time() * 1000), previous.get("updated", 0) + 1)}
                e.save(path, state)
                return state
        if action in {"delete_project", "delete_projects", "restore_deleted_project"}:
            if action == "delete_projects":
                e.need(a.get("confirm") is True, "请确认删除所选作品")
                return self.repo.delete_many(a.get("items"))
            if action == "delete_project":
                e.need(a.get("confirm") is True, "请确认删除作品")
                return self.repo.delete(pid, a["base_hash"])
            return self.repo.restore_deleted(pid)
        if action == "storyboard_all":
            from .storyboard_tools import generate_all
            request = {**a, "bundle_id": uuid.uuid4().hex}
            return {
                "job": self.jobs.submit(
                    "生成整部分镜",
                    lambda update, jid: generate_all(self, pid, request, update, jid),
                    pid,
                    request=request,
                )
            }
        if action == "accept_storyboard_bundle":
            from .storyboard_tools import accept_bundle

            return accept_bundle(self.repo, pid, a["bundle"])
        if action == "decide_change":
            from .storyboard_tools import decide

            return decide(self.repo, pid, a["candidate"], a["unit"], a["decision"])
        if action == "save_workspace":
            e.need(
                isinstance(a["state"], dict) and len(json.dumps(a["state"])) < 2000000,
                "编辑草稿过大",
            )
            from .repository import LOCK

            with LOCK:
                path = self.repo.folder(pid) / "workspace.json"
                previous = e.read(path) if path.exists() else {}
                e.need(
                    a["state"].get("updated", 0) >= previous.get("updated", 0),
                    "已有更新的编辑记录",
                )
                archive_time = previous.get("archived_at", 0)
                if previous and time.time() - archive_time >= 60:
                    e.save(
                        path.parent / "workspace-history" / (e.stamp() + ".json"),
                        previous,
                    )
                    archive_time = time.time()
                e.save(path, {**a["state"], "archived_at": archive_time})
            return {"saved": True}
        if action == "automatic":
            from .automatic import run_automatic

            e.need(a.get("authorize_auto_confirm") is True, "需要明确授权自动保存分镜")
            e.need(e.digest(self.repo.get(pid)) == a["base_hash"], "作品已变化，请刷新")
            return {
                "job": self.jobs.submit(
                    "自动制作整部漫画",
                    lambda update, jid: run_automatic(self, pid, update, jid),
                    pid,
                )
            }
        if action == "character_upload":
            return self.characters.upload(a["data_url"])
        if action == "save_lora_labels":
            return comfyui.save_lora_labels(a["items"])
        if action == "save_drawing_settings":
            return drawing_settings.save(self.repo, pid, a["settings"])
        if action == "preview_art_preset":
            value = art_presets.apply(a["settings"], a["preset"])
            return drawing_settings.validate(value)
        if action == "character_save":
            return self.characters.save(
                a["name"],
                a["profile"],
                a["source"],
                a.get("image_id"),
                a.get("base_revision", 0),
            )
        if action in ["character_draft", "story_draft"]:

            def draft(update, jid):
                update("正在核对并加载模型，随后生成草稿")
                self.prepare_language(
                    vision=action == "character_draft" and a.get("mode") == "image",
                    update=update,
                )
                folder = DATA / "creative" / jid
                fn = (
                    creative.character_draft
                    if action == "character_draft"
                    else creative.story_draft
                )
                result = fn(self.characters, a, folder, update)
                e.save(folder / "draft.json", {"kind": action, **result})
                return result

            return {
                "job": self.jobs.submit(
                    "角色建档草稿" if action == "character_draft" else "整理剧本草稿",
                    draft,
                )
            }
        if action == "create_story":
            if a.get("drawing_settings") is not None:
                drawing_settings.validate(a["drawing_settings"])
            created = creative.create_story(self.repo, self.characters, a["result"])
            if a.get("drawing_settings") is not None:
                drawing_settings.save(self.repo, created, a["drawing_settings"])
            return {"project": created}
        if action == "storyboard":
            initial = self.repo.get(pid)
            e.need(e.digest(initial) == a["base_hash"], "项目已变化，请刷新")

            def storyboard(update, jid):
                update({"message": "正在核对并加载语言模型", "progress": None})
                self.prepare_language(update=update)
                p = self.repo.get(pid)
                e.need(e.digest(p) == a["base_hash"], "等待模型时项目已变化，请重试")
                if not p.get("plan"):
                    update("按已确认剧本自动分页，不另建世界观或剧情方案")
                    payload, metrics = generate(
                        p,
                        "plan",
                        "",
                        "按已确认剧本安排大小不同的格子。",
                        self.repo.folder(pid) / "runs" / jid / "plan",
                        update,
                    )
                    c = self.repo.propose(
                        pid,
                        "plan",
                        "",
                        payload,
                        e.digest(p),
                        "已确认剧本的自动分页",
                        metrics,
                    )
                    p = self.repo.accept(pid, c["id"])
                target = a.get("target") or next(
                    (x["id"] for x in p["plan"]["pages"] if x["id"] not in p["pages"]),
                    p["plan"]["pages"][0]["id"],
                )
                stage = "panel" if "-K" in target else "page"
                update(
                    {
                        "message": f"正在编写 {target} 的画面与对白",
                        "progress": {
                            "completed": 1,
                            "total": 3,
                            "unit": "步骤（分页／分镜／审核）",
                        },
                    }
                )
                payload, metrics = generate(
                    p,
                    stage,
                    target,
                    a.get("instruction", ""),
                    self.repo.folder(pid) / "runs" / jid / target,
                    update,
                )
                proposed = e.merged(p, stage, target, payload)
                try:
                    update(
                        {
                            "message": "正在自动检查分镜连续性与对白",
                            "progress": {
                                "completed": 2,
                                "total": 3,
                                "unit": "步骤（分页／分镜／审核）",
                            },
                        }
                    )
                    audit, audit_metrics = generate(
                        proposed,
                        "audit",
                        target.split("-")[0],
                        "核对原文事件、人物口吻、动作单一瞬间、道具和位置衔接。仅报告，不改稿。",
                        self.repo.folder(pid) / "runs" / jid / "continuity",
                        update,
                    )
                    metrics["continuity_review"] = audit
                    metrics["continuity_metrics"] = audit_metrics
                except Exception as exc:
                    metrics["continuity_review"] = {
                        "verdict": "unavailable",
                        "reason": str(exc),
                        "note": "连续性审核失败，不能视为通过",
                    }
                c = self.repo.propose(
                    pid,
                    stage,
                    target,
                    payload,
                    e.digest(p),
                    "按确认剧本生成分镜",
                    metrics,
                )
                update(
                    {
                        "message": "分镜草稿已生成，等待你确认",
                        "progress": {
                            "completed": 3,
                            "total": 3,
                            "unit": "步骤（分页／分镜／审核）",
                        },
                    }
                )
                return {"candidate": c["id"], "target": target}

            return {"job": self.jobs.submit("生成文字分镜", storyboard, pid)}
        if action == "audit_candidate":
            p = self.repo.get(pid)
            e.need(e.digest(p) == a["base_hash"], "作品已变化，请刷新")
            c = next(
                (c for c in self.repo.candidates(pid) if c["id"] == a["candidate"]),
                None,
            )
            e.need(
                c and c["stage"] in ["page", "panel"] and c["base_hash"] == e.digest(p),
                "候选不存在或基于旧版本，不能审核",
            )

            def audit_candidate(update, jid):
                self.prepare_language(update=update)
                proposed = e.merged(p, c["stage"], c["target"], c["payload"])
                audit, audit_metrics = generate(
                    proposed,
                    "audit",
                    c["target"].split("-")[0],
                    "核对原文事件、角色身份、动作单一瞬间及状态衔接；只报告，不改稿。",
                    self.repo.folder(pid) / "runs" / jid / "continuity",
                    update,
                )
                revised = self.repo.propose(
                    pid,
                    c["stage"],
                    c["target"],
                    c["payload"],
                    e.digest(p),
                    "仅补做连续性审核，分镜内容不变",
                    {
                        **c["metadata"],
                        "continuity_review": audit,
                        "continuity_metrics": audit_metrics,
                        "reviewed_candidate": c["id"],
                    },
                )
                return {"candidate": revised["id"], "target": c["target"]}

            return {"job": self.jobs.submit("补做连续性审核", audit_candidate, pid)}
        if action == "create":
            brief = self.characters.freeze_brief(a["brief"]) if a.get("brief") else None
            return {"project": self.repo.create(brief, a.get("sample", False))}
        if action == "save_draft":
            self.repo.draft(
                pid, a["stage"], a.get("target", ""), a["payload"], a["base_hash"]
            )
            return {"saved": True}
        if action == "propose":
            if a["stage"] == "brief":
                a["payload"] = self.characters.freeze_brief(a["payload"])
            return self.repo.propose(
                pid, a["stage"], a.get("target", ""), a["payload"], a["base_hash"]
            )
        if action == "accept":
            return self.repo.accept(pid, a["candidate"])
        if action == "restore":
            return self.repo.restore(pid, a["revision"], a["base_hash"])
        if action == "fork":
            return {"project": self.repo.fork(pid, a["reset_from"])}
        if action == "export":
            return self.repo.export(pid)
        if action == "validate":
            return {
                "warnings": e.validate_project(
                    self.repo.get(pid), a.get("complete", False)
                )
            }
        if action == "start_model":

            def start(update, jid):
                self.prepare_language(update=update)

            return {"job": self.jobs.submit("启动分析模型", start)}
        if action == "stop_model":
            from .model import GENERATION_LOCK
            e.need(GENERATION_LOCK.acquire(blocking=False), "请等待当前任务结束后卸载模型")
            try:
                result = self.model.unload_if_idle()
                e.need(result["status"] in {"unloaded", "already_unloaded"},
                       result.get("reason", "模型仍有请求"))
                return self.model.status()
            finally:
                GENERATION_LOCK.release()
        if action == "generate":
            p = self.repo.get(pid)
            e.need(e.digest(p) == a["base_hash"], "项目已变化，请刷新")
            e.need(self.model.owns_live_worker() and self.model.status()["ready"],
                   "工作室自己的分析模型尚未就绪，请先点击启动分析模型")

            def run(update, jid):
                e.need(self.model.owns_live_worker() and self.model.status()["ready"],
                       "分析模型已释放，请重新启动后再提交")
                drawing_kwargs = {}
                if a["stage"] == "drawing_prompt":
                    prefs = drawing_settings.load(self.repo, pid)
                    drawing_kwargs["drawing_checkpoint"] = (prefs or {}).get("checkpoint") or comfyui.config()["checkpoint"]
                with self.model.inference_context():
                    payload, metrics = generate(
                        p, a["stage"], a.get("target", ""), a.get("instruction", ""),
                        self.repo.folder(pid) / "runs" / jid, update, **drawing_kwargs)
                return self.repo.propose(
                    pid,
                    a["stage"],
                    a.get("target", ""),
                    payload,
                    e.digest(p),
                    "本地模型生成",
                    metrics,
                )

            return {"job": self.jobs.submit("生成 " + a["stage"], run, pid)}
        if action == "start_comfy":
            return {
                "job": self.jobs.submit(
                    "启动 ComfyUI", lambda update, jid: self.comfy.start()
                )
            }
        if action in {"redraw_page", "redraw_panel"}:
            from .page_redraw import prepare
            from .prompt_exchange import draw_imported
            request = prepare(self.repo, pid, a, single_panel=action == "redraw_panel")
            label = "重画当前格" if action == "redraw_panel" else "重画当前页"
            return {"job": self.jobs.submit(label + (" · 按问题修改" if request['revision_note'] else ""),
                lambda update, jid: draw_imported(self, pid, request, update, jid), pid, request=request)}
        if action == "preview_drawing":
            from .manual_drawing import preview
            return preview(self.repo, pid, a)
        if action in {"draw", "draw_all"}:
            from .manual_drawing import prepare_request
            from .prompt_exchange import draw_imported
            request = prepare_request(self.repo, pid, {**a, **({"targets":[a["target"]], "force":True} if action == "draw" else {})})
            return {"job": self.jobs.submit("按确认提示词出图 · 自己选图",
                lambda update, jid: draw_imported(self, pid, request, update, jid), pid, request=request)}
        if action == "small_batch":
            from .drawing_tools import small_batch

            return {"job": self.jobs.submit(
                "单张 / 少量试画",
                lambda update, jid: small_batch(self, pid, a, update, jid), pid
            )}
        if action == "drawing_pilot":
            raise ValueError("自动镜头改写已停用，请核对提示词后直接出图")
        if action == "local_repair":
            from .drawing_tools import local_repair
            return {
                "job": self.jobs.submit(
                    "框选区域局部修复",
                    lambda update, jid: local_repair(self, pid, a, update, jid),
                    pid,
                )
            }
        if action == "select_image":
            index = comfyui.select_image(
                self.repo, pid, a["target"], a["image"], a.get("allow_rejected", False), a.get("allow_unreviewed", False)
            )
            return {"saved": True} if a.get("compact") else index
        if action == "compose":
            return layout.compose(self.repo, pid, a["target"], a["layout"])
        if action == "plan_bubbles":
            e.need(a.get("use_model") is True, "气泡规划会使用本地模型；游戏中请保持暂停 AI")
            self.repo.get(pid)
            def plan_bubbles(update, jid):
                from . import bubble_planner
                self.prepare_language(update=update)
                return bubble_planner.generate(self.repo, pid, self.repo.folder(pid) / "runs" / jid,
                                               update, force=a.get("force") is True)
            return {"job": self.jobs.submit("AI 逐句选择气泡", plan_bubbles, pid)}
        if action == "sudowrite_package":
            return sudowrite.package(
                self.repo,
                pid,
                a["stage"],
                a.get("target", ""),
                a.get("instruction", ""),
            )
        if action == "sudowrite_import":
            return sudowrite.import_result(self.repo, pid, a["handoff"], a["text"])
        raise ValueError("未知操作")

    def snapshot(self, pid):
        p = self.repo.get(pid)
        return {
            "project": p,
            "hash": e.digest(p),
            "candidates": self.repo.candidates(pid),
            "drafts": self.repo.drafts(pid),
            "history": self.repo.history(pid),
            "warnings": e.validate_project(p),
        }


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def send(self, status, data, content_type="application/json; charset=utf-8"):
            raw = (
                data
                if isinstance(data, bytes)
                else json.dumps(data, ensure_ascii=False).encode()
            )
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'",
            )
            self.end_headers()
            self.wfile.write(raw)

        def check_host(self):
            e.need(
                self.headers.get("Host")
                in [
                    f"127.0.0.1:{self.server.server_port}",
                    f"localhost:{self.server.server_port}",
                ],
                "仅允许本机访问",
            )

        def do_GET(self):
            try:
                self.check_host()
                url = urlparse(self.path)
                query = parse_qs(url.query)
                if url.path == "/api/model-downloads":
                    self.send(200, app.model_downloads.snapshot())
                    return
                if url.path == "/api/session":
                    self.send(200, {"token": app.token, "workspace": str(ROOT)})
                    return
                if url.path == "/api/desktop-book":
                    from .desktop import book
                    self.send(200, book(app.repo, query['id'][0]))
                    return
                if url.path == "/api/bootstrap":
                    self.send(
                        200,
                        {
                            "token": app.token,
                            "schemas": e.SCHEMAS,
                            "template": e.read(ROOT / "config/template.json"),
                            "projects": app.repo.listing(),
                            "jobs": app.jobs.listing(),
                        },
                    )
                    return
                if url.path == "/api/queue":
                    self.send(200, app.night_queue.snapshot())
                    return
                if url.path == "/api/projects":
                    self.send(200, app.repo.listing())
                    return
                if url.path == "/api/images":
                    self.send(200, {"images": comfyui.drawing_state(app.repo, query['id'][0])})
                    return
                if url.path == "/api/drawing-settings":
                    self.send(200, drawing_settings.load(app.repo, query['id'][0]))
                    return
                if url.path == "/api/queue-preview":
                    self.send(200, app.night_queue.preview(query["item"][0]))
                    return
                if url.path == "/api/project":
                    self.send(200, app.snapshot(query["id"][0]))
                    return
                if url.path == "/api/prompt-exchange":
                    from .prompt_exchange import export_download
                    self.send(200, export_download(app.repo, query["id"][0]))
                    return
                if url.path == "/api/problems":
                    from .problem_panels import state
                    self.send(200, state(app.repo, query['id'][0]))
                    return
                if url.path == "/api/prompt-import":
                    from .prompt_exchange import current_import
                    self.send(200, current_import(app.repo, query["id"][0]))
                    return
                if url.path == "/api/local-prompts":
                    from .local_prompts import current_draft
                    self.send(200, current_draft(app.repo, query["id"][0]))
                    return
                if url.path == "/api/trash":
                    self.send(200, app.trash_state())
                    return
                if url.path == "/api/input-drafts":
                    self.send(200, [{"id": f.stem, **e.read(f)} for f in sorted(
                        (app.repo.root.parent / "input-drafts").glob("*.json"), reverse=True)][:30])
                    return
                if url.path == "/api/workspace":
                    path = app.repo.folder(query["id"][0]) / "workspace.json"
                    self.send(200, e.read(path) if path.exists() else {})
                    return
                if url.path == "/api/change-items":
                    from .storyboard_tools import candidate_units

                    _, units = candidate_units(
                        app.repo, query["id"][0], query["candidate"][0]
                    )
                    self.send(200, units)
                    return
                if url.path == "/api/storyboard-bundles":
                    self.send(
                        200,
                        [
                            e.read(f)
                            for f in sorted(
                                (
                                    app.repo.folder(query["id"][0])
                                    / "storyboard-drafts"
                                ).glob("*/bundle.json"),
                                reverse=True,
                            )
                        ],
                    )
                    return
                if url.path == "/api/art-comparison":
                    folders = sorted(
                        (DATA / "test-results/art-style").glob("*/report.json"),
                        reverse=True,
                    )
                    self.send(
                        200,
                        [{"folder": f.parent.name, **e.read(f)} for f in folders[:5]],
                    )
                    return
                if url.path == "/api/art-image":
                    folder = safe_id(query["folder"][0])
                    raw_file = query["file"][0]
                    e.need(raw_file.endswith(".png"), "图片格式不符")
                    file = safe_id(raw_file[:-4]) + ".png"
                    path = DATA / "test-results/art-style" / folder / file
                    e.need(path.suffix == ".png" and path.is_file(), "图片不存在")
                    self.send(200, path.read_bytes(), "image/png")
                    return
                if url.path == "/api/characters":
                    from .clothing_policy import RULES
                    self.send(
                        200, {"characters": app.characters.searchable_listing(), "fields": DETAILS,
                              "clothing_policy": RULES}
                    )
                    return
                if url.path == "/api/hand-guide":
                    from .guided_hand import preview_reference
                    self.send(200,{**preview_reference(), 'local_repair_features': ['owner','outline','blend_mask']})
                    return
                if url.path == "/api/drawing-options":
                    from .comparison_models import available
                    from .hand_detail import capability as hand_detail_capability
                    selected_checkpoint = query.get("checkpoint", [None])[0]
                    project_id = query.get("id", [None])[0]
                    self.send(
                        200,
                        {
                            "comfy": comfyui.discover(checkpoint=selected_checkpoint, probe=False),
                            "art_presets": art_presets.PRESETS,
                            "prompt_characters": True,
                            "prompt_translate": True,
                            "hand_detail": hand_detail_capability(),
                            "comparison_models": available(),
                            "diffusion_models": {name: {"label": profile[0], "family": profile[1]} for name, profile in prompt_models.MODELS.items()},
                            "art_sample_folder": art_presets.SAMPLE_FOLDER,
                            "lora_labels": comfyui.lora_labels(),
                            "lora_inspection": inspect_local(checkpoint=selected_checkpoint),
                            "drawing_settings": drawing_settings.load(
                                app.repo, project_id
                            )
                            if project_id
                            else None,
                        },
                    )
                    return
                if url.path == "/api/character-image":
                    self.send(
                        200,
                        app.characters.image_path(query["id"][0]).read_bytes(),
                        "image/png",
                    )
                    return
                if url.path == "/api/character-history":
                    self.send(200, app.characters.history(query["name"][0]))
                    return
                if url.path == "/api/job":
                    self.send(200, app.jobs.get(query["id"][0]))
                    return
                if url.path == "/api/creative-drafts":
                    self.send(
                        200,
                        [
                            {"id": p.parent.name, **e.read(p)}
                            for p in sorted(
                                (DATA / "creative").glob("*/draft.json"),
                                key=lambda path: path.stat().st_mtime,
                                reverse=True,
                            )
                        ][:30],
                    )
                    return
                if url.path == "/api/bubble-plan":
                    from . import bubble_planner
                    self.send(200, bubble_planner.load(app.repo, query["id"][0]))
                    return
                if url.path == "/api/layout-presets":
                    p = app.repo.get(query["id"][0])
                    page = p["pages"][query["page"][0]]
                    self.send(
                        200,
                        {
                            k: layout.default_layout(page, k)
                            for k in ["story", "balanced", "hero", "cinema", "vertical"]
                        },
                    )
                    return
                if url.path == "/api/status":
                    self.send(
                        200, {"model": app.model.status(), "jobs": app.jobs.listing(),
                              "queue": app.night_queue.snapshot(),
                              "features": {"remaining_candidate_count": True,
                                           "candidate_count_total": True, "night_queue": True,
                                           "qwen_multi_reference_edit": True,
                                           "page_redraw_corrections": True,
                                           "automatic_context_cleanup": True,
                                           "problem_panel_review": True}}
                    )
                    return
                if url.path == "/api/review-statistics":
                    from .review_statistics import review_statistics

                    self.send(200, review_statistics(app.repo, query["id"][0]))
                    return
                if url.path == "/api/drawing":
                    from .comparison_models import available
                    pid = query["id"][0]
                    p = app.repo.get(pid)
                    self.send(
                        200,
                        {
                            "comfy": comfyui.discover(probe=False),
                            "comparison_models": available(),
                            "diffusion_models": {name: {"label": profile[0], "family": profile[1]} for name, profile in prompt_models.MODELS.items()},
                            "images": comfyui.drawing_state(app.repo, pid),
                            "drawing_settings": drawing_settings.load(app.repo, pid),
                            "art_preset": art_presets.get(
                                drawing_settings.load(app.repo, pid)
                            ),
                            "lora_labels": comfyui.lora_labels(),
                            "lora_inspection": inspect_local(),
                            "panel_hashes": {
                                panel["id"]: e.digest(panel)
                                for page in p["pages"].values()
                                for panel in page["panels"]
                            },
                            "layouts": {
                                k: layout.default_layout(v)
                                for k, v in p["pages"].items()
                            },
                        },
                    )
                    return
                if url.path in ["/api/image", "/api/layout"]:
                    pid = query["id"][0]
                    bundle = safe_id(query["bundle"][0])
                    name = query["file"][0]
                    allowed = (
                        [f"{prefix}{i}.png" for prefix in ("", "base-") for i in range(1, 17)]
                        if url.path == "/api/image"
                        else [
                            "page.png",
                            "page.svg",
                            "lettering.svg",
                            "layout.json",
                            "lettering.json",
                            "manifest.json",
                        ]
                    )
                    e.need(name in allowed, "未知图片或排版文件")
                    path = (
                        app.repo.folder(pid)
                        / ("images" if url.path == "/api/image" else "layouts")
                        / bundle
                        / name
                    )
                    self.send(
                        200,
                        path.read_bytes(),
                        mimetypes.guess_type(name)[0] or "application/octet-stream",
                    )
                    return
                if url.path == "/api/history":
                    path = (
                        app.repo.folder(query["id"][0])
                        / "history"
                        / (safe_id(query["revision"][0]) + ".json")
                    )
                    self.send(200, e.read(path))
                    return
                if url.path == "/api/export":
                    filename = query["file"][0]
                    e.need(
                        filename
                        in [
                            "script.md",
                            "script.json",
                            "characters.json",
                            "scenes.json",
                            "props.json",
                            "lettering.json",
                            "states.json",
                            "drawing-handoff.json",
                            "manifest.json",
                        ],
                        "无效导出文件",
                    )
                    path = (
                        app.repo.folder(query["id"][0])
                        / "exports"
                        / safe_id(query["bundle"][0])
                        / filename
                    )
                    self.send(200, path.read_bytes(), "text/plain; charset=utf-8")
                    return
                allowed = {
                    "/": "index.html",
                    "/app.js": "app.js",
                    "/drawing.js": "drawing.js",
                    "/image-size.js": "image-size.js",
                    "/studio-home.js": "studio-home.js",
                    "/prompt-exchange.js": "prompt-exchange.js",
                    "/problem-panels.js": "problem-panels.js",
                    "/panel-reader.js": "panel-reader.js",
                    "/story-import.js": "story-import.js",
                    "/model-downloads.js": "model-downloads.js",
                    "/direct-imports.js": "direct-imports.js",
                    "/imported-setup.js": "imported-setup.js",
                    "/loras.js": "loras.js",
                    "/creative.js": "creative.js",
                    "/prompt.js": "prompt.js",
                    "/clothing.js": "clothing.js",
                    "/style.css": "style.css",
                }
                e.need(url.path in allowed, "页面不存在")
                path = ROOT / "web" / allowed[url.path]
                self.send(
                    200,
                    path.read_bytes(),
                    mimetypes.guess_type(path)[0] + "; charset=utf-8",
                )
            except Exception as exc:
                self.send(400, {"error": str(exc)})

        def do_POST(self):
            try:
                self.check_host()
                origin = self.headers.get("Origin")
                e.need(
                    origin in (None, f"http://{self.headers['Host']}"), "拒绝跨站请求"
                )
                e.need(
                    secrets.compare_digest(
                        self.headers.get("X-Studio-Token", ""), app.token
                    ),
                    "请刷新本地界面后操作",
                )
                e.need(self.path == "/api/action", "未知接口")
                length = int(self.headers.get("Content-Length", "0"))
                e.need(0 < length <= 15_000_000, "请求大小无效")
                self.send(200, app.action(json.loads(self.rfile.read(length))))
            except Exception as exc:
                self.send(400, {"error": str(exc)})

    return Handler


def serve():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    cfg = settings()
    app = Application()
    server = ThreadingHTTPServer(("127.0.0.1", cfg["port"]), make_handler(app))
    atexit.register(app.model.stop)
    atexit.register(app.comfy.stop)
    url = f"http://127.0.0.1:{server.server_port}"
    print("本地漫画工作室：" + url, flush=True)
    if not args.no_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.night_queue.close()
        app.model.stop()
        app.comfy.stop()
        server.server_close()


def main():
    from .instance import InstanceLock

    try:
        with InstanceLock():
            serve()
    except RuntimeError as exc:
        print(str(exc), flush=True)
        import sys

        if "--no-browser" not in sys.argv:
            webbrowser.open(f"http://127.0.0.1:{settings()['port']}")


if __name__ == "__main__":
    main()
