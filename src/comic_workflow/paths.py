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


def settings():
    cfg = json.loads((ROOT / "config/settings.json").read_text(encoding="utf-8-sig"))
    if _prompt_model.get():
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
