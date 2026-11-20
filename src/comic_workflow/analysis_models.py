"""Local analysis profile selection and private access to this studio's workers."""
import secrets
from urllib.parse import urlsplit

from . import engine as e
from .paths import ANALYSIS_MODELS, DATA, analysis_choice, local_path, raw_settings


def profile_problem(cfg, model):
    if model == "huihui_27b":
        files = [(cfg["model_path"], cfg["model_size"]), (cfg["runtime_path"], None)]
    else:
        flash = cfg["prompt_model"]
        files = [(f["path"], f["size"]) for f in flash["model_files"]]
        files += [(flash[key], None) for key in ("strata_python", "strata_cli")]
        profile = local_path(cfg["page_redraw_model"]["strata_profile"])
        try:
            run = e.read(profile)
            for args in (run["args"], run.get("args_written", run["args"])):
                if args[args.index("--max-context") + 1] != "65536":
                    return "引擎配置的 --max-context 必须是 65536"
            for flag in ("--pack", "--expert-profile", "--mtp", "--native"):
                if flag in run["args"]:
                    asset = local_path(run["args"][run["args"].index(flag) + 1])
                    if not asset.exists():
                        return "引擎资源缺失：" + str(asset)
            for field in ("cwd", "tokenizer"):
                if not local_path(run[field]).is_dir():
                    return "引擎目录缺失：" + str(run[field])
            files.append((run["exe"], None))
        except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
            return "无法验证 64K 引擎配置：" + str(exc)
    for name, size in files:
        path = local_path(name)
        if not path.is_file() or (size is not None and path.stat().st_size != size):
            return "文件缺失或大小不符：" + str(path)
    return ""


def choices():
    cfg = raw_settings()
    rows = []
    for model, label in ANALYSIS_MODELS.items():
        if model == "flash_next_64k" and "prompt_model" not in cfg:
            continue
        if model == "huihui_27b":
            label = f"Huihui Qwen 27B · {cfg['context_size'] // 1024}K"
        problem = profile_problem(cfg, model)
        rows.append(dict(id=model, label=label, available=not problem, reason=problem,
                         context_size=65536 if model == "flash_next_64k" else cfg["context_size"]))
    return {"selected": analysis_choice(cfg), "options": rows}


def select_model(model, manager):
    from .model import GENERATION_LOCK
    e.need(isinstance(model, str) and model in ANALYSIS_MODELS, "无法识别选择的分析模型")
    e.need(GENERATION_LOCK.acquire(blocking=False), "当前任务正在处理，请等任务结束后选择模型")
    try:
        status = choices()
        if status["selected"] == model:
            return status
        problem = next(row["reason"] for row in status["options"] if row["id"] == model)
        e.need(not problem, problem)
        e.need(not manager.owns_live_worker(), "当前模型已加载，请先在工作室卸载，再选择模型")
        e.save(DATA / "analysis-model.json", {"model": model})
        return {**status, "selected": model}
    finally:
        GENERATION_LOCK.release()


# Keys live only in this process and the privately launched worker environment.
# A separate copy of the studio, a browser chat, and another local client cannot
# submit a generation to these endpoints, including in the final idle check.
_worker_keys = {}


def worker_key(port):
    key = secrets.token_urlsafe(32)
    _worker_keys[port] = key
    return key


def release_key(port):
    _worker_keys.pop(port, None)


def worker_headers(url):
    parsed = urlsplit(url)
    if parsed.scheme == "http" and parsed.hostname == "127.0.0.1":
        key = _worker_keys.get(parsed.port)
        if key:
            return {"Authorization": "Bearer " + key}
    return {}

