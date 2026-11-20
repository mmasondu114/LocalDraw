"""All runtime locations are relative to this checkout, never the old conversation."""

import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

ROOT = Path(
    os.environ.get("COMIC_STUDIO_ROOT", Path(__file__).resolve().parents[2])
).resolve()
DATA = ROOT / "data"
PROJECTS = DATA / "projects"
_prompt_model = ContextVar("prompt_model", default=False)


_vision_mode = ContextVar("vision_model", default=False)
ANALYSIS_MODELS = {"flash_next_64k": "Qwen3.8 Flash-Next · 64K",
                   "huihui_27b": "Huihui Qwen3.8 27B · 32K"}


def raw_settings():
    return json.loads((ROOT / "config/settings.json").read_text(encoding="utf-8-sig"))


def analysis_choice(cfg):
    path = DATA / "analysis-model.json"
    selected = json.loads(path.read_text(encoding="utf-8"))["model"] if path.is_file() else cfg["analysis_model"]
    if selected not in ANALYSIS_MODELS:
        raise ValueError("分析模型配置无效，请重新选择")
    return selected


def use_vision_model(vision):
    # Generation workers use a dedicated thread per job.
    _vision_mode.set(vision)


def settings():
    cfg = raw_settings()
    if "analysis_model" in cfg:
        if analysis_choice(cfg) == "flash_next_64k" and not _vision_mode.get():
            cfg.update(cfg["prompt_model"])
            cfg.update(cfg["page_redraw_model"])
            if cfg["context_size"] != 65536:
                raise ValueError("Flash-Next 分析配置要求 65536 上下文")
    elif _prompt_model.get():
        cfg.update(cfg.get("prompt_model", {}))
        if _prompt_model.get() == "page_redraw":
            cfg.update(cfg.get("page_redraw_model", {}))
    return cfg


@contextmanager
def prompt_model(profile=None):
    """Route an entire prompt-writing job, including startup and token counting."""
    token = _prompt_model.set(profile or True)
    try:
        yield
    finally:
        _prompt_model.reset(token)


def local_path(value):
    path = Path(value)
    return path if path.is_absolute() else ROOT / path
