"""ComfyUI API adapter, durable image jobs and explicit image selection."""

import copy
import json
import secrets
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from .installed_assets import models as model_directory
from . import engine as e
from .model import request_json
from .paths import ROOT, DATA, local_path
from .repository import LOCK, safe_id
from .processes import AttachedProcess, attach_exit_job, close_job, find_listening_pid, is_pid_running, terminate_tree
from .telemetry import ComfyProgress, emit
from . import prompt_models

_LORA_ALIASES_CACHE = (None, None, {})


def config():
    return e.read(ROOT / "config/comfyui.json")


# A model process started by another studio is never a target for this copy.
_private_process = None
_verified_process = None


def _verify_service(url):
    global _verified_process
    process = _private_process
    e.need(process is not None and process.poll() is None, "此副本尚未启动绘图服务")
    if _verified_process is process:
        return
    stats = request_json(url + "/system_stats", timeout=2)
    argv = stats.get("system", {}).get("argv", [])
    marker = str(DATA / "comfy-user")
    e.need("--user-directory" in argv and
           argv[argv.index("--user-directory") + 1:argv.index("--user-directory") + 2] == [marker],
           "该端口不是此副本的绘图服务")
    _verified_process = process


def api(path, body=None, timeout=10):
    cfg = config()
    url = urllib.parse.urlparse(cfg["endpoint"])
    e.need(
        url.scheme == "http" and url.hostname in ("127.0.0.1", "localhost"),
        "ComfyUI 只支持本机接口",
    )
    if cfg.get("private_service"):
        _verify_service(cfg["endpoint"])
    return request_json(cfg["endpoint"] + path, body, timeout)


def lora_file_key(filename):
    """Keep portable LoRA identities separate from ComfyUI's native filenames."""
    portable = filename.replace("\\", "/")
    return lora_path_aliases().get(portable, portable)


def lora_path_aliases():
    global _LORA_ALIASES_CACHE
    path = DATA / "lora-curation/rename-result.json"
    if not path.exists():
        return {}
    changed = path.stat().st_mtime_ns
    if _LORA_ALIASES_CACHE[:2] != (path, changed):
        mapping = {old.replace("\\", "/"): new.replace("\\", "/")
                   for old, new in e.read(path).get("mapping", {}).items()}
        _LORA_ALIASES_CACHE = (path, changed, mapping)
    return _LORA_ALIASES_CACHE[2]


def discover(checkpoint=None, *, probe=True):
    from .lora_folders import matches, directory
    def in_folder(name):
        if checkpoint is None:
            return True
        folder = directory(checkpoint)
        if (model_directory(ROOT) / "loras" / folder).is_dir():
            return name.startswith(folder + "/")
        return matches(name, checkpoint)
    try:
        if not probe:
            raise ConnectionError("仅列出本地模型文件")
        stats = api("/system_stats", timeout=2)
        info = api("/object_info/CheckpointLoaderSimple")
        names = info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
        from .prompt_models import installed
        names = [*installed(), *names]
        loras = api("/object_info/LoraLoader")["LoraLoader"]["input"]["required"][
            "lora_name"
        ][0]
        lora_files = {lora_file_key(name): name for name in loras
                      if in_folder(lora_file_key(name))}
        return {
            "ready": True,
            "checkpoints": names,
            "loras": list(lora_files),
            "lora_files": lora_files,
            "lora_path_aliases": lora_path_aliases(),
            "lora_directory": directory(checkpoint or config()["checkpoint"]),
            "stats": stats,
            "configured": config()["checkpoint"],
            "consistency_enabled": config().get("consistency_lora", False),
            "delta_matte_enabled": config().get("delta_matte_skin", True),
            "krea2_abliterated_available": prompt_models.krea2_abliterated_available(),
        }
    except Exception as exc:
        return {
            "ready": False if probe else None,
            "checkpoints": [
                p.relative_to(model_directory(ROOT) / "checkpoints").as_posix()
                for p in (model_directory(ROOT) / "checkpoints").rglob("*.safetensors")
            ] + prompt_models.installed(),
            "loras": [
                p.relative_to(model_directory(ROOT) / "loras").as_posix()
                for p in (model_directory(ROOT) / "loras").rglob("*.safetensors")
                if in_folder(p.relative_to(model_directory(ROOT) / "loras").as_posix())
            ],
            "configured": config()["checkpoint"],
            "consistency_enabled": config().get("consistency_lora", False),
            "delta_matte_enabled": config().get("delta_matte_skin", True),
            "krea2_abliterated_available": prompt_models.krea2_abliterated_available(),
            "error": str(exc) if probe else None,
            "lora_path_aliases": lora_path_aliases(),
            "lora_directory": directory(checkpoint or config()["checkpoint"]),
        }


def lora_labels():
    path = DATA / "lora-labels.json"
    labels = e.read(path) if path.exists() else {}
    return {lora_file_key(name): label for name, label in labels.items()}


def save_lora_labels(items):
    with LOCK:
        return _save_lora_labels(items)


def _save_lora_labels(items):
    available = {lora_file_key(name) for name in discover().get("loras", [])}
    e.need(isinstance(items, list) and len(items) <= 10000, "无效 LoRA 名单")
    labels = lora_labels()
    from .characters import CharacterLibrary
    cards = {c["id"]: c for c in CharacterLibrary().listing()}
    for item in items:
        file = lora_file_key(item["file"])
        e.need(file in available, "LoRA 文件未找到")
        name = str(item.get("name", "")).strip()
        e.need(0 < len(name) <= 80, "请为 LoRA 填写 1–80 字符的名字")
        e.need(
            isinstance(item.get("weight"), (float, int)) and 0 <= item["weight"] <= 2,
            "LoRA 权重须为 0–2",
        )
        owner = item.get("character_id", labels.get(file, {}).get("character_id"))
        e.need(owner is None or owner == "" or owner in cards, "关联角色已不存在，请重新选择")
        labels[file] = {
            **labels.get(file, {}),
            **({"character_id": owner} if owner is not None else {}),
            "name": name,
            "character": cards[owner]["name"] if owner else str(item.get("character", "")),
            "trigger_words": str(item.get("trigger_words", "")),
            "weight": item["weight"],
            "checkpoint": str(item.get("checkpoint", "")),
            "confirmed": item.get("confirmed") is True,
        }
    with LOCK:
        path = DATA / "lora-labels.json"
        if path.exists():
            e.save(DATA / "lora-label-history" / (e.stamp() + ".json"), e.read(path))
        e.save(path, labels)
    return labels


def graph(checkpoint, positive, negative, seed, cfg, loras=None):
    from .prompt_models import MODELS, graph as diffusion_graph
    if checkpoint in MODELS:
        return diffusion_graph(checkpoint, positive, negative, seed, cfg, loras)
    result = {
        "1": prompt_models.base_loader(checkpoint),
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": positive, "clip": ["1", 1]},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative, "clip": ["1", 1]},
        },
        "4": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": cfg["width"], "height": cfg["height"], "batch_size": 1},
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": cfg["steps"],
                "cfg": cfg["cfg"],
                "sampler_name": cfg["sampler_name"],
                "scheduler": cfg["scheduler"],
                "denoise": 1.0,
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
            },
        },
        "6": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["5", 0], "vae": ["1", 2]},
        },
        "7": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "comic-studio/panel", "images": ["6", 0]},
        },
    }
    model_ref = ["1", 0]
    clip_ref = ["1", 1]
    for i, lora in enumerate(loras or [], 8):
        key = str(i)
        result[key] = {
            "class_type": "LoraLoader",
            "inputs": {
                "lora_name": lora["file"],
                "strength_model": lora["weight"],
                "strength_clip": lora["clip_weight"],
                "model": model_ref,
                "clip": clip_ref,
            },
        }
        model_ref = [key, 0]
        clip_ref = [key, 1]
    result["2"]["inputs"]["clip"] = clip_ref
    result["3"]["inputs"]["clip"] = clip_ref
    result["5"]["inputs"]["model"] = model_ref
    return result


class ComfyManager:
    def __init__(self, *, keep_alive=True, session_dir=None):
        self.process = None
        self.log = None
        self.job = None
        self.keep_alive = keep_alive
        self.last_used = 0.0
        self.session_file = (Path(session_dir) if session_dir is not None else DATA) / "comfy-session.json"
        if self.keep_alive and self.session_file.is_file():
            self.attach_existing()

    def is_running(self):
        return self.process is not None and self.process.poll() is None

    def save_session(self):
        process = self.process
        pid = getattr(process, "pid", None)
        if not self.is_running() or not isinstance(pid, int) or pid <= 0:
            return
        cfg = config()
        port = urllib.parse.urlparse(cfg["endpoint"]).port
        e.save(
            self.session_file,
            {
                "pid": pid,
                "port": port,
                "user_directory": str(DATA / "comfy-user"),
                "preloaded_checkpoint": getattr(process, "preloaded_checkpoint", None),
            },
        )

    def clear_session(self):
        try:
            self.session_file.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _matches_own_marker(stats, marker):
        argv = (stats or {}).get("system", {}).get("argv", [])
        return (
            "--user-directory" in argv
            and argv[argv.index("--user-directory") + 1 : argv.index("--user-directory") + 2] == [marker]
        )

    def _validate_running_service(self, cfg):
        stats = api("/system_stats")
        e.need(
            any(d.get("type") == "cuda" for d in stats.get("devices", [])),
            "绘图服务未使用 CUDA 显卡，禁止回退到 CPU",
        )
        argv = stats.get("system", {}).get("argv", [])
        if cfg.get("memory_mode") == "auto_cuda":
            managed_preload = self.process is not None and "--disable-dynamic-vram" in argv
            matching_memory = (
                "--reserve-vram" in argv
                and ("--disable-smart-memory" in argv or managed_preload)
                and not any(flag in argv for flag in ("--cpu", "--gpu-only", "--highvram"))
            )
        else:
            matching_memory = (
                ("--highvram" in argv)
                if cfg.get("memory_mode") == "staged_cuda"
                else ("--gpu-only" in argv)
            )
        e.need(
            matching_memory,
            "现有绘图服务的显存模式与配置不一致，请重启绘图服务；推理必须使用 CUDA",
        )
        self.last_used = time.monotonic()

    def _adopt_orphaned_own_service(self, cfg, port):
        """Re-attach to this copy's own running ComfyUI if session file was lost or stale."""
        global _private_process, _verified_process
        marker = str(DATA / "comfy-user")
        try:
            stats = request_json(cfg["endpoint"] + "/system_stats", timeout=2.0)
        except Exception:
            return False
        if cfg.get("private_service") and not self._matches_own_marker(stats, marker):
            return False
        pid = find_listening_pid(port)
        if not pid or not is_pid_running(pid):
            return False
        attached = AttachedProcess(pid, preloaded_checkpoint=None)
        self.process = attached
        _private_process = attached
        _verified_process = attached
        self.last_used = time.monotonic()
        if self.keep_alive:
            self.save_session()
        return True

    def attach_existing(self):
        global _private_process, _verified_process
        if self.is_running():
            return True
        if not self.session_file.is_file():
            return False
        pid = 0
        try:
            saved = e.read(self.session_file)
            pid = int(saved.get("pid") or 0)
            cfg = config()
            port = urllib.parse.urlparse(cfg["endpoint"]).port
            marker = str(DATA / "comfy-user")
            if (
                pid <= 0
                or saved.get("port") != port
                or saved.get("user_directory") != marker
                or not is_pid_running(pid)
            ):
                self.clear_session()
                return False
            stats = request_json(cfg["endpoint"] + "/system_stats", timeout=1.5)
            if cfg.get("private_service") and not self._matches_own_marker(stats, marker):
                self.clear_session()
                return False
            attached = AttachedProcess(pid, preloaded_checkpoint=saved.get("preloaded_checkpoint"))
            self.process = attached
            _private_process = attached
            _verified_process = attached
            self.last_used = time.monotonic()
            return True
        except Exception:
            # Keep session file if the recorded PID is still alive (e.g. transient busy timeout).
            if not (pid > 0 and is_pid_running(pid)):
                self.clear_session()
            return False

    def start(self, runtime_options=None):
        global _private_process
        if self.process is None and self.keep_alive and self.session_file.is_file():
            self.attach_existing()
        if runtime_options and (self.preloaded or self.is_running()):
            self.shutdown()  # A specialized drawing may require a different node whitelist or flags.
        cfg = {**config(), **(runtime_options or {})}
        if discover()["ready"]:
            self._validate_running_service(cfg)
            return
        port = urllib.parse.urlparse(cfg["endpoint"]).port
        if cfg.get("private_service"):
            e.need(self.process is None or self.process.poll() is not None,
                   "副本绘图服务尚未就绪，请稍后重试")
            with socket.socket() as probe:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                try:
                    probe.bind(("127.0.0.1", port))
                except OSError as exc:
                    if not runtime_options and self._adopt_orphaned_own_service(cfg, port):
                        self._validate_running_service(cfg)
                        return
                    raise ValueError("副本绘图端口已被占用，已保留原有服务") from exc
        source = local_path(cfg["source_directory"])
        python = local_path(cfg["python_path"])
        e.need(
            python.exists() and (source / "main.py").exists(),
            "ComfyUI 程序或环境缺失，请运行 04-setup-comfyui.bat",
        )
        (DATA / "logs").mkdir(parents=True, exist_ok=True)
        (DATA / "comfy-input").mkdir(parents=True, exist_ok=True)
        self.log = (DATA / "logs/comfyui.log").open("a", encoding="utf-8")
        # Written at launch so moving the checkout does not leave stale absolute model paths.
        (ROOT / "config/comfy-model-paths.yaml").write_text(
            "comic_studio:\n  base_path: "
            + json.dumps(str(model_directory(ROOT)))
            + "\n  checkpoints: checkpoints\n  loras: loras\n  clip_vision: clip_vision\n  ipadapter: ipadapter\n  controlnet: controlnet\n  upscale_models: upscale_models\n  inpaint: inpaint\n"
            + "  diffusion_models: diffusion_models\n  text_encoders: text_encoders\n  vae: vae\n"
            + "comic_nodes:\n  base_path: "
            + json.dumps(str(ROOT / "runtime"))
            + "\n  custom_nodes: custom_nodes\n",
            encoding="utf-8",
        )
        if cfg.get('diagnostic_loras_directory'):
            diagnostic = Path(cfg['diagnostic_loras_directory']).resolve()
            e.need(diagnostic.is_dir() and diagnostic.is_relative_to((DATA/'comfy-lora-diagnostic').resolve()),
                   '隔离检验权重目录无效')
            with (ROOT/'config/comfy-model-paths.yaml').open('a', encoding='utf-8') as paths:
                paths.write('diagnostic_loras:\n  base_path: '+json.dumps(str(diagnostic))+'\n  loras: .\n')
        memory_flags = ["--highvram" if cfg.get("memory_mode") == "staged_cuda" else "--gpu-only"]
        if cfg.get("memory_mode") == "auto_cuda":
            memory_flags = ["--reserve-vram", "2"]
            if not cfg.get("preload_weights"):
                memory_flags.append("--disable-smart-memory")
        args = [
            str(python),
            str(source / "main.py"),
            "--listen",
            "127.0.0.1",
            "--port",
            str(port),
            "--cuda-device",
            "0",
            *(["--disable-dynamic-vram"] if cfg.get("preload_weights") else []),
            *memory_flags,
            "--disable-all-custom-nodes",
            "--whitelist-custom-nodes",
            "local_draw_warmup",
            "comfyui-anima-3-8B",
            "ComfyUI-Anima-3.8B-loraPatch",
            "ComfyUI-Anima-Delta-Q35-LoRA",
            "ComfyUI-Cosmos-Reference",
            "--disable-api-nodes",
            "--input-directory",
            str(DATA / "comfy-input"),
            "--extra-model-paths-config",
            str(ROOT / "config/comfy-model-paths.yaml"),
        ]
        if cfg.get("private_service"):
            user = DATA / "comfy-user"
            user.mkdir(parents=True, exist_ok=True)
            args += ["--user-directory", str(user)]
        storage = cfg.get('unet_storage_dtype','fp16')
        e.need(storage in ('fp16','fp8_e4m3fn'),'绘图模型的显存存储设置无效')
        if storage=='fp8_e4m3fn': args.append('--fp8_e4m3fn-unet')
        e.save(DATA / "logs/comfy-command.json", args)
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if self.keep_alive:
            creation_flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        self.process = subprocess.Popen(
            args,
            cwd=source,
            stdout=self.log,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags,
        )
        _private_process = self.process
        self.job = None if self.keep_alive else attach_exit_job(self.process)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            e.need(
                self.process.poll() is None,
                "ComfyUI 启动失败，详情见 data/logs/comfyui.log",
            )
            if discover()["ready"]:
                self.last_used = time.monotonic()
                if self.keep_alive:
                    self.save_session()
                return
            time.sleep(1)
        self.shutdown()
        raise ValueError("ComfyUI 启动超时")

    def stop(self):
        self.last_used = time.monotonic()
        if not self.keep_alive and not self.preloaded:
            self.shutdown()

    @property
    def preloaded(self):
        process = self.process
        if process is not None and process.poll() is None:
            return getattr(process, "preloaded_checkpoint", None)
        return None

    def detach(self):
        """Release local handles while leaving the ComfyUI server alive across restarts."""
        global _private_process, _verified_process
        if self.is_running():
            self.save_session()
        if self.log:
            try:
                self.log.close()
            except OSError:
                pass
            self.log = None
        if _private_process is self.process:
            _private_process = None
        if _verified_process is self.process:
            _verified_process = None
        self.process = None

    def shutdown(self):
        global _private_process, _verified_process
        proc = self.process
        terminate_tree(proc)
        close_job(self.job)
        self.job = None
        if self.log:
            try:
                self.log.close()
            except OSError:
                pass
            self.log = None
        self.clear_session()
        if _private_process is proc:
            _private_process = None
        if _verified_process is proc:
            _verified_process = None
        self.process = None


def drawing_state(repo, pid):
    path = repo.folder(pid) / "images/index.json"
    with LOCK:
        return e.read(path) if path.exists() else {"selections": {}, "batches": []}


def run_batch(
    repo,
    pid,
    target,
    update,
    checkpoint=None,
    count=3,
    mode="seeds",
    loras=None,
    candidate_indices=None,
    repairs=None,
    review_required=False,
    render_options=None,
    prompt_override=None,
    board_sample=None,
):
    from .task_control import checkpoint as task_checkpoint, TaskCancelled
    task_checkpoint()
    e.need(isinstance(count, int) and 1 <= count <= 16, "每格候选数须为 1–16")
    e.need(mode in ["seeds", "compositions", "angles"], "未知候选模式")
    p = repo.get(pid)
    panel = next(
        (
            x
            for x in p["pages"].get(target.split("-")[0], {}).get("panels", [])
            if x["id"] == target
        ),
        None,
    )
    e.need(panel, "请先确认文字分镜")
    storyboard_hash = e.digest(panel)
    prompt = p.get("drawing_prompts", {}).get(target)
    if board_sample is not None:
        e.need(p.get("prompt_board") and board_sample["board_target"] == target
               and board_sample["board_panel_hash"] == storyboard_hash, "这格分镜已更改，请重新提交")
        p = board_sample
        panel = p["pages"]["P001"]["panels"][0]
        prompt = p["drawing_prompts"]["P001-K01"]
    if prompt_override is not None:
        from .character_binding import identity_hash
        e.need((render_options or {}).get("external_prompt") is True and mode == "seeds", "导入提示词只支持按原文出图")
        e.need(prompt_override.get("panel_id") == target and prompt_override.get("panel_hash") == e.digest(panel), "导入提示词与当前画格不符")
        e.need(all(isinstance(prompt_override.get(k), str) and len(prompt_override[k]) <= 20000 for k in ("positive", "negative"))
               and prompt_override["positive"].strip(), "导入提示词文本无效")
        prompt = {"panel_hash": e.digest(panel), "identity_hash": identity_hash(p, panel),
                  "payload": {"positive": prompt_override["positive"], "negative": prompt_override["negative"], "character_anchors": []}}
        # Preserve imported text while loading the job's explicit LoRA snapshot.
        render_options = {**render_options, "direct_prompt": True, "reference": False, "control": False, "hand_detail": False}
    e.need(
        prompt and prompt["panel_hash"] == e.digest(panel),
        "请先生成并确认此格的绘图提示词；旧分镜的提示词不可用于新格",
    )
    from .character_binding import identity_hash, with_character_anchors

    e.need(
        prompt.get("identity_hash") == identity_hash(p, panel),
        "角色绑定已更新，请重新生成此格绘图描述；旧图和选图保持不变",
    )
    cfg = config()
    from . import drawing_settings, lora_binding

    prefs = drawing_settings.load(repo, pid)
    indices = candidate_indices if candidate_indices is not None else list(range(count))
    e.need(
        len(indices) == count
        and len(set(indices)) == count
        and all(isinstance(i, int) and 0 <= i < 16 for i in indices),
        "无效候选视角编号",
    )
    if mode == "angles":
        variants = prompt["payload"]["composition_variants"]
        e.need(
            len(variants) == 6 and len(set(variants)) == 6,
            "此格尚无六种独立视角描述，请先生成新的绘图适配候选",
        )
    checkpoint = checkpoint or cfg["checkpoint"]
    from .prompt_models import MODELS, defaults
    using_anima = checkpoint in MODELS
    cfg["transient"] = bool(p.get("transient"))
    if using_anima or (render_options or {}).get("direct_prompt"):
        cfg.update(defaults(checkpoint))
    if "delta_matte_skin" in (render_options or {}):
        e.need(type(render_options["delta_matte_skin"]) is bool, "Invalid matte skin setting")
        cfg["delta_matte_skin"] = render_options["delta_matte_skin"]
    if "krea2_abliterated_encoder" in (render_options or {}):
        e.need(type(render_options["krea2_abliterated_encoder"]) is bool, "Invalid abliterated encoder setting")
        cfg["krea2_abliterated_encoder"] = render_options["krea2_abliterated_encoder"]
    if (render_options or {}).get("direct_prompt"):
        cfg["literal_prompt"] = True
        if MODELS.get(checkpoint, (None, None))[1] == "krea2" and prompt["payload"]["negative"].strip():
            # Turbo normally runs without CFG. Explicit negative text opts into mild guidance.
            cfg["cfg"] = 1.2
    if prompt_override is not None:
        cfg["literal_prompt"] = True
    if MODELS.get(checkpoint, (None, None))[1] == 'qwen_edit':
        from .qwen_edit import validate_frozen, freeze_assets, RECIPE
        cfg['edit_references'] = validate_frozen((render_options or {}).get('edit_references'))
        e.need((render_options or {}).get('edit_recipe') == RECIPE, 'Qwen 编辑方案已变化，请重新预检，不能改变旧轮次')
        cfg['edit_models'] = freeze_assets()
        e.need(cfg['edit_models'] == (render_options or {}).get('edit_models'), 'Qwen 编辑模型与本轮冻结记录不符')
        e.need(not loras, 'Qwen 编辑试验不加载其他模型的 LoRA')
    if (render_options or {}).get("steps") is not None:
        steps = render_options["steps"]
        e.need(type(steps) is int and 1 <= steps <= 100, "步数须为 1–100")
        cfg["steps"] = steps
    from .image_size import dimensions
    size_options = {**(prefs or {}), **(p.get("prompt_input", {}) if p.get("quick_sample") else {}),
                    **(render_options or {})}
    ratio = size_options.get("aspect_ratio", "storyboard")
    if ratio == "storyboard":
        ratio = panel["aspect_ratio"]
    cfg["width"], cfg["height"] = dimensions(ratio, size_options.get("resolution", "default"),
                                            alignment=16 if MODELS.get(checkpoint, (None, None))[1] == "moss_image" else 8)
    status = discover()
    e.need(status["ready"], "ComfyUI 尚未启动")
    lora_files = status.get("lora_files") or {
        lora_file_key(name): name for name in status.get("loras", [])
    }
    e.need(checkpoint in status["checkpoints"], "选定绘图权重未安装，请在模型下载中安装后再试")
    loras = (copy.deepcopy(loras or [])
             if prompt_override is not None or (render_options or {}).get("direct_prompt")
             else lora_binding.route(loras or [], p, panel, lora_labels(), prefs))
    e.need(
        isinstance(loras, list) and len(loras) <= lora_binding.MAX_LORAS, "最多同时使用五个已核对底模的 LoRA"
    )
    allow_experimental = bool((render_options or {}).get("direct_prompt") or prompt_override is not None)
    for lora in loras:
        from .lora_folders import matches
        lora["file"] = lora_file_key(lora.get("file", ""))
        e.need(matches(lora["file"], checkpoint), "LoRA 不在当前模型对应的文件夹中")
        structural_match = False
        local_lora = (model_directory(ROOT) / "loras" / lora.get("file", "")).resolve()
        lora_root = (model_directory(ROOT) / "loras").resolve()
        if local_lora.is_relative_to(lora_root) and local_lora.is_file():
            from .lora_inspection import inspect_lora

            inspection = inspect_lora(local_lora, checkpoint=checkpoint)
            structural_match = inspection["structurally_compatible"] is True
            e.need(
                inspection["structurally_compatible"] is not False,
                "LoRA "
                + lora["file"]
                + " 与当前绘图底模不兼容："
                + inspection["family"],
            )
        e.need(
            lora.get("file") in lora_files,
            "LoRA 文件不在 ComfyUI 可用列表中",
        )
        e.need(
            (lora.get("compatible_checkpoint") == checkpoint
             and lora.get("confirmed_compatibility") is True)
            or (allow_experimental and structural_match
                and lora.get("experimental_checkpoint") == checkpoint),
            "请先根据 LoRA 资源说明确认适配当前底模",
        )
        e.need(
            all(
                isinstance(lora.get(k), (float, int)) and 0 <= lora[k] <= 2
                for k in ["weight", "clip_weight"]
            ),
            "LoRA 权重须为 0–2",
        )
        e.need(isinstance(lora.get("trigger_words", ""), str), "触发词须为文本")
    bid = uuid.uuid4().hex
    from .framing import apply_camera

    options = {
        **({"consistency_lora": prefs["consistency_lora"]} if prefs and "consistency_lora" in prefs else {}),
        **(render_options or {}),
    }
    from . import anima_consistency
    from .characters import CharacterLibrary
    if (MODELS.get(checkpoint, (None, None))[1] != 'qwen_edit'
            and (prefs or {}).get("reference_character_id") and not options.get("external_prompt")
            and not options.get("diagnostic")
            and not options.get("consistency_reference") and options.get("consistency_lora", True)):
        selected = anima_consistency.selected_character_reference(p, panel, checkpoint, prefs,
            library=CharacterLibrary(repo.root.parent / "characters"))
        if selected:
            options["consistency_reference"] = anima_consistency.freeze_character_reference(selected)
    consistency = anima_consistency.prepare(p, panel, checkpoint, cfg, options, lora_files,
        library=lambda: CharacterLibrary(repo.root.parent / "characters"))
    if consistency["enabled"]:
        if consistency.get("reference"):
            e.need("ApplyCosmosReferenceLatent" in api("/object_info/ApplyCosmosReferenceLatent"),
                   "一致性参考节点未加载，请重启绘图服务")
        update({"message": "自动加载 Anima Edit v2" + (
            "，使用固定参考图保持外观" if consistency.get("reference") else "，本次无图像参考")})
    folder = repo.folder(pid) / "images" / bid
    folder.mkdir(parents=True)
    batch = {
        "id": bid,
        "panel_id": target,
        "panel_hash": storyboard_hash,
        "prompt_hash": e.digest(prompt),
        "identity_hash": identity_hash(p, panel),
        "checkpoint": checkpoint,
        "created": e.stamp(),
        "status": "running",
        "images": [],
        "source": "ComfyUI 模型生成",
        "settings": cfg,
        "count": count,
        "candidate_indices": indices,
        "mode": mode,
        "loras": loras,
        "review_required": review_required,
        "consistency": consistency,
        "render_options": options,
    }

    def persist():
        with LOCK:
            index = drawing_state(repo, pid)
            index["batches"] = [b for b in index["batches"] if b["id"] != bid] + [
                copy.deepcopy(batch)
            ]
            e.save(repo.folder(pid) / "images/index.json", index)

    persist()
    watcher = None
    job = None
    record = None
    try:
        for i in range(count):
            from .task_control import checkpoint as task_checkpoint
            task_checkpoint()
            update(
                {
                    "message": f"绘制 {target} 候选 {i + 1}/{count}",
                    "progress": {
                        "completed": i,
                        "total": count,
                        "unit": "张（本轮绘制）",
                    },
                }
            )
            seed = options.get("seed", secrets.randbelow(2**53))
            positive = (
                prompt["payload"]["positive"]
                if mode == "seeds"
                else prompt["payload"]["composition_variants"][
                    indices[i] % len(prompt["payload"]["composition_variants"])
                ]
            )
            if repairs and repairs.get(indices[i]):
                positive += ", " + repairs[indices[i]]
            if options.get("direct_prompt"):
                # Dedicated raw-prompt entry: no automatic camera/quality/identity words.
                negative = prompt["payload"]["negative"]
                batch["prompt_adapter"] = {"id": "user_raw", "checkpoint": checkpoint}
            else:
                positive = with_character_anchors(
                    positive, prompt["payload"]["character_anchors"]
                )
                positive, negative = apply_camera(
                    positive, prompt["payload"]["negative"], prompt["payload"]
                )
                from .prompt_adapter import compile_prompt

                positive, negative, dialect = compile_prompt(positive, negative, checkpoint)
                batch["prompt_adapter"] = dialect
                batch["visual_plan"] = copy.deepcopy(prompt["payload"].get("visual_plan"))
            user_positive = positive
            if loras and not options.get("external_prompt") and not options.get("diagnostic"):
                from .lora_inspection import apply_lora_triggers

                positive = apply_lora_triggers(positive, loras)
            workflow = graph(checkpoint, positive, negative, seed, cfg, loras)
            from .delta_style import CHECKPOINT as DELTA_CHECKPOINT
            if checkpoint == DELTA_CHECKPOINT:
                positive = workflow["2"]["inputs"]["text"]
                negative = workflow["3"]["inputs"]["text"]
                batch["delta_matte_skin"] = cfg.get("delta_matte_skin", True)
            anima_consistency.apply(workflow, consistency, cfg)
            # ComfyUI validates enum values exactly, including Windows separators.
            for node in workflow.values():
                if node["class_type"] in ('LoraLoader', 'DeltaMixQ35LoraLoader'):
                    inputs = node["inputs"]
                    inputs["lora_name"] = lora_files[lora_file_key(inputs["lora_name"])]
            if p.get("transient"):
                for node in workflow.values():
                    if node["class_type"] == "SaveImage":
                        node["class_type"] = "PreviewImage"
                        node["inputs"].pop("filename_prefix", None)
            e.save(folder / f"workflow-{i + 1}.json", workflow)
            if using_anima and "80" in workflow:
                batch["action_conditioning"] = {
                    "mode": "reclining_kick", "layout_end": 0.55,
                    "layout_positive": workflow["80"]["inputs"]["prompt"],
                    "detail_positive": workflow["2"]["inputs"]["prompt"],
                    "same_sampling_trajectory": True,
                }
            if watcher:
                watcher.close()
            watcher = ComfyProgress(cfg["endpoint"], bid)
            emit(
                update,
                "提交绘图任务",
                "ComfyUI",
                detail=f"{target} · 候选 {i + 1}/{count}",
            )
            job, record = None, None
            batch["submission_pending"] = True
            batch["active_candidate"] = {
                "id": f"{bid}-{i + 1}",
                "file": f"{bid}/{i + 1}.png",
                "seed": seed,
                "view_index": indices[i] + 1,
                "positive": positive,
                "user_positive": user_positive,
                "negative": negative,
                "review": {"verdict": "pending" if review_required else "unreviewed"},
            }
            persist()
            if _private_process and getattr(_private_process, "preloaded_checkpoint", None) != checkpoint:
                _private_process.preloaded_checkpoint = None
                session_path = DATA / "comfy-session.json"
                if session_path.is_file():
                    try:
                        sess = e.read(session_path)
                        if sess.get("pid") == getattr(_private_process, "pid", None) and sess.get("preloaded_checkpoint") is not None:
                            sess["preloaded_checkpoint"] = None
                            e.save(session_path, sess)
                    except Exception:
                        pass
            response = api("/prompt", {"prompt": workflow, "client_id": bid})
            batch["submission_pending"] = False
            e.need(
                not response.get("node_errors"),
                "ComfyUI 节点校验失败：" + e.encoded(response.get("node_errors")),
            )
            job = response["prompt_id"]
            batch["submission_pending"] = False
            batch["active_prompt"] = job
            persist()
            e.save(folder / f"job-{i + 1}.json", response)
            deadline = time.monotonic() + cfg["timeout_seconds"]
            record = None
            last_history = 0
            trace_path = folder / f"progress-{i + 1}.jsonl"
            while time.monotonic() < deadline:
                event = watcher.poll(job, workflow)
                if event:
                    update({"runtime": event})
                    with trace_path.open("a", encoding="utf-8") as trace:
                        trace.write(json.dumps(event, ensure_ascii=False) + "\n")
                if time.monotonic() - last_history >= 1:
                    last_history = time.monotonic()
                    record = api("/history/" + job).get(job)
                    if record:
                        break
            e.need(
                record,
                "ComfyUI 超时；任务编号已保留。不要重复提交，请在 ComfyUI 检查队列",
            )
            e.save(folder / f"history-{i + 1}.json", record)
            e.need(
                record.get("status", {}).get("status_str") != "error",
                "ComfyUI 执行失败，原始报告已保留",
            )
            images = [
                image
                for out in record.get("outputs", {}).values()
                for image in out.get("images", [])
            ]
            e.need(images, "ComfyUI 未返回图片")
            emit(
                update,
                "读取并保存图片",
                "ComfyUI 历史结果",
                detail=f"{target} · 候选 {i + 1}/{count}",
            )
            query = urllib.parse.urlencode(images[0])
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(cfg["endpoint"] + "/view?" + query, timeout=30) as r:
                content = r.read(40_000_001)
            e.need(len(content) <= 40_000_000, "返回图片过大")
            name = f"{i + 1}.png"
            (folder / name).write_bytes(content)
            from PIL import Image

            with Image.open(folder / name) as img:
                img.verify()
            if p.get("transient"):
                # Downloaded images have no embedded workflow or prompt metadata.
                with Image.open(folder / name) as img:
                    clean = img.copy()
                    clean.info.clear()
                clean.save(folder / name)
                cleanup_prompt_output(job, record)
            batch["images"].append(
                {
                    "id": f"{bid}-{i + 1}",
                    "file": f"{bid}/{name}",
                    "seed": seed,
                    "prompt_id": job,
                    "view_index": indices[i] + 1,
                    "positive": positive,
                    "user_positive": user_positive,
                    "negative": negative,
                    "review": {"verdict": "pending"}
                    if review_required
                    else {"verdict": "unreviewed"},
                }
            )
            persist()
            update(
                {
                    "message": f"已保存候选 {i + 1}/{count}",
                    "progress": {
                        "completed": i + 1,
                        "total": count,
                        "unit": "张（本轮绘制）",
                    },
                }
            )
        batch["status"] = "done"
        persist()
        return batch
    except TaskCancelled:
        batch.update(status="cancelled", submission_pending=False)
        persist()
        raise
    except Exception as exc:
        # A definite HTTP rejection did not enqueue work. Connection loss remains ambiguous.
        cause = exc.__cause__
        if (
            batch.get("submission_pending")
            and isinstance(cause, urllib.error.HTTPError)
            and 400 <= cause.code < 500
        ):
            batch["submission_pending"] = False
        batch.update(
            status="uncertain"
            if (batch.get("submission_pending") or (job and not record))
            else "failed",
            error=str(exc),
        )
        persist()
        raise
    finally:
        if p.get("transient") and job and record:
            cleanup_prompt_output(job, record)
        if watcher:
            watcher.close()


def cleanup_prompt_output(job, record):
    import logging
    try:
        stats = api("/system_stats")
        argv = stats.get("system", {}).get("argv", [])
        temp_root = local_path(config()["source_directory"]) / "temp"
        if "--temp-directory" in argv:
            temp_root = Path(argv[argv.index("--temp-directory") + 1])
        temp_root = temp_root.resolve()
        for output in record.get("outputs", {}).values():
            for image in output.get("images", []):
                if image.get("type") != "temp":
                    continue
                path = (temp_root / image.get("subfolder", "") / image["filename"]).resolve()
                if path.is_relative_to(temp_root) and path.suffix.lower() == ".png":
                    path.unlink(missing_ok=True)
        api("/history", {"delete": [job]})
    except (OSError, ValueError, KeyError, IndexError) as exc:
        logging.warning("Temporary ComfyUI cleanup failed: %s", exc)


def select_image(repo, pid, target, image_id, allow_rejected=False, allow_unreviewed=False):
    with LOCK:
        p = repo.get(pid)
        panel = next(
            x for x in p["pages"][target.split("-")[0]]["panels"] if x["id"] == target
        )
        index = drawing_state(repo, pid)
        found = [
            (b, i)
            for b in index["batches"]
            if b["panel_id"] == target
            for i in b["images"]
            if i["id"] == image_id
        ]
        e.need(len(found) == 1, "候选图片不存在")
        batch, image = found[0]
        verdict = image.get("review", {}).get("verdict")
        e.need(verdict != "pending" or allow_unreviewed is True, "此图尚未完成自动审核，请明确选择未审核图片")
        e.need(
            verdict != "reject" or allow_rejected is True,
            "请从下方失败图区域手动选择此图",
        )
        e.need(batch["panel_hash"] == e.digest(panel), "图片来自旧版分镜，请重新绘制")
        e.save(repo.folder(pid) / "images/history" / (e.stamp() + ".json"), index)
        index["selections"][target] = {
            "image": image,
            "panel_hash": batch["panel_hash"],
            "batch": batch["id"],
            "manual_override": verdict == "reject",
            "manual_unreviewed": verdict == "pending",
        }
        e.save(repo.folder(pid) / "images/index.json", index)
        return index
