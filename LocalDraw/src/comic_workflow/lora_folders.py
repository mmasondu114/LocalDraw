"""Model-specific LoRA directories, with support for pre-migration references."""

def is_anima(checkpoint):
    from .prompt_models import MODELS
    return MODELS.get(checkpoint, (None, None))[1] in {"anima38", "anima", "delta"}


def directory(checkpoint):
    from .prompt_models import MODELS
    family = MODELS.get(checkpoint, (None, None))[1]
    return family if family in {"krea2", "moss_image", "qwen_edit"} else "anime" if is_anima(checkpoint) else "sdxl"


def matches(filename, checkpoint):
    first = filename.replace("\\", "/").split("/", 1)[0]
    if first in {"anime", "sdxl", "krea2", "moss_image", "qwen_edit"}:
        return first == directory(checkpoint)
    # Existing installations can resolve their old paths before being migrated.
    return directory(checkpoint) not in {"krea2", "moss_image", "qwen_edit"} and (first == "Anima") == is_anima(checkpoint)


def open_folder(checkpoint=None, folder=None, root=None):
    import os
    from . import engine as e
    from .installed_assets import models
    from .paths import ROOT

    base = (models(root or ROOT) / "loras").resolve()
    if folder and folder != "*":
        e.need(isinstance(folder, str), "无效 LoRA 目录")
        target = (base / folder).resolve()
        e.need(target == base or target.is_relative_to(base), "无效 LoRA 目录")
        e.need(target.is_dir(), "LoRA 目录不存在")
    elif checkpoint:
        e.need(isinstance(checkpoint, str), "无效模型名称")
        target = (base / directory(checkpoint)).resolve()
        e.need(target.is_relative_to(base), "无效 LoRA 目录")
        target.mkdir(parents=True, exist_ok=True)
    else:
        target = base
        target.mkdir(parents=True, exist_ok=True)
    os.startfile(str(target))
    return {"opened": str(target)}
