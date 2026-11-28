"""Pure in-process studio application core (zero HTTP server, zero WebView)."""

from __future__ import annotations

import atexit
import json
import secrets
import threading
import time
import uuid
from pathlib import Path

from . import comfyui, creative, drawing_settings, engine as e, layout, prompt_models
from .characters import CharacterLibrary, DETAILS
from .jobs import Jobs
from .lora_inspection import inspect_local
from .model import ModelManager, generate
from .paths import DATA, ROOT, settings
from .repository import LOCK, Repository, safe_id


class UIStateStore:
    """Thread-safe native JSON persistence replacing browser localStorage."""

    def __init__(self, path: Path | None = None):
        self.path = path or (DATA / "app-state.json")
        self._lock = threading.RLock()
        self._data: dict = self._load()

    def _load(self) -> dict:
        try:
            if self.path.is_file():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    return raw
        except Exception:
            pass
        return {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        e.save(self.path, self._data)

    def get(self, key: str, default=None):
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        with self._lock:
            if self._data.get(key) == value:
                return
            self._data[key] = value
            self._save()

    def lora_weights(self) -> dict[str, float]:
        with self._lock:
            raw = self._data.get("lora_weights", {})
            return dict(raw) if isinstance(raw, dict) else {}

    def remember_lora_weight(self, file: str, weight: float) -> None:
        key = str(file or "").replace("\\", "/")
        if not key or not (0 <= float(weight) <= 2):
            return
        with self._lock:
            weights = self._data.setdefault("lora_weights", {})
            if not isinstance(weights, dict):
                weights = {}
                self._data["lora_weights"] = weights
            val = round(float(weight), 4)
            if weights.get(key) != val:
                weights[key] = val
                self._save()

    def favorite_loras(self) -> list[str]:
        with self._lock:
            raw = self._data.get("favorite_loras", [])
            if not isinstance(raw, list):
                return []
            seen: set[str] = set()
            out: list[str] = []
            for item in raw:
                key = str(item or "").replace("\\", "/").strip()
                if key and key not in seen:
                    seen.add(key)
                    out.append(key)
            return out

    def is_lora_favorite(self, file: str) -> bool:
        key = str(file or "").replace("\\", "/").strip()
        if not key:
            return False
        return key in self.favorite_loras()

    def set_favorite_lora(self, file: str, favorite: bool) -> bool:
        key = str(file or "").replace("\\", "/").strip()
        if not key:
            return False
        with self._lock:
            favs = self.favorite_loras()
            if favorite:
                favs = [key, *[x for x in favs if x != key]]
            else:
                favs = [x for x in favs if x != key]
            if self._data.get("favorite_loras") != favs:
                self._data["favorite_loras"] = favs
                self._save()
            return bool(favorite)

    def toggle_favorite_lora(self, file: str) -> bool:
        key = str(file or "").replace("\\", "/").strip()
        if not key:
            return False
        with self._lock:
            is_fav = key in self.favorite_loras()
            return self.set_favorite_lora(key, not is_fav)

    def prompt_draft(self, key: str = "new") -> dict:
        with self._lock:
            drafts = self._data.get("prompt_drafts", {})
            if isinstance(drafts, dict) and isinstance(drafts.get(key), dict):
                return dict(drafts[key])
            return {}

    def save_prompt_draft(self, key: str, draft: dict) -> None:
        with self._lock:
            drafts = self._data.setdefault("prompt_drafts", {})
            if not isinstance(drafts, dict):
                drafts = {}
                self._data["prompt_drafts"] = drafts
            drafts[key] = dict(draft)
            self._save()


class StudioApp:
    """In-process desktop studio controller. Called directly by the PySide6 UI."""

    def __init__(self, repo: Repository | None = None, *, keep_alive_comfy: bool = True):
        self.repo = repo or Repository()
        from .model_downloads import ModelDownloads

        self.model_downloads = ModelDownloads()
        self.jobs = Jobs(
            on_pause=self.release_paused_models,
            on_finish=self.clear_finished_context,
        )
        from .storyboard_tools import restore_jobs

        restore_jobs(self.repo, self.jobs)
        self.model = ModelManager()
        self.pending_idle_worker = None
        self.idle_release_timer = None
        self.ui_state = UIStateStore(self.repo.root.parent / "app-state.json")
        saved_keep_alive = self.ui_state.get("comfy_keep_alive")
        effective_keep_alive = keep_alive_comfy if saved_keep_alive is None else bool(saved_keep_alive)
        self.comfy = comfyui.ComfyManager(
            keep_alive=effective_keep_alive,
            session_dir=self.repo.root.parent,
        )
        self.token = secrets.token_urlsafe(32)
        self.characters = CharacterLibrary(self.repo.root.parent / "characters")
        from .night_queue import NightQueue

        self.night_queue = NightQueue(self)
        self._cache_lock = threading.Lock()
        self._warmed = False
        self._shutdown_done = False
        atexit.register(self.shutdown)

    def warmup_caches_async(self) -> None:
        """Preload disk-cached LoRA metadata and character index off the UI thread."""
        with self._cache_lock:
            if self._warmed:
                return
            self._warmed = True

        def _worker():
            try:
                comfyui.discover(probe=False)
                inspect_local()
                self.characters.searchable_listing()
            except Exception:
                pass

        t = threading.Thread(target=_worker, name="studio-warmup", daemon=True)
        t.start()

    def has_active_work(self) -> bool:
        with self.jobs.lock:
            if self.jobs.controls:
                return True
        if any(item.get("status") == "running" for item in self.model_downloads.snapshot().get("items", [])):
            return True
        return False

    def shutdown(self, *, keep_comfy: bool | None = None, interrupt_active: bool = False) -> None:
        if self._shutdown_done:
            return
        self._shutdown_done = True
        try:
            self.night_queue.stop_event.set()
        except Exception:
            pass
        if self.idle_release_timer:
            try:
                self.idle_release_timer.cancel()
            except Exception:
                pass
            self.idle_release_timer = None
        if interrupt_active and self.comfy.is_running():
            try:
                comfyui.api("/interrupt", {}, timeout=1.5)
            except Exception:
                pass
        keep = bool(self.comfy.keep_alive) if keep_comfy is None else bool(keep_comfy)
        try:
            if keep:
                self.comfy.detach()
            else:
                self.comfy.shutdown()
        except Exception:
            pass
        try:
            self.model.stop()
        except Exception:
            pass
        try:
            self.repo._temporary.cleanup()
        except Exception:
            pass

    def clear_finished_context(self, job):
        if job["label"] == "卸载绘图模型":
            return None
        if job["label"] in {"启动语言模型", "启动 FlashNext", "启动分析模型"}:
            self.pending_idle_worker = None
            return None
        if not self.model.owns_live_worker():
            return {"status": "already_unloaded"}
        with self.night_queue.lock, self.jobs.lock:
            newer_jobs = any(other != job.get("id") for other in self.jobs.controls)
            waiting = any(
                item["status"] in {"waiting", "running"}
                and (item["status"] == "waiting" or item.get("job") != job.get("id"))
                for item in self.night_queue.state["items"]
            )
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
            if (
                self.pending_idle_worker is worker
                and self.model.owned_worker() is worker
                and self.model.owns_live_worker()
            ):
                self.clear_finished_context({"label": "队列已更新"})
        finally:
            GENERATION_LOCK.release()

    def release_drawing_cache(self):
        if comfyui.discover()["ready"]:
            queue = comfyui.api("/queue")
            e.need(
                not queue.get("queue_running") and not queue.get("queue_pending"),
                "绘图进行中，请等待完成",
            )
            if self.comfy.process is not None and self.comfy.process.poll() is None:
                comfyui.api("/free", {"unload_models": True, "free_memory": True})
                self.comfy.process.preloaded_checkpoint = None
                self.comfy.save_session()

    def release_paused_models(self):
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
                item["drawing_states"] = sorted(
                    {
                        b.get("status", "unknown")
                        for b in batches
                        if b.get("status") in {"running", "queued", "submitted", "uncertain"}
                    }
                )
        return result

    def release_prompt(self, pid):
        if pid not in self.repo.transient_ids:
            return
        if any(c["job"].get("project") == pid for c in self.jobs.controls.values()):
            timer = threading.Timer(2, self.release_prompt, args=(pid,))
            timer.daemon = True
            timer.start()
            return
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

    # ---------- Direct In-Process Queries (replacing HTTP GET /api/*) ----------

    def bootstrap(self) -> dict:
        return {
            "schemas": e.SCHEMAS,
            "template": e.read(ROOT / "config/template.json"),
            "projects": self.repo.listing(),
            "jobs": self.jobs.listing(),
        }

    def status(self) -> dict:
        return {
            "model": self.model.status(),
            "jobs": self.jobs.listing(),
            "queue": self.night_queue.snapshot(),
            "drawing_model": self.drawing_model_snapshot(),
            "features": {
                "remaining_candidate_count": True,
                "candidate_count_total": True,
                "night_queue": True,
                "qwen_multi_reference_edit": True,
                "page_redraw_corrections": True,
                "automatic_context_cleanup": True,
                "problem_panel_review": True,
            },
        }

    def remember_checkpoint(self, checkpoint: str | None) -> str | None:
        cp = str(checkpoint or "").strip()
        if not cp:
            return None
        ui_state = getattr(self, "ui_state", None)
        if ui_state is not None:
            ui_state.set("last_checkpoint", cp)
        return cp

    def preferred_checkpoint(self, available: list[str] | None = None, *, exclude_edit: bool = False) -> str:
        from .qwen_edit import CHECKPOINT as edit_checkpoint

        pool = list(available) if available is not None else []
        if exclude_edit:
            pool = [c for c in pool if c != edit_checkpoint]

        candidates = [
            self.comfy.preloaded,
            self.ui_state.get("last_checkpoint"),
            (self.ui_state.prompt_draft("new") or {}).get("checkpoint"),
            comfyui.config().get("checkpoint"),
        ]
        for cand in candidates:
            c = str(cand or "").strip()
            if not c or (exclude_edit and c == edit_checkpoint):
                continue
            if not pool or c in pool:
                return c
        return pool[0] if pool else ""

    def drawing_model_snapshot(self) -> dict:
        from .drawing_models import snapshot

        snap = snapshot(self)
        models = snap.get("models") or []
        available = [m["value"] for m in models]
        pref = self.preferred_checkpoint(available)
        label_map = {m["value"]: m["label"] for m in models}
        snap["preferred"] = pref
        snap["preferred_label"] = label_map.get(pref, pref)
        return snap

    def drawing_options(self, checkpoint=None, project_id=None, for_import=False) -> dict:
        from .anima_consistency import previous_panel_unavailable
        from . import second_pass

        comfy_info = comfyui.discover(checkpoint=checkpoint, probe=False)
        preloaded = self.comfy.preloaded
        preferred = self.preferred_checkpoint(comfy_info.get("checkpoints") or [], exclude_edit=for_import)
        selected_checkpoint = checkpoint
        if for_import and not selected_checkpoint:
            selected_checkpoint = preferred or None
        if selected_checkpoint and selected_checkpoint != checkpoint:
            comfy_info = comfyui.discover(checkpoint=selected_checkpoint, probe=False)
        label_keys = (
            "name",
            "character_id",
            "character",
            "weight",
            "trigger_words",
            "appearance_prompt",
            "source_trained_words",
            "confirmed",
            "preferred",
        )
        compact_labels = {
            file: {k: v for k, v in info.items() if k in label_keys}
            for file, info in comfyui.lora_labels().items()
        }
        return {
            "comfy": comfy_info,
            "preloaded_checkpoint": preloaded,
            "preferred_checkpoint": preferred,
            "selected_checkpoint": selected_checkpoint,
            "previous_panel_unavailable": previous_panel_unavailable(
                selected_checkpoint or preferred or comfyui.config()["checkpoint"]
            ),
            "prompt_characters": True,
            "prompt_translate": True,
            "diffusion_models": {
                name: {
                    "label": profile[0],
                    "family": profile[1],
                    "steps": profile[2],
                    "sampler_name": profile[4],
                    "scheduler": profile[5],
                }
                for name, profile in prompt_models.MODELS.items()
            },
            "upscale_models": second_pass.available_upscalers(),
            "lora_labels": compact_labels,
            "lora_inspection": inspect_local(checkpoint=selected_checkpoint),
            "drawing_settings": drawing_settings.load(self.repo, project_id) if project_id else None,
        }

    def characters_data(self, compact: bool = True) -> dict:
        from .clothing_policy import RULES

        cards = self.characters.searchable_listing()
        if compact:
            cards = [
                {
                    "id": c["id"],
                    "name": c["name"],
                    "revision": c["revision"],
                    "image_id": c.get("image_id"),
                    "profile": c["profile"],
                    "pinyin": c.get("pinyin", ""),
                    "english_aliases": c.get("english_aliases", []),
                    "search_terms": c.get("search_terms", []),
                    "source": {
                        k: c.get("source", {})[k]
                        for k in ("aliases", "drawing_prompt_cleanup")
                        if k in c.get("source", {})
                    },
                }
                for c in cards
            ]
        return {"characters": cards, "fields": DETAILS, "clothing_policy": RULES}

    def character_image_path(self, image_id: str) -> Path:
        return self.characters.image_path(image_id)

    def drawing_state(self, pid: str) -> dict:
        p = self.repo.get(pid)
        return {
            "comfy": comfyui.discover(probe=False),
            "diffusion_models": {
                name: {
                    "label": profile[0],
                    "family": profile[1],
                    "steps": profile[2],
                    "sampler_name": profile[4],
                    "scheduler": profile[5],
                }
                for name, profile in prompt_models.MODELS.items()
            },
            "images": comfyui.drawing_state(self.repo, pid),
            "drawing_settings": drawing_settings.load(self.repo, pid),
            "lora_labels": comfyui.lora_labels(),
            "lora_inspection": inspect_local(),
            "panel_hashes": {
                panel["id"]: e.digest(panel)
                for page in p["pages"].values()
                for panel in page["panels"]
            },
            "layouts": {k: layout.default_layout(v) for k, v in p["pages"].items()},
        }

    def images_state(self, pid: str) -> dict:
        return {"images": comfyui.drawing_state(self.repo, pid)}

    def image_file_path(self, pid: str, rel_file: str) -> Path:
        return self.repo.folder(pid) / "images" / rel_file

    def layout_file_path(self, pid: str, bundle: str, name: str) -> Path:
        return self.repo.folder(pid) / "layouts" / safe_id(bundle) / name

    def prompt_import_state(self, pid: str) -> dict:
        from .prompt_exchange import current_import

        return current_import(self.repo, pid)

    def local_prompts_state(self, pid: str) -> dict:
        from .local_prompts import current_draft

        return current_draft(self.repo, pid)

    def problems_state(self, pid: str) -> dict:
        from .problem_panels import state

        return state(self.repo, pid)

    def board_layout_state(self, pid: str) -> dict:
        from .prompt_board import page_state

        return page_state(self.repo, pid)

    def creative_drafts(self) -> list[dict]:
        return [
            {"id": p.parent.name, **e.read(p)}
            for p in sorted(
                (DATA / "creative").glob("*/draft.json"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        ][:30]

    def layout_presets(self, pid: str, page_id: str) -> dict:
        p = self.repo.get(pid)
        page = p["pages"][page_id]
        return {
            k: layout.default_layout(page, k)
            for k in ["story", "balanced", "hero", "cinema", "vertical"]
        }

    def wardrobe_catalog(self) -> dict:
        from .wardrobe import catalog

        return catalog()

    def export_storyboard(self, pid: str) -> dict:
        from .prompt_exchange import export_download

        return export_download(self.repo, pid)

    # ---------- Action Dispatcher ----------

    def action(self, a: dict):
        action = a["action"]
        pid = a.get("project")
        if action == "wardrobe_preview":
            from .wardrobe import preview

            return preview(a)
        if action in {"generate_prompt_board", "confirm_prompt_board"}:
            from . import prompt_board

            if action == "generate_prompt_board":
                return prompt_board.generate(self, a)
            return prompt_board.confirm(self, a.get("job"))
        if action in {
            "create_prompt_board",
            "draw_board_panel",
            "queue_board_panel",
            "unselect_board_image",
            "compose_board_page",
        }:
            from . import prompt_board

            if action == "create_prompt_board":
                return prompt_board.create(self, a)
            if action == "unselect_board_image":
                return prompt_board.unselect(self.repo, pid, a.get("target"), a.get("image"))
            if action == "compose_board_page":
                return prompt_board.compose(self.repo, pid, a)
            return prompt_board.submit(self, pid, a, action == "queue_board_panel")
        if action in {"fusion_source", "save_prompt_fusion"}:
            from . import task_fusion

            if action == "fusion_source":
                return task_fusion.source(self, a.get("kind"), a.get("source_id"))
            return task_fusion.save(self, a)
        if action in {"check_update", "open_releases"}:
            from . import updates

            return updates.check(a.get("token", "")) if action == "check_update" else updates.open_releases()
        if action == "download_model_asset":
            return self.model_downloads.start(a.get("asset"))
        if action == "set_preferred_checkpoint":
            cp = self.remember_checkpoint(a.get("checkpoint"))
            return {"checkpoint": cp or self.preferred_checkpoint()}
        if action in {
            "preload_drawing_model",
            "draw_quick_sample",
            "queue_prompt",
            "draw_board_panel",
            "queue_board_panel",
            "draw_imported",
        } and a.get("checkpoint"):
            self.remember_checkpoint(a.get("checkpoint"))
        if action == "save_drawing_settings" and isinstance(a.get("settings"), dict) and a["settings"].get("checkpoint"):
            self.remember_checkpoint(a["settings"].get("checkpoint"))
        if action in {"preload_drawing_model", "unload_drawing_model"}:
            from . import drawing_models

            if action == "preload_drawing_model":
                cp = a.get("checkpoint") or self.preferred_checkpoint()
                self.remember_checkpoint(cp)
                return {
                    "job": self.jobs.submit(
                        "加载绘图模型",
                        lambda update, jid: drawing_models.preload(self, cp, update, jid),
                    )
                }
            return {
                "job": self.jobs.submit(
                    "卸载绘图模型",
                    lambda update, jid: drawing_models.unload(self),
                )
            }
        if action == "open_model_folder":
            return self.model_downloads.open_folder(a.get("asset"))
        if action == "open_drawing_model_folder":
            from . import drawing_models

            return drawing_models.open_folder(a.get("checkpoint"))
        if action == "open_lora_folder":
            from . import lora_folders

            return lora_folders.open_folder(a.get("checkpoint"), a.get("folder"))
        if action in {"analysis_model", "set_analysis_model"}:
            from .analysis_models import choices, select_model

            return choices() if action == "analysis_model" else select_model(a.get("model"), self.model)
        if action in {
            "start_queue",
            "pause_queue",
            "remove_queue_item",
            "reorder_queue",
            "retry_queue_item",
            "clear_queue_history",
            "rename_queue_item",
        }:
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
                return self.night_queue.add(
                    queued_pid,
                    label or project["brief"]["title"],
                    {
                        "action": "prompt_batch",
                        "base_hash": e.digest(project),
                        "run_key": queued_pid,
                        "checkpoint": project["prompt_input"]["checkpoint"],
                        "candidate_count": project["prompt_input"]["count"],
                    },
                )
        if action in {"queue_drawing", "queue_imported"}:
            if action == "queue_drawing":
                from .manual_drawing import prepare_request
            else:
                from .prompt_exchange import prepare_request
            with self.night_queue.lock:
                self.night_queue.ensure_capacity()
                label = self.night_queue.custom_label(a.get("task_name"))
                request = prepare_request(self.repo, pid, a)
                return self.night_queue.add(
                    pid,
                    label or self.repo.get(pid)["brief"]["title"] + " · 出图",
                    request,
                )
        if action == "set_remaining_candidate_count":
            from .drawing_quantity import change_remaining_count

            saved = self.jobs.get(a["job"])
            e.need(pid == saved.get("project"), "任务不属于当前作品")
            return self.jobs.update_paused_request(
                a["job"],
                lambda request: change_remaining_count(
                    request,
                    comfyui.drawing_state(self.repo, pid)["batches"],
                    a.get("candidate_count"),
                    a.get("quantity_mode", "remaining"),
                ),
            )
        if action in {
            "preview_direct_story",
            "save_direct_story",
            "preview_direct_prompts",
            "save_direct_prompts",
        }:
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
        if action == "delete_gallery_image":
            from .image_delete_cache import delete_image

            return delete_image(
                self,
                pid,
                a.get("image_id"),
                delete_variants=bool(a.get("delete_variants", True)),
            )
        if action == "undo_delete_gallery_image":
            from .image_delete_cache import undo_delete_image

            return undo_delete_image(self, pid)
        if action == "clear_generated_images":
            from .drawing_reset import clear_images

            return clear_images(self, pid, a.get("base_hash"))
        if action in {"pause_job", "resume_job", "cancel_job", "retry_job"}:
            saved = self.jobs.get(a["job"])
            if action == "retry_job":
                e.need(saved["status"] == "failed", "只能重新生成已出错的任务")
                e.need(
                    saved.get("request", {}).get("action") == "storyboard_all",
                    "此按钮仅用于重新生成出错的分镜任务",
                )
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

                    return understand_and_redraw(self, saved["project"], request["document"], update, jid)
                if request.get("action") == "understand_problems":
                    from .problem_panels import understand

                    return understand(self, saved["project"], request["document"], update, jid)
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

            return self.jobs.control(
                a["job"],
                action.removesuffix("_job"),
                confirm=a.get("confirm", False),
                resume_fn=resume,
            )
        if action in {
            "save_problem",
            "export_problems",
            "import_problems",
            "understand_problems",
            "understand_and_redraw",
            "redraw_problems",
        }:
            from . import problem_panels

            if action == "save_problem":
                return problem_panels.save(self.repo, pid, a)
            if action == "export_problems":
                return problem_panels.export(self.repo, pid)
            if action == "import_problems":
                return problem_panels.import_result(self.repo, pid, a.get("document"))
            if action in {"understand_problems", "understand_and_redraw"}:
                document = problem_panels.export(self.repo, pid)
                request = {"action": action, "document": document}
                if action == "understand_and_redraw":
                    return {
                        "job": self.jobs.submit(
                            "按问题说明统一重画",
                            lambda update, jid: problem_panels.understand_and_redraw(
                                self, pid, document, update, jid
                            ),
                            pid,
                            request=request,
                        )
                    }
                return {
                    "job": self.jobs.submit(
                        "理解问题格子 · FlashNext",
                        lambda update, jid: problem_panels.understand(self, pid, document, update, jid),
                        pid,
                        request=request,
                    )
                }
            from .prompt_exchange import draw_imported

            request = problem_panels.prepare_drawing(self.repo, pid)
            return {
                "job": self.jobs.submit(
                    "重画全部问题格子",
                    lambda update, jid: draw_imported(self, pid, request, update, jid),
                    pid,
                    request=request,
                )
            }
        if action == "fill_local_prompts":
            from . import local_prompts

            document = local_prompts.export_storyboard(self.repo, pid)
            request = {
                "action": action,
                "draft_id": uuid.uuid4().hex,
                "storyboard_hash": document["storyboard_hash"],
            }
            return {
                "job": self.jobs.submit(
                    "本地 Qwen 填写提示词",
                    lambda update, jid: local_prompts.generate_draft(self, pid, request, update, jid),
                    pid,
                    request=request,
                )
            }
        if action in {"preview_prompt_import", "save_prompt_import", "draw_imported"}:
            from . import prompt_exchange

            if action == "preview_prompt_import":
                return prompt_exchange.preview(self.repo, pid, a.get("document"))
            if action == "save_prompt_import":
                return prompt_exchange.save_import(self.repo, pid, a)
            request = prompt_exchange.prepare_request(self.repo, pid, a)
            return {
                "job": self.jobs.submit(
                    "按导入提示词逐格绘制",
                    lambda update, jid: prompt_exchange.draw_imported(self, pid, request, update, jid),
                    pid,
                    request=request,
                )
            }
        if action == "release_prompt":
            self.release_prompt(pid)
            return {"released": True}
        if action == "create_quick_sample":
            from .quick_sample import create

            return create(self.repo, a)
        if action == "draw_quick_sample":
            from .quick_sample import draw_session

            return draw_session(self, pid, a)
        if action == "second_pass":
            from . import second_pass

            second_pass.validate_request(self.repo, pid, a)
            return {
                "job": self.jobs.submit(
                    "二次采样",
                    lambda update, jid: second_pass.run(self, pid, a, update, jid),
                    pid,
                )
            }
        if action == "apply_second_pass":
            from . import second_pass

            return second_pass.apply_result(self.repo, pid, a["source_image_id"], a["image_id"])
        if action == "unapply_second_pass":
            from . import second_pass

            return second_pass.unapply_result(self.repo, pid, a["source_image_id"])
        if action == "prompt_batch":
            from .quick_sample import run

            return {"job": self.jobs.submit("提示词出图", lambda update, jid: run(self, pid, a, update, jid), pid)}
        if action == "prompt_translate":
            from .quick_sample import translate

            return {
                "job": self.jobs.submit(
                    "转换英文绘图提示词",
                    lambda update, jid: translate(self, a, update, jid),
                    transient=True,
                )
            }
        if action == "new_workspace":
            with LOCK:
                path = self.repo.folder("new") / "workspace.json"
                previous = e.read(path) if path.exists() else {}
                local = a.get("previous_state") or {}
                if local.get("updated", 0) >= previous.get("updated", 0):
                    previous = local
                result = a.get("draft_result")
                if previous or result:
                    e.save(
                        self.repo.root.parent / "input-drafts" / (e.stamp() + ".json"),
                        {"state": previous, "result": result},
                    )
                state = {
                    "stage": "script",
                    "target": "",
                    "views": {
                        "script:": {
                            "fields": {
                                "story-text": "",
                                "story-preferences": "",
                                "story-mode": "structure",
                            },
                            "scroll": 0,
                        }
                    },
                    "updated": max(int(time.time() * 1000), previous.get("updated", 0) + 1),
                }
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
            with LOCK:
                path = self.repo.folder(pid) / "workspace.json"
                previous = e.read(path) if path.exists() else {}
                e.need(a["state"].get("updated", 0) >= previous.get("updated", 0), "已有更新的编辑记录")
                archive_time = previous.get("archived_at", 0)
                if previous and time.time() - archive_time >= 60:
                    e.save(path.parent / "workspace-history" / (e.stamp() + ".json"), previous)
                    archive_time = time.time()
                e.save(path, {**a["state"], "archived_at": archive_time})
            return {"saved": True}
        if action == "character_upload":
            return self.characters.upload(a["data_url"])
        if action == "save_lora_labels":
            return comfyui.save_lora_labels(a["items"])
        if action == "save_drawing_settings":
            return drawing_settings.save(self.repo, pid, a["settings"])
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
                fn = creative.character_draft if action == "character_draft" else creative.story_draft
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
                    update("正在自动分页")
                    payload, metrics = generate(
                        p,
                        "plan",
                        "",
                        "按已确认剧本安排大小不同的格子。",
                        self.repo.folder(pid) / "runs" / jid / "plan",
                        update,
                    )
                    c = self.repo.propose(pid, "plan", "", payload, e.digest(p), "已确认剧本的自动分页", metrics)
                    p = self.repo.accept(pid, c["id"])
                target = a.get("target") or next(
                    (x["id"] for x in p["plan"]["pages"] if x["id"] not in p["pages"]),
                    p["plan"]["pages"][0]["id"],
                )
                stage = "panel" if "-K" in target else "page"
                update({"message": f"正在编写 {target} 的画面与对白", "progress": {"completed": 1, "total": 3, "unit": "步骤"}})
                payload, metrics = generate(
                    p, stage, target, a.get("instruction", ""), self.repo.folder(pid) / "runs" / jid / target, update
                )
                proposed = e.merged(p, stage, target, payload)
                try:
                    update({"message": "正在自动检查分镜连续性与对白", "progress": {"completed": 2, "total": 3, "unit": "步骤"}})
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
                    metrics["continuity_review"] = {"verdict": "unavailable", "reason": str(exc)}
                c = self.repo.propose(pid, stage, target, payload, e.digest(p), "按确认剧本生成分镜", metrics)
                update({"message": "分镜草稿已生成，等待你确认", "progress": {"completed": 3, "total": 3, "unit": "步骤"}})
                return {"candidate": c["id"], "target": target}

            return {"job": self.jobs.submit("生成文字分镜", storyboard, pid)}
        if action == "create":
            brief = self.characters.freeze_brief(a["brief"]) if a.get("brief") else None
            return {"project": self.repo.create(brief, a.get("sample", False))}
        if action == "save_draft":
            self.repo.draft(pid, a["stage"], a.get("target", ""), a["payload"], a["base_hash"])
            return {"saved": True}
        if action == "propose":
            if a["stage"] == "brief":
                a["payload"] = self.characters.freeze_brief(a["payload"])
            return self.repo.propose(pid, a["stage"], a.get("target", ""), a["payload"], a["base_hash"])
        if action == "accept":
            return self.repo.accept(pid, a["candidate"])
        if action == "restore":
            return self.repo.restore(pid, a["revision"], a["base_hash"])
        if action == "fork":
            return {"project": self.repo.fork(pid, a["reset_from"])}
        if action == "export":
            return self.repo.export(pid)
        if action == "validate":
            return {"warnings": e.validate_project(self.repo.get(pid), a.get("complete", False))}
        if action == "start_model":
            return {"job": self.jobs.submit("启动分析模型", lambda update, jid: self.prepare_language(update=update))}
        if action == "stop_model":
            from .model import GENERATION_LOCK

            e.need(GENERATION_LOCK.acquire(blocking=False), "请等待当前任务结束后卸载模型")
            try:
                result = self.model.unload_if_idle()
                e.need(result["status"] in {"unloaded", "already_unloaded"}, result.get("reason", "模型仍有请求"))
                return self.model.status()
            finally:
                GENERATION_LOCK.release()
        if action == "audit_candidate":
            p = self.repo.get(pid)
            e.need(e.digest(p) == a["base_hash"], "作品已变化，请刷新")
            c = next((c for c in self.repo.candidates(pid) if c["id"] == a["candidate"]), None)
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
                return {"candidate": revised["id"], "continuity_review": audit}

            return {"job": self.jobs.submit("补做连续性审核", audit_candidate, pid)}
        if action == "generate":
            p = self.repo.get(pid)
            e.need(e.digest(p) == a["base_hash"], "项目已变化，请刷新")
            e.need(
                self.model.owns_live_worker() and self.model.status()["ready"],
                "工作室自己的分析模型尚未就绪，请先点击启动分析模型",
            )

            def run_gen(update, jid):
                e.need(
                    self.model.owns_live_worker() and self.model.status()["ready"],
                    "分析模型已释放，请重新启动后再提交",
                )
                drawing_kwargs = {}
                if a["stage"] == "drawing_prompt":
                    prefs = drawing_settings.load(self.repo, pid)
                    drawing_kwargs["drawing_checkpoint"] = (prefs or {}).get("checkpoint") or comfyui.config()["checkpoint"]
                with self.model.inference_context():
                    payload, metrics = generate(
                        p,
                        a["stage"],
                        a.get("target", ""),
                        a.get("instruction", ""),
                        self.repo.folder(pid) / "runs" / jid,
                        update,
                        **drawing_kwargs,
                    )
                return self.repo.propose(
                    pid,
                    a["stage"],
                    a.get("target", ""),
                    payload,
                    e.digest(p),
                    "本地模型生成",
                    metrics,
                )

            return {"job": self.jobs.submit("生成 " + a["stage"], run_gen, pid)}
        if action == "set_comfy_keep_alive":
            enabled = bool(a.get("enabled", True))
            self.comfy.keep_alive = enabled
            self.ui_state.set("comfy_keep_alive", enabled)
            return {"keep_alive": enabled}
        if action == "start_comfy":
            return {"job": self.jobs.submit("启动绘图引擎", lambda update, jid: self.comfy.start())}
        if action in {"redraw_page", "redraw_panel"}:
            from .page_redraw import prepare
            from .prompt_exchange import draw_imported

            request = prepare(self.repo, pid, a, single_panel=action == "redraw_panel")
            label = "重画当前格" if action == "redraw_panel" else "重画当前页"
            return {
                "job": self.jobs.submit(
                    label + (" · 按问题修改" if request["revision_note"] else ""),
                    lambda update, jid: draw_imported(self, pid, request, update, jid),
                    pid,
                    request=request,
                )
            }
        if action == "preview_drawing":
            from .manual_drawing import preview

            return preview(self.repo, pid, a)
        if action in {"draw", "draw_all"}:
            from .manual_drawing import prepare_request
            from .prompt_exchange import draw_imported

            request = prepare_request(
                self.repo,
                pid,
                {**a, **({"targets": [a["target"]], "force": True} if action == "draw" else {})},
            )
            return {
                "job": self.jobs.submit(
                    "按确认提示词出图 · 自己选图",
                    lambda update, jid: draw_imported(self, pid, request, update, jid),
                    pid,
                    request=request,
                )
            }
        if action == "select_image":
            index = comfyui.select_image(
                self.repo,
                pid,
                a["target"],
                a["image"],
                a.get("allow_rejected", False),
                a.get("allow_unreviewed", False),
            )
            return {"saved": True} if a.get("compact") else index
        if action == "compose":
            return layout.compose(self.repo, pid, a["target"], a["layout"])
        if action == "plan_bubbles":
            e.need(a.get("use_model") is True, "气泡规划会使用本地模型")
            self.repo.get(pid)

            def plan_bubbles(update, jid):
                from . import bubble_planner

                self.prepare_language(update=update)
                return bubble_planner.generate(
                    self.repo, pid, self.repo.folder(pid) / "runs" / jid, update, force=a.get("force") is True
                )

            return {"job": self.jobs.submit("AI 逐句选择气泡", plan_bubbles, pid)}
        raise ValueError("未知操作：" + str(action))

    def snapshot(self, pid: str) -> dict:
        p = self.repo.get(pid)
        return {
            "project": p,
            "hash": e.digest(p),
            "candidates": self.repo.candidates(pid),
            "drafts": self.repo.drafts(pid),
            "history": self.repo.history(pid),
            "warnings": e.validate_project(p),
        }
